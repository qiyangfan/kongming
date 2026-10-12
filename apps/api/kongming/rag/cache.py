"""检索上下文缓存(009):Redis ctx 层(TTL 10min,fail-open)。

失效纪律(D6):知识写操作(上传/删除/fetch-metadata/reindex/datasource 删除)
在业务事务提交后精确失效(SCAN 收集+批量 DEL,仅目标 tenant+datasource 范围);
回填旧值窗口由 TTL 有界吸收(上游同款取舍)。
"""

import hashlib
import json
import logging
from collections.abc import AsyncIterator, Callable, Sequence
from typing import cast

from redis.asyncio import Redis

from kongming.config import DEFAULT_FULLTEXT_TS_CONFIG, validated_fulltext_ts_config
from kongming.rag.models import RagContext, team_set_fingerprint

log = logging.getLogger(__name__)

_CTX_PREFIX = "km:ragctx:"
_CTX_TTL_SEC = 600
_SCAN_COUNT = 100
# 112:查询语义版本段(全文路「AND 优先、内容词兜底」)——查询语义变更时
# bump 本常量,变更前旧键空间在 TTL 窗口内自然弃用不串味(与 098 配置段
# 同一「键版本化」思路;同部署内恒定,evict SCAN 模式不含该段跨语义全收)
_QUERY_SEMANTICS = "and-cw"


def _ctx_key(
    tenant_id: str, datasource_key: str, question: str, ts_config: str, team_fp: str = "public"
) -> str:
    """键段序:租户/数据源/分词配置/查询语义/团队集合指纹在 hash 前缀段
    (失效 SCAN 模式 `租户:数据源:*` 跨配置、跨语义、跨可见集合全收——
    数据写失效本就应清全部变体),问句材料(含 history/扩展开关等派生键
    材料)hash 在尾段。102:团队集合指纹段(team_set_fingerprint,空集=
    "public")——不同可见集合的调用者 MUST NOT 互命中;团队关系变化→指纹
    变→自然隔离(TTL 吸收旧条目),与 098 配置维度同一「键版本化」思路。"""
    digest = hashlib.sha256(question.encode("utf-8")).hexdigest()
    return (
        f"{_CTX_PREFIX}{tenant_id}:{datasource_key}:{ts_config}"
        f":{_QUERY_SEMANTICS}:{team_fp}:{digest}"
    )


class ContextCache:
    """fail-open:Redis 不可用时读=未命中、写/失效=放弃(仅告警)。

    098:键纳入分词配置维度(键版本化最简形态)——配置只在启动读、键含配置
    即天然隔离,不做切换期主动清缓存;否则换配置后 TTL 窗口内会命中异分词
    形态的旧输出(静默混用)。evict 模式不含配置段,跨配置全失效。
    102:键纳入调用者团队集合指纹维度(同一思路)——可见集合因用户团队关系
    而异,键含指纹即天然隔离;evict 模式同样不含指纹段,归属变更后跨集合全失效。
    """

    def __init__(self, redis: Redis | None, ts_config: str = DEFAULT_FULLTEXT_TS_CONFIG) -> None:
        self._redis = redis
        self._ts = validated_fulltext_ts_config(ts_config)

    async def get(
        self,
        tenant_id: str,
        datasource_key: str,
        question: str,
        team_ids: Sequence[str] = (),
    ) -> RagContext | None:
        if self._redis is None:
            return None
        try:
            raw = await self._redis.get(
                _ctx_key(
                    tenant_id, datasource_key, question, self._ts, team_set_fingerprint(team_ids)
                )
            )
        except Exception:  # noqa: BLE001
            log.warning("ctx 缓存读失败,按未命中处理", exc_info=True)
            return None
        if raw is None:
            return None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        # isinstance 收窄产物键型未知,收窄键型后再取值(仅类型断言)
        typed = cast("dict[str, object]", data)
        return RagContext(
            schema_hint=str(typed.get("schema_hint") or ""),
            history_sql=str(typed.get("history_sql") or ""),
        )

    async def set(
        self,
        tenant_id: str,
        datasource_key: str,
        question: str,
        ctx: RagContext,
        team_ids: Sequence[str] = (),
    ) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.set(
                _ctx_key(
                    tenant_id, datasource_key, question, self._ts, team_set_fingerprint(team_ids)
                ),
                json.dumps(
                    {"schema_hint": ctx.schema_hint, "history_sql": ctx.history_sql},
                    ensure_ascii=False,
                ),
                ex=_CTX_TTL_SEC,
            )
        except Exception:  # noqa: BLE001
            log.warning("ctx 缓存写失败,跳过", exc_info=True)

    async def evict(self, tenant_id: str, datasource_key: str) -> None:
        """精确失效该 tenant+datasource 的全部 ctx 缓存(SCAN,禁 KEYS)。"""
        if self._redis is None:
            return
        pattern = f"{_CTX_PREFIX}{tenant_id}:{datasource_key}:*"
        try:
            # redis-py scan_iter 签名 kwargs 未注解(strict 判部分未知):成员
            # 访问行豁免,经 cast 收窄为「返回文本键迭代器」的可调用后使用
            it = cast(
                "Callable[..., AsyncIterator[str]]",
                self._redis.scan_iter,  # pyright: ignore[reportUnknownMemberType]  # redis-py 未注解
            )
            keys = [key async for key in it(match=pattern, count=_SCAN_COUNT)]
            if keys:
                await self._redis.delete(*keys)
        except Exception:  # noqa: BLE001
            log.warning("ctx 缓存失效失败(旧值残留至 TTL 过期)", exc_info=True)
