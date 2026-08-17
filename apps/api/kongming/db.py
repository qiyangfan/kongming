"""数据库基础设施:async engine/session + FastAPI 依赖 + UUID v7 生成器。

ADR-004 约定主键 UUID v7;Python 3.13 标准库尚无 uuid7(3.14 才入),
此处按 RFC 9562 内联实现(48bit 毫秒时间戳 + 版本/变体位 + 随机位),不引新依赖。
"""

import os
import time
import uuid
from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from kongming.config import get_settings


@lru_cache
def get_engine() -> AsyncEngine:
    return create_async_engine(get_settings().database_url, pool_size=5, max_overflow=5)


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖:请求级会话。只读路径由用例自行决定 commit/rollback。"""
    async with get_sessionmaker()() as session:
        yield session


def uuid7() -> uuid.UUID:
    """RFC 9562 UUID v7:毫秒时间戳前缀,单调友好(索引局部性优于 v4)。"""
    ts_ms = time.time_ns() // 1_000_000
    rand_a = int.from_bytes(os.urandom(2), "big") & 0x0FFF
    rand_b = int.from_bytes(os.urandom(8), "big") & 0x3FFFFFFFFFFFFFFF
    value = (
        ((ts_ms & 0xFFFFFFFFFFFF) << 80)
        | (0x7 << 76)
        | (rand_a << 64)
        | (0x2 << 62)
        | rand_b
    )
    return uuid.UUID(int=value)
