# pyright: reportPrivateUsage=false
"""限流窗口测试(smooth-chat-rate-windows):固定窗口原子性 + 问答滑动窗口。

固定窗口(工具配额):Lua 原子递增+TTL+限额——并发递增恰好 limit 次放行、
首请求带 TTL、窗口内不续期、过期自动重建、无 TTL 旧键修复、脚本异常
fail-open。滑动窗口(问答速率闸):窗口切换前耗尽额度后跨窗仍受限、额度随
旧请求老化恢复、主体/助手/IP 分桶隔离。真实 Redis(conftest db 15 用例级
清空);窗口时长用秒级小窗控测试耗时,边界时刻由 Redis 服务端时钟推算。
"""

import asyncio
from typing import Any, cast

from redis.asyncio import Redis

from kongming.redis_client import get_redis
from kongming.toolcall.ratelimit import RedisRateLimiter


async def _server_now(r: Redis) -> float:
    """Redis 服务端 UNIX 秒(与脚本内 TIME 同源,边界推算用)。

    redis-py 的 time 存根类型不全,按 conftest 既有口径经 Any 收窄。"""
    t = await cast("Any", r).time()
    return float(t[0]) + float(t[1]) / 1e9


class _BoomRedis:
    """register_script 即抛的假 Redis(fail-open 断言用)。"""

    def register_script(self, script: str) -> object:
        async def _call(*args: object, **kwargs: object) -> object:
            raise ConnectionError("redis down")

        return _call


# ── 固定窗口:原子性与 TTL 管理 ──


async def test_fixed_window_concurrent_increments_exactly_limit() -> None:
    """并发递增原子性:20 并发请求 limit=5 → 恰好 5 次放行(Lua 单脚本)。"""
    r = get_redis()
    limiter = RedisRateLimiter(r)
    results = await asyncio.gather(*[limiter.allow("k", limit=5, window_sec=60) for _ in range(20)])
    assert sum(results) == 5


async def test_fixed_window_first_request_sets_ttl() -> None:
    """首个请求即带 TTL(INCR 与 EXPIRE 同脚本,无两步竞态)。"""
    r = get_redis()
    limiter = RedisRateLimiter(r)
    assert await limiter.allow("k", limit=5, window_sec=60)
    ttl = await r.ttl("km:toolrl:k")
    assert 0 < ttl <= 60


async def test_fixed_window_no_renewal_within_window() -> None:
    """窗口内后续请求不续期(TTL 只减不增;窗口长度不因请求滑动)。"""
    r = get_redis()
    limiter = RedisRateLimiter(r)
    await limiter.allow("k", limit=5, window_sec=60)
    ttl1 = await r.ttl("km:toolrl:k")
    await asyncio.sleep(1.2)  # 跨过一个 TTL 秒刻度再断言(Redis TTL 秒级粒度)
    await limiter.allow("k", limit=5, window_sec=60)
    ttl2 = await r.ttl("km:toolrl:k")
    assert ttl2 < ttl1


async def test_fixed_window_rebuilds_after_expiry() -> None:
    """过期后键消失,新请求重建窗口并重新放行(1s 小窗)。"""
    r = get_redis()
    limiter = RedisRateLimiter(r)
    assert await limiter.allow("k", limit=1, window_sec=1)
    assert not await limiter.allow("k", limit=1, window_sec=1)
    await asyncio.sleep(1.2)
    assert await r.exists("km:toolrl:k") == 0
    assert await limiter.allow("k", limit=1, window_sec=1)  # 新窗口重新放行


async def test_fixed_window_repairs_legacy_no_ttl_key() -> None:
    """历史竞态残留的无 TTL 键:访问即补当前窗口 TTL(自愈,不永久生效)。"""
    r = get_redis()
    limiter = RedisRateLimiter(r)
    await r.set("km:toolrl:k", 3)  # 无 TTL 的旧键(裸 SET)
    assert await r.ttl("km:toolrl:k") == -1
    assert await limiter.allow("k", limit=5, window_sec=60)
    assert await r.ttl("km:toolrl:k") > 0


