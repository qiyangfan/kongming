"""Redis 基础设施:进程级单例 + FastAPI 依赖。

auth 域的 refresh 家族指纹/登录失败计数/oauth state 均落 Redis(ADR-003 既定),
decode_responses=True 以 str 语义读写。
"""

from functools import lru_cache

from redis.asyncio import Redis, from_url

from kongming.config import get_settings


@lru_cache
def get_redis() -> Redis:
    return from_url(get_settings().redis_url, decode_responses=True)
