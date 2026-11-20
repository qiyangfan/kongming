"""auth 依赖装配(Service 注解)。

组合根之外唯一的装配层:路由只依赖本模块导出的 Service,不直接摸仓储。
"""

from typing import Annotated

from fastapi import Depends
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth import providers
from kongming.auth.repository import SqlUnitOfWork
from kongming.auth.service import AuthService
from kongming.auth.stores import (
    RedisLoginAttemptStore,
    RedisOAuthStateStore,
    RedisRefreshFamilyStore,
)
from kongming.config import get_settings
from kongming.db import get_session
from kongming.redis_client import get_redis


async def get_auth_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> AuthService:
    """装配 AuthService:唯一知道各端口绑定哪种实现的地方。"""
    return AuthService(
        SqlUnitOfWork(session),
        attempts=RedisLoginAttemptStore(redis),
        families=RedisRefreshFamilyStore(redis),
        states=RedisOAuthStateStore(redis),
        settings=get_settings(),
        registry=providers.REGISTRY,
    )


Service = Annotated[AuthService, Depends(get_auth_service)]
