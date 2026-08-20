"""rag 域 agent 回调客户端(009):embedding / describe-table(ADR-002 方向:
LLM key 仅 agent 面持有,api 经内部令牌回调获取智能服务)。

HTTP 层错误一律收敛领域结果(embedding→EmbeddingError 降级;describe→
enriched=False 降级),不裸抛——辅助路失败不阻断主链路(上游同款纪律)。
"""

import hashlib
import json
import logging

import httpx
from redis.asyncio import Redis

from kongming.config import get_settings
from kongming.rag.models import TableBlock
from kongming.rag.ports import EmbeddingError

log = logging.getLogger(__name__)

_EMB_CACHE_PREFIX = "km:emb:v1:"  # v1=embedding 模型版本;换模型必须 bump+评估维度迁移
_EMB_TTL_SEC = 24 * 3600


class AgentRagClient:
    """EmbeddingPort/TableDescriberPort 实现:httpx 回调 + Redis emb 缓存(fail-open)。"""

    def __init__(self, redis: Redis | None = None) -> None:
        settings = get_settings()
        self._base = settings.agent_base_url.rstrip("/")
        self._token = settings.internal_token or ""
        self._timeout = 60.0  # embedding/润色单请求超时(批量 64 条富余)
        self._dim = settings.rag_embedding_dim  # 维度防线(R2-R1)
        self._redis = redis

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
                # 维度漂移(换模型未 bump v1 前缀)按未命中重取,防插入报维度错
                if isinstance(v, list) and len(v) == self._dim:
                    vectors[i] = v
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
            async with httpx.AsyncClient(
                base_url=self._base,
                timeout=self._timeout,
                headers={"X-Internal-Token": self._token},
            ) as client:
                resp = await client.post("/internal/embedding", json={"texts": texts})
        except httpx.HTTPError as e:
            raise EmbeddingError(f"embedding 服务不可达: {e}") from e
        if resp.status_code != 200:
            raise EmbeddingError(f"embedding 服务拒绝(HTTP {resp.status_code})")
        try:
            data = resp.json()
        except ValueError as e:  # 200 但非 JSON(网关回 HTML 等)——同降级语义
            raise EmbeddingError(f"embedding 响应非 JSON: {e}") from e
        vectors = data.get("vectors") or []
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
            async with httpx.AsyncClient(
                base_url=self._base,
                timeout=self._timeout,
                headers={"X-Internal-Token": self._token},
            ) as client:
                resp = await client.post("/internal/describe-table", json=payload)
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
                str(k): str(v) for k, v in (data.get("columns_comment") or {}).items()
            },
            sample_queries=[str(s) for s in data.get("sample_queries") or []],
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
