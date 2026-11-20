"""Redis 限流实现。工具配额用固定窗口,计数/TTL/限额判定由同一 Lua 脚本原子完成;
问答限流用 Redis 服务端时钟估算当前窗口与前一窗口的加权计数。

滑动窗口不限制窗口内突发,精确用量仍以 `sys_llm_usage` 为准。
Redis 不可用时告警并放行;限流不替代权限、租户隔离或 SQL 防火墙。"""

import logging

from redis.asyncio import Redis

log = logging.getLogger(__name__)

# 固定窗口:INCR 后 TTL<0(刚创建或历史无 TTL 残留)→ 补窗口 TTL;不续期。
_FIXED_WINDOW_LUA = """
local current = redis.call('INCR', KEYS[1])
if redis.call('TTL', KEYS[1]) < 0 then
    redis.call('EXPIRE', KEYS[1], ARGV[2])
end
if current > tonumber(ARGV[1]) then
    return 0
end
return 1
"""

# 滑动窗口:服务端时钟定窗;前一窗口按剩余权重计入估算;超估不递增。
_SLIDING_WINDOW_LUA = """
local window = tonumber(ARGV[2])
local now = tonumber(redis.call('TIME')[1])
local cur_idx = math.floor(now / window)
local cur_key = KEYS[1] .. ':' .. cur_idx
local prev_key = KEYS[1] .. ':' .. (cur_idx - 1)
local cur = tonumber(redis.call('GET', cur_key) or '0')
local prev = tonumber(redis.call('GET', prev_key) or '0')
local weight = (window - (now - cur_idx * window)) / window
if cur + prev * weight + 1 > tonumber(ARGV[1]) then
    return 0
end
redis.call('INCR', cur_key)
redis.call('EXPIRE', cur_key, window * 2)
return 1
"""


class RedisRateLimiter:
    def __init__(self, redis: Redis) -> None:
        self._r = redis
        self._fixed = redis.register_script(_FIXED_WINDOW_LUA)
        self._sliding = redis.register_script(_SLIDING_WINDOW_LUA)

    async def allow(self, key: str, *, limit: int, window_sec: int) -> bool:
        """固定窗口判定(工具配额;原子递增+TTL+限额,失败 fail-open)。"""
        try:
            return bool(await self._fixed(keys=[f"km:toolrl:{key}"], args=[limit, window_sec]))
        except Exception:
            # 降级放行(fail-open):限流层失效不阻断业务,error 暴露
            log.exception("限流 Redis 不可用,fail-open 放行 key=%s", key)
            return True

    async def allow_sliding(self, key: str, *, limit: int, window_sec: int) -> bool:
        """滑动窗口判定(问答速率闸;近似加权估算,失败 fail-open)。"""
        try:
            return bool(await self._sliding(keys=[f"km:chatrl:{key}"], args=[limit, window_sec]))
        except Exception:
            log.exception("滑动窗口限流 Redis 不可用,fail-open 放行 key=%s", key)
            return True
