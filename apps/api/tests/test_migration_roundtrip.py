"""迁移全链往返:base 降级后重升 head(降级脚本不留孤儿、重升可复现)。

单条迁移的升降级由各域迁移测试覆盖;此处守住整链:任何迁移的 downgrade
漏删对象都会让重升失败或残留表,门禁即红。
"""

import asyncio

from alembic.script import ScriptDirectory
from sqlalchemy import text

from alembic import command
from tests.conftest import migrated_tmp_db


async def test_full_chain_downgrade_to_base_then_upgrade() -> None:
    async with migrated_tmp_db() as (session, cfg):
        await asyncio.to_thread(command.downgrade, cfg, "base")
        leftover = await session.execute(
            text(
                """
                SELECT count(*) FROM pg_tables
                WHERE schemaname IN ('public', 'rag') AND tablename <> 'alembic_version'
                """
            )
        )
        assert leftover.scalar_one() == 0  # 降级到 base 后不残留业务表

        await asyncio.to_thread(command.upgrade, cfg, "head")
        head = ScriptDirectory.from_config(cfg).get_current_head()
        current = await session.execute(text("SELECT version_num FROM alembic_version"))
        assert current.scalar_one() == head
        restored = await session.execute(text("SELECT to_regclass('public.sys_user')"))
        assert restored.scalar_one() == "sys_user"
