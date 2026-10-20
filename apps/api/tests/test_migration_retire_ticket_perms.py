# pyright: reportPrivateUsage=false
"""0057 权限退役迁移测试(remove-ticket-domain 任务 2.1,安全规则类测试先行)。

覆盖:两项 ticket 权限与角色绑定被精确删除;无关权限/角色绑定与历史
sys_tool_audit 保留;升级后启动种子不重建;downgrade 仅还原权限行、
不还原绑定(设计明示的回滚约束)。
迁移演练走 conftest.migrated_tmp_db 一次性临时库(dropped 列占位零累积)。
"""

import asyncio
import uuid
from typing import Any, cast

from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from alembic import command
from kongming.auth.seed import seed_all
from kongming.config import get_settings
from tests.conftest import migrated_tmp_db

_TICKET_CODES = ("tool:ticket:search", "tool:ticket:manage")


async def _run_alembic(session: AsyncSession, cfg: Config, target: str) -> None:
    """执行一条 alembic 命令(前 rollback 清场;连接持 ACCESS SHARE 会死等
    ALTER 的 ACCESS EXCLUSIVE,与 test_http_connector 同款处理)。
    目标 revision 早于当前= downgrade(0057 删除逻辑的演练入口)。"""
    await session.rollback()
    if target == "head":
        await asyncio.to_thread(command.upgrade, cfg, "head")
    else:
        await asyncio.to_thread(command.downgrade, cfg, target)


async def _seed_fixture(session: AsyncSession) -> dict[str, Any]:
    """造数:两项 ticket 权限+角色绑定、一项无关权限+绑定、一条 ticket 审计行。"""
    role_id = str(uuid.uuid4())
    await session.execute(
        text(
            "INSERT INTO public.sys_role (id, role_key, role_name, data_scope, sql_scope)"
            " VALUES (:i, 'mig_probe', '迁移探针', 'SELF', 'SELECT_ONLY')"
        ),
        {"i": role_id},
    )
    perm_ids: dict[str, str] = {}
    for code in (*_TICKET_CODES, "tool:sql:validate"):
        pid = str(uuid.uuid4())
        perm_ids[code] = pid
        await session.execute(
            text(
                "INSERT INTO public.sys_permission (id, perm_code, perm_name)"
                " VALUES (:i, :c, :c) ON CONFLICT (perm_code) DO NOTHING"
            ),
            {"i": pid, "c": code},
        )
        await session.execute(
            text(
                "INSERT INTO public.sys_role_permission (role_id, permission_id)"
                " SELECT :r, p.id FROM public.sys_permission p WHERE p.perm_code = :c"
                " ON CONFLICT DO NOTHING"
            ),
            {"r": role_id, "c": code},
        )
    audit_id = str(uuid.uuid4())
    await session.execute(
        text(
            "INSERT INTO public.sys_tool_audit"
            " (id, trace_id, user_id, tenant_id, tool_name, status, domain)"
            " VALUES (:i, 'tr-mig-probe', :u, :t, 'ticket_search', 'EXECUTED', 'ticket')"
        ),
        {"i": audit_id, "u": str(uuid.uuid4()), "t": str(uuid.uuid4())},
    )
    await session.commit()
    return {"role_id": role_id, "audit_id": audit_id}


async def _perm_codes(session: AsyncSession) -> set[str]:
    rows = (
        (await session.execute(text("SELECT perm_code FROM public.sys_permission"))).scalars().all()
    )
    return set(rows)


async def _binding_count(session: AsyncSession, role_id: str) -> int:
    return cast(
        "int",
        (
            await session.execute(
                text("SELECT count(*) FROM public.sys_role_permission WHERE role_id = :r"),
                {"r": role_id},
            )
        ).scalar_one(),
    )


async def test_migration_0057_precise_retire() -> None:
    """升级只删两项 ticket 权限及其绑定;无关权限/绑定与历史审计保留。"""
    async with migrated_tmp_db() as (session, cfg):
        fx = await _seed_fixture(session)
        assert set(_TICKET_CODES) <= await _perm_codes(session)
        assert await _binding_count(session, fx["role_id"]) == 3

        # 0056 → head 走真实迁移路径(先降后升,演练 upgrade 删除逻辑)
        await _run_alembic(session, cfg, "0056")
        await _run_alembic(session, cfg, "head")

        codes = await _perm_codes(session)
        assert not (set(_TICKET_CODES) & codes)  # 两项精确删除
        assert "tool:sql:validate" in codes  # 无关权限保留
        assert await _binding_count(session, fx["role_id"]) == 1  # 仅剩无关绑定
        audit = (
            await session.execute(
                text("SELECT count(*) FROM public.sys_tool_audit WHERE id = :i"),
                {"i": fx["audit_id"]},
            )
        ).scalar_one()
        assert audit == 1  # 历史审计零触碰


async def test_migration_0057_seed_does_not_recreate() -> None:
    """迁移后启动种子(只增不删语义)不重建两项 ticket 权限。"""
    async with migrated_tmp_db() as (session, cfg):
        await _seed_fixture(session)
        await _run_alembic(session, cfg, "0056")
        await _run_alembic(session, cfg, "head")
        await seed_all(session, get_settings())
        codes = await _perm_codes(session)
        assert not (set(_TICKET_CODES) & codes)


async def test_migration_0057_downgrade_restores_rows_not_bindings() -> None:
    """downgrade 仅还原权限行(目录完整性),角色绑定不还原(设计回滚约束)。"""
    async with migrated_tmp_db() as (session, cfg):
        fx = await _seed_fixture(session)
        await _run_alembic(session, cfg, "0056")
        await _run_alembic(session, cfg, "head")
        assert not (set(_TICKET_CODES) & await _perm_codes(session))

        await _run_alembic(session, cfg, "0056")  # downgrade
        codes = await _perm_codes(session)
        assert set(_TICKET_CODES) <= codes  # 权限行还原
        assert await _binding_count(session, fx["role_id"]) == 1  # 绑定不还原
