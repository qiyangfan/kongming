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
    s = get_settings()
    # 074:pool_pre_ping 检出前存活校验(对端掐断连接不再变成间歇 500);
    # 容量与换代周期可配,缺省 5/5/关 与历史行为零差异
    return create_async_engine(
        s.database_url,
        pool_size=s.db_pool_size,
        max_overflow=s.db_max_overflow,
        pool_pre_ping=True,
        pool_recycle=s.db_pool_recycle_sec or -1,
    )


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖:请求级会话。只读路径由用例自行决定 commit/rollback。"""
    async with get_sessionmaker()() as session:
        yield session