async def test_fixed_window_fail_open_on_script_error() -> None:
    """脚本异常(fail-open):放行并记录(限流是保护层非安全闸)。"""
    limiter = RedisRateLimiter(_BoomRedis())  # type: ignore[arg-type]
    assert await limiter.allow("k", limit=1, window_sec=60) is True


# ── 滑动窗口:跨窗平滑与老化恢复 ──


async def test_sliding_window_blocks_across_boundary_then_recovers() -> None:
    """窗口切换前耗尽 → 跨窗即时请求仍受限(固定窗口会放行,回归点);
    额度随旧请求老化恢复(前一窗口权重衰减)。窗口取 4s:拒绝区=新窗口
    前 2s(权重>0.5),断言前按服务端时钟逼近边界,不依赖固定 sleep 精度。"""
    r = get_redis()
    limiter = RedisRateLimiter(r)
    window, limit = 4, 2
    blocked = False
    key = "k-0"
    for attempt in range(3):
        key = f"k-{attempt}"
        # 当前窗口内耗尽额度
        assert await limiter.allow_sliding(key, limit=limit, window_sec=window)
        assert await limiter.allow_sliding(key, limit=limit, window_sec=window)
        assert not await limiter.allow_sliding(key, limit=limit, window_sec=window)
        # 睡到下一窗口前 1s 内再断言:固定 sleep 在全量负载下可能滑过 2s 拒绝区,
        # 逐步逼近边界让判定留在权重>0.5 的区间内(单次 Redis 往返远小于 1s 余量)
        while True:
            d = (await _server_now(r)) % window
            if d < 1.0:
                break
            await asyncio.sleep(min(window - d + 0.05, 0.5))
        if not await limiter.allow_sliding(key, limit=limit, window_sec=window):
            blocked = True
            break
        # 全量负载下调度停顿可能整体滑出拒绝区:换键重跑,不把环境抖动判成放行缺陷。
    assert blocked, "跨窗即时请求必须受限"
    # 睡到窗口中点(权重≈0.5,估算≈1 → 放行):老化恢复
    await asyncio.sleep(window / 2)
    assert await limiter.allow_sliding(key, limit=limit, window_sec=window)


async def test_sliding_window_bucket_isolation() -> None:
    """分桶隔离:主体/助手/IP 各键独立,一个桶耗尽不影响另一个。"""
    r = get_redis()
    limiter = RedisRateLimiter(r)
    assert await limiter.allow_sliding("chat:t1:u1", limit=1, window_sec=60)
    assert not await limiter.allow_sliding("chat:t1:u1", limit=1, window_sec=60)
    # 同主体换用户、公开嵌入换 bot/IP:互不挤占
    assert await limiter.allow_sliding("chat:t1:u2", limit=1, window_sec=60)
    assert await limiter.allow_sliding("chatbot:kb:1.1.1.1", limit=1, window_sec=60)
    assert await limiter.allow_sliding("chatbot:kb:2.2.2.2", limit=1, window_sec=60)


async def test_sliding_window_fail_open_on_script_error() -> None:
    """滑动窗口脚本异常同样 fail-open 放行。"""
    limiter = RedisRateLimiter(_BoomRedis())  # type: ignore[arg-type]
    assert await limiter.allow_sliding("k", limit=1, window_sec=60) is True


async def test_window_key_namespaces_separated() -> None:
    """固定窗口(工具配额)与滑动窗口(问答)键命名空间分离(策略不串)。"""
    r = get_redis()
    limiter = RedisRateLimiter(r)
    await limiter.allow("k", limit=5, window_sec=60)
    await limiter.allow_sliding("k", limit=5, window_sec=60)
    r_any = cast("Any", r)  # scan_iter 存根类型不全(conftest 同款口径)
    tool_keys = [k async for k in r_any.scan_iter(match="km:toolrl:*")]
    chat_keys = [k async for k in r_any.scan_iter(match="km:chatrl:*")]
    assert tool_keys and chat_keys
    assert not set(tool_keys) & set(chat_keys)
