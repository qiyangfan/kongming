"""只读执行池:业务查询专用引擎,与控制面写引擎物理分离(纵深防御)。

- 连接级只读:server_settings default_transaction_read_only=on(spec 原话),
  即使防火墙被绕过,写操作在数据库层再被拒一次;
- statement_timeout 兜底慢查询;池 size=5/无溢出(datasource spec 池受限);
- 引擎按 datasource_key 惰性建立并缓存——本变更仅 'default'
  (sys_datasource 行动态池后续插槽,池随数据源变更失效的钩子也在此)。
"""

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from kongming.config import get_settings

_ENGINES: dict[str, AsyncEngine] = {}

SUPPORTED_KEYS = ("default",)


def get_read_engine(key: str = "default") -> AsyncEngine:
    if key not in SUPPORTED_KEYS:
        raise KeyError(key)  # 调用方先校验;此处再兜底
    if key not in _ENGINES:
        _ENGINES[key] = create_async_engine(
            get_settings().database_url,
            pool_size=5,
            max_overflow=0,
            connect_args={
                "server_settings": {
                    "default_transaction_read_only": "on",
                    "statement_timeout": "30000",
                }
            },
        )
    return _ENGINES[key]


async def dispose_read_engines() -> None:
    """进程收尾/数据源变更失效钩子(本变更仅注册 default)。"""
    for engine in _ENGINES.values():
        await engine.dispose()
    _ENGINES.clear()
