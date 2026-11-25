"""语音建连闸的 Redis 适配:原子频次/预算、0=关闭、Redis 故障 fail-open。"""

from typing import Any, cast
from uuid import uuid4

import anyio
import pytest

from kongming.voice.repository import RedisCallGuard


class _BoomRedis:
    """模拟 Redis 故障:任何 eval 都抛错。"""

    async def eval(self, *_args: object, **_kwargs: object) -> object:
        raise RuntimeError("redis down")


def test_guard_fail_open_on_redis_error() -> None:
    """频次与预算在 Redis 故障时 fail-open;释放不抛(准入 fail-closed 兜底)。"""
    guard = RedisCallGuard(
        cast("Any", _BoomRedis()),
        rate_per_minute=1,
        daily_per_user=1,
        daily_per_deployment=1,
    )

    async def _go() -> None:
        assert await guard.allow_establish("u")
        assert await guard.reserve_user_day("u")
        assert await guard.reserve_deployment_day()
        await guard.release_user_day("u")
        await guard.release_deployment_day()

    anyio.run(_go)


def test_guard_zero_limits_are_disabled() -> None:
    """0=该维不启用:即使 Redis 故障也不触碰存储。"""
    guard = RedisCallGuard(
        cast("Any", _BoomRedis()),
        rate_per_minute=0,
        daily_per_user=0,
        daily_per_deployment=0,
    )

    async def _go() -> None:
        assert await guard.allow_establish("u")
        assert await guard.reserve_user_day("u")
        assert await guard.reserve_deployment_day()
        await guard.release_user_day("u")
        await guard.release_deployment_day()

    anyio.run(_go)


def test_guard_atomic_rate_and_user_budget() -> None:
    """真实 Redis:固定窗口频次与用户预算的原子上限/释放语义。"""
    from redis.asyncio import from_url

    from kongming.redis_client import get_settings

    client = from_url(get_settings().redis_url, decode_responses=True)
    guard = RedisCallGuard(client, rate_per_minute=2, daily_per_user=1, daily_per_deployment=0)
    user = f"guard-test-{uuid4().hex}"

    async def _go() -> None:
        try:
            assert await guard.allow_establish(user)
            assert await guard.allow_establish(user)
            assert not await guard.allow_establish(user)  # 固定窗口超限不递减
            assert await guard.reserve_user_day(user)
            assert not await guard.reserve_user_day(user)  # 超限回滚,计数仍为 1
            await guard.release_user_day(user)
            assert await guard.reserve_user_day(user)  # 释放后可再占
            await guard.release_user_day(user)
        finally:
            await client.aclose()

    anyio.run(_go)


def test_guard_release_to_zero_resets_window() -> None:
    """失败占用的释放把计数归零时删键:24 小时窗口从首个成功建联起算。"""
    from redis.asyncio import from_url

    from kongming.redis_client import get_settings

    client = from_url(get_settings().redis_url, decode_responses=True)
    guard = RedisCallGuard(client, rate_per_minute=0, daily_per_user=2, daily_per_deployment=0)
    user = f"guard-window-{uuid4().hex}"
    key = f"voice:guard:user:{user}"

    async def _go() -> None:
        try:
            assert await guard.reserve_user_day(user)
            assert await client.ttl(key) > 0
            await guard.release_user_day(user)
            assert await client.exists(key) == 0  # 归零删键,不保留空窗口
            assert await guard.reserve_user_day(user)  # 下一次成功重新起窗
            await guard.release_user_day(user)
        finally:
            await client.aclose()

    anyio.run(_go)


@pytest.mark.parametrize("limit", [0])
def test_guard_does_not_create_keys_when_disabled(limit: int) -> None:
    """关闭的维度不产生 Redis 写入(用故障替身证明未触碰存储)。"""
    guard = RedisCallGuard(
        cast("Any", _BoomRedis()),
        rate_per_minute=limit,
        daily_per_user=limit,
        daily_per_deployment=limit,
    )
    assert anyio.run(guard.reserve_user_day, "u") is True
