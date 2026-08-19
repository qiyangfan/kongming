"""只读执行池:业务查询专用引擎,与控制面写引擎物理分离(纵深防御)。

- 连接级只读:server_settings default_transaction_read_only=on(spec 原话),
  即使防火墙被绕过,写操作在数据库层再被拒一次;
- statement_timeout 兜底慢查询;池 size=5/无溢出(datasource spec 池受限);
- 引擎按 datasource_key 惰性建立并缓存(008 动态化):default 走平台配置连接串,
  其余 key 由注册数据源的解密 DSN 建池(调用方先校验+解密,凭证不出控制面);
- 模块级 asyncio.Lock 防并发建双池(上游 computeIfAbsent 同语义);
- 定向失效 evict_engine(key):数据源删除/禁用/连接信息变更 commit 后调用;
  dispose_read_engines 全量清理由 lifespan teardown 接线(008 前无调用点)。
"""

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
    """进程收尾全量清理(lifespan teardown 调用);单个 dispose 失败不跳过后续(R2-O4)。"""
    async with _ENGINE_LOCK:
        engines = list(_ENGINES.values())
        _ENGINES.clear()
    for engine in engines:
        try:
            await engine.dispose()
        except Exception:  # noqa: BLE001  # 收尾期尽力而为,证据留日志
            log.warning("只读池收尾 dispose 失败(已忽略)", exc_info=True)
