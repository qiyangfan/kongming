"""Alembic 环境:async engine(适配 asyncpg driver),URL 从 KM_DATABASE_URL 注入。"""

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

from kongming.config import get_settings  # noqa: E402

# URL 单源=KM_DATABASE_URL(settings);程序化注入(迁移演练临时库,如
# tests.conftest.migrated_tmp_db)优先——ini 的占位 url 以 driver:// 开头,
# 借缀区分「未注入」
_injected_url = config.get_main_option("sqlalchemy.url")
if not _injected_url or _injected_url.startswith("driver://"):
    config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = None  # baseline 为显式 SQL,不用 autogenerate


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
