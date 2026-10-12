"""rag 域 agent 回调客户端(009):embedding / describe-table(ADR-002 方向:
LLM key 仅 agent 面持有,api 经内部令牌回调获取智能服务)。

HTTP 层错误一律收敛领域结果(embedding→EmbeddingError 降级;describe→
enriched=False 降级),不裸抛——辅助路失败不阻断主链路(上游同款纪律)。
"""

import hashlib
import json
import logging
from typing import cast

import httpx
from redis.asyncio import Redis

from kongming.config import get_settings
from kongming.rag.models import TableBlock, clean_alias
from kongming.rag.ports import EmbeddingError

log = logging.getLogger(__name__)

_EMB_CACHE_PREFIX = "km:emb:v1:"  # v1=embedding 模型版本;换模型必须 bump+评估维度迁移
_EMB_TTL_SEC = 24 * 3600


class AgentRagClient:
    """EmbeddingPort/TableDescriberPort 实现:httpx 回调 + Redis emb 缓存(fail-open)。

    AsyncClient 实例级 lazy 复用(013 挂账⑭):消每请求 TLS 握手——fetch-metadata
    500 表润色从 500 次建连降为共享一条 keep-alive 连接链。client 不 close,
    生命周期随实例(进程级单例见 get_agent_rag_client);httpx.AsyncClient
    设计为并发任务共享,无锁安全。
    """

    def __init__(self, redis: Redis | None = None) -> None:
        settings = get_settings()
        self._base = settings.agent_base_url.rstrip("/")
        self._token = settings.internal_token or ""
        self._timeout = 60.0  # embedding/润色单请求超时(批量 64 条富余)
        self._dim = settings.rag_embedding_dim  # 维度防线(R2-R1)
        self._redis = redis
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        """lazy 创建并复用;首建竞态无害(最多多建一个被 GC,无状态正确性问题)。

        进程单 loop 前提(R1-O1):连接池内连接绑定创建时事件循环——生产
        uvicorn 单 worker 单 loop 安全;测试若经本实例完成一次成功建连,
        后续用例换 loop 复用会 RuntimeError,须自行复位单例槽/_client。
        """
        if self._client is None:
            self._client = httpx.AsyncClient(
                trust_env=False,  # 出站受控:不采环境代理(SSRF 钉扎/内网回调语义)
                base_url=self._base,
                timeout=self._timeout,
                headers={"X-Internal-Token": self._token},
            )
        return self._client

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """批量向量化;emb 缓存逐条命中跳过(确定性映射,fail-open)。"""
        vectors: list[list[float] | None] = [None] * len(texts)
        keys = [_emb_key(t) for t in texts]
        cached = await self._mget(keys)
        missing: list[int] = []
        for i, hit in enumerate(cached):
            if hit is not None:
                try:
                    v = json.loads(hit)
                except ValueError:
                    missing.append(i)  # 坏缓存按未命中重取
                    continue
                # 维度漂移(换模型未 bump v1 前缀)按未命中重取,防插入报维度错;
                # 自有缓存回读(写入侧 json.dumps(list[list[float]])),此处仅收窄形态
                if isinstance(v, list) and len(cast("list[float]", v)) == self._dim:
                    vectors[i] = cast("list[float]", v)
                else:
                    missing.append(i)
            else:
                missing.append(i)
        if missing:
            batch = [texts[i] for i in missing]
            fetched = await self._embed_http(batch)
            await self._mset([keys[i] for i in missing], [fetched[j] for j in range(len(missing))])
            for j, i in enumerate(missing):
                vectors[i] = fetched[j]
        return [v if v is not None else [] for v in vectors]

    async def _embed_http(self, texts: list[str]) -> list[list[float]]:
        try:
            resp = await self._get_client().post("/internal/embedding", json={"texts": texts})
        except httpx.HTTPError as e:
            raise EmbeddingError(f"embedding 服务不可达: {e}") from e
        if resp.status_code != 200:
            raise EmbeddingError(f"embedding 服务拒绝(HTTP {resp.status_code})")
        try:
            data = resp.json()
        except ValueError as e:  # 200 但非 JSON(网关回 HTML 等)——同降级语义
            raise EmbeddingError(f"embedding 响应非 JSON: {e}") from e
        # agent 契约:vectors 为 list[list[float]];非数值元素由下方 float() 原样报错
        vectors = cast("list[list[float]]", data.get("vectors") or [])
        if len(vectors) != len(texts):
            raise EmbeddingError("embedding 返回数量与请求不符")
        out = [[float(x) for x in v] for v in vectors]
        if any(len(v) != self._dim for v in out):
            # 供应商侧换模型/维度漂移:降级(而非落库时报维度错 500)
            raise EmbeddingError(
                f"embedding 维度不符(期望 {self._dim},实际 {len(out[0]) if out else 0})"
            )
        return out

    async def describe(self, block: TableBlock, schema_name: str) -> tuple[bool, TableBlock]:
        """表结构润色;enriched=False 时返回原块(api 侧落库降级纯结构)。"""
        payload = {
            "table_name": block.table_name,
            "schema_name": schema_name,
            "table_comment": block.table_comment,
            "columns": [
                {"name": name, "type": desc if _looks_like_type(desc) else "", "comment": desc}
                for name, desc in block.columns_comment.items()
            ],
        }
        try:
            resp = await self._get_client().post("/internal/describe-table", json=payload)
        except httpx.HTTPError as e:
            log.warning("describe-table 回调异常: %s", e)
            return False, block
        if resp.status_code != 200:
            log.warning("describe-table 被拒(HTTP %s)", resp.status_code)
            return False, block
        try:
            data = resp.json()
        except ValueError:
            log.warning("describe-table 响应非 JSON,降级纯结构")
            return False, block
        if not data.get("enriched"):
            return False, block
        return True, TableBlock(
            table_name=block.table_name,
            table_comment=str(data.get("table_comment") or block.table_comment),
            columns_comment={
                str(k): str(v)
                for k, v in cast("dict[str, object]", data.get("columns_comment") or {}).items()
            },
            sample_queries=[str(s) for s in cast("list[object]", data.get("sample_queries") or [])],
            # 037:透传即清洗(库内值与消费值一致,审查循环 B6)
            alias=clean_alias(str(data.get("alias") or "")),
        )

    # ---- Redis emb 缓存(fail-open:异常按未命中/放弃写入处理) ----

    async def _mget(self, keys: list[str]) -> list[str | None]:
        if self._redis is None or not keys:
            return [None] * len(keys)
        try:
            raw: list[str | None] = list(await self._redis.mget(keys))  # type: ignore[arg-type]
            return raw
        except Exception:  # noqa: BLE001  # Redis 故障不阻断 embedding(fail-open)
            log.warning("emb 缓存读失败,按未命中处理", exc_info=True)
            return [None] * len(keys)

    async def _mset(self, keys: list[str], vectors: list[list[float]]) -> None:
        if self._redis is None or not keys:
            return
        try:
            pipe = self._redis.pipeline()
            for key, vec in zip(keys, vectors, strict=True):
                pipe.set(key, json.dumps(vec), ex=_EMB_TTL_SEC)
            await pipe.execute()
        except Exception:  # noqa: BLE001
            log.warning("emb 缓存写失败,跳过(不影响结果)", exc_info=True)


def _emb_key(text: str) -> str:
    return _EMB_CACHE_PREFIX + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _looks_like_type(desc: str) -> bool:
    """纯结构块的列说明=英文类型形态(int/varchar…)——回填 type 字段用。"""
    return bool(desc) and all(c.isascii() for c in desc.replace(" ", ""))


_singleton: AgentRagClient | None = None


def get_agent_rag_client(redis: Redis | None = None) -> AgentRagClient:
    """进程内共享单例(013 挂账⑭):组合根(chat/router、rag/router 的 Depends
    工厂)经此取实例,AsyncClient 连接池跨请求复用,消每请求 TLS 握手。
    redis 仅首建生效(get_redis 自身为单例工厂);单例无业务状态(仅配置+
    连接池+redis 句柄)。测试面(R1-S3 如实注记):TestClient 测试经组合根
    触达本工厂,但 embed/describe 类级补丁(test_rag_admin/test_rag_recall)
    或测试环境连接失败兜底使 _client 恒空池,跨 loop 复用无害;成功建连的
    新测试须先复位 _singleton(单 loop 前提见 _get_client)。
    """
    global _singleton
    if _singleton is None:
        _singleton = AgentRagClient(redis)
    return _singleton
