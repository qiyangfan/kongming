"""提供异步数据库引擎、会话工厂和 FastAPI 会话依赖，并重新导出 UUID v7 生成器。"""

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
    # pool_pre_ping 检出前存活校验(对端掐断连接不再变成间歇 500);
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
