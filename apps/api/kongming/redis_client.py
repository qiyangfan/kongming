"""提供进程级 Redis 客户端单例和 FastAPI 依赖，供登录失败计数、令牌状态及联邦 state 使用。"""

from functools import lru_cache

from redis.asyncio import Redis, from_url

from kongming.config import get_settings


@lru_cache
def get_redis() -> Redis:
    return from_url(get_settings().redis_url, decode_responses=True)
