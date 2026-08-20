"""检索上下文缓存(009):Redis ctx 层(TTL 10min,fail-open)。

失效纪律(D6):知识写操作(上传/删除/fetch-metadata/reindex/datasource 删除)
在业务事务提交后精确失效(SCAN 收集+批量 DEL,仅目标 tenant+datasource 范围);
回填旧值窗口由 TTL 有界吸收(上游同款取舍)。
"""

import hashlib
import json
import logging

from redis.asyncio import Redis

from kongming.rag.models import RagContext

log = logging.getLogger(__name__)

_CTX_PREFIX = "km:ragctx:"
_CTX_TTL_SEC = 600
_SCAN_COUNT = 100


def _ctx_key(tenant_id: str, datasource_key: str, question: str) -> str:
    digest = hashlib.sha256(question.encode("utf-8")).hexdigest()
    return f"{_CTX_PREFIX}{tenant_id}:{datasource_key}:{digest}"


class ContextCache:
    """fail-open:Redis 不可用时读=未命中、写/失效=放弃(仅告警)。"""

    def __init__(self, redis: Redis | None) -> None:
        self._redis = redis

    async def get(self, tenant_id: str, datasource_key: str, question: str) -> RagContext | None:
        if self._redis is None:
            return None
        try:
            raw = await self._redis.get(_ctx_key(tenant_id, datasource_key, question))
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
        return RagContext(
            schema_hint=str(data.get("schema_hint") or ""),
            history_sql=str(data.get("history_sql") or ""),
        )

    async def set(
        self, tenant_id: str, datasource_key: str, question: str, ctx: RagContext
    ) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.set(
                _ctx_key(tenant_id, datasource_key, question),
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
            keys = [key async for key in self._redis.scan_iter(match=pattern, count=_SCAN_COUNT)]
            if keys:
                await self._redis.delete(*keys)
        except Exception:  # noqa: BLE001
            log.warning("ctx 缓存失效失败(旧值残留至 TTL 过期)", exc_info=True)
