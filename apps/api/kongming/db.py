"""数据库基础设施:async engine/session + FastAPI 依赖。

ADR-004 约定主键 UUID v7;生成器实现见 kongming/uuid7.py(纯函数模块),
此处 re-export 维持既有 `from kongming.db import uuid7` import 路径不变。
"""

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from kongming.config import get_settings
from kongming.uuid7 import uuid7

__all__ = ["get_engine", "get_sessionmaker", "get_session", "uuid7"]


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
