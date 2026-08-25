"""固定窗口限流(019,六层防御 L5):Redis INCR + EXPIRE。

Redis 不可用 fail-open 仅告警放行(与 007 吊销降级同口径:可用性优先,
限流是配额保护层而非安全闸;安全闸=权限点/租户/防火墙,各自独立)。
"""

import logging

from redis.asyncio import Redis

log = logging.getLogger(__name__)


class RedisRateLimiter:
    def __init__(self, redis: Redis) -> None:
        self._r = redis

    async def allow(self, key: str, *, limit: int, window_sec: int) -> bool:
        try:
            full_key = f"km:toolrl:{key}"
            # 首次写入用 SET NX EX 原子带 TTL(审查循环 B8:裸 INCR+EXPIRE 两步间
            # 进程崩溃会留下无 TTL 的永久计数键,该 (tenant,user) 被永久限流)
            created = await self._r.set(full_key, 1, ex=window_sec, nx=True)
            if created:
                return 1 <= limit
            count = await self._r.incr(full_key)  # INCR 不改动既有 TTL
            return count <= limit
        except Exception:
            # 降级放行(fail-open):限流层失效不阻断业务,error 暴露
            log.error("限流 Redis 不可用,fail-open 放行 key=%s", key, exc_info=True)
            return True
