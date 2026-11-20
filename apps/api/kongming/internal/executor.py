"""业务查询用与控制面写引擎分离的只读连接池:连接级只读+语句超时兜底;池按数据源按需建并缓存,数据源变更提交后定向失效,退出时全量释放。"""

import asyncio
import logging

from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from kongming.config import get_settings

log = logging.getLogger(__name__)

_ENGINES: dict[str, AsyncEngine] = {}
_ENGINE_LOCK = asyncio.Lock()


def _create_engine(dsn: str | URL) -> AsyncEngine:
    """建池参数全 key 同构(池 5/无溢出/连接级只读/30s 语句超时)。"""
    return create_async_engine(
        dsn,
        pool_size=5,
        max_overflow=0,
        connect_args={
            "server_settings": {
                "default_transaction_read_only": "on",
                "statement_timeout": "30000",
            }
        },
    )


async def get_or_create_engine(key: str = "default", dsn: URL | None = None) -> AsyncEngine:
    """按 key 取/建只读池;非 default 必须携带 dsn(调用方已校验+解密)。"""
    async with _ENGINE_LOCK:
        if key not in _ENGINES:
            resolved: str | URL | None = dsn
            if resolved is None:
                if key != "default":
                    raise KeyError(key)  # 调用方先校验;此处再兜底
                resolved = get_settings().database_url
            _ENGINES[key] = _create_engine(resolved)
    return _ENGINES[key]


async def evict_engine(key: str) -> None:
    """定向失效(pop+dispose);不存在则 no-op。dispose 失败由调用方按尽力而为处理。"""
    async with _ENGINE_LOCK:
        engine = _ENGINES.pop(key, None)
    if engine is not None:
        await engine.dispose()


async def dispose_read_engines() -> None:
    """进程收尾全量清理(lifespan teardown 调用);单个 dispose 失败不跳过后续。"""
    async with _ENGINE_LOCK:
        engines = list(_ENGINES.values())
        _ENGINES.clear()
    for engine in engines:
        try:
            await engine.dispose()
        except Exception:  # 收尾期尽力而为,证据留日志
            log.warning("只读池收尾 dispose 失败(已忽略)", exc_info=True)
