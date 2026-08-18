"""启动种子(rbac spec「种子数据」):齐备/幂等/只增不改/缺省密码强制首登改密。"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth import password as pwd
from kongming.auth.seed import DEFAULT_ADMIN_PASSWORD, seed_all
from kongming.config import get_settings


async def test_seed_empty_db_creates_everything(session: AsyncSession) -> None:
    await seed_all(session, get_settings())
    roles = (
        await session.execute(text("SELECT role_key, sort FROM public.sys_role ORDER BY sort"))
    ).all()
    assert [(r.role_key, r.sort) for r in roles] == [("SUPER_ADMIN", 1), ("DEVELOPER", 2)]
    perms = (
        await session.execute(text("SELECT perm_code FROM public.sys_permission ORDER BY 1"))
    ).scalars().all()
    assert perms == ["chat:execute", "system:role:manage", "system:user:manage"]
    tenants = (
        await session.execute(text("SELECT tenant_key FROM public.sys_tenant"))
    ).scalars().all()
    assert tenants == ["default"]
    admin = (
        await session.execute(
            text(
                "SELECT id, password_hash, password_changed_at FROM public.sys_user"
                " WHERE username='admin'"
            )
        )
    ).one()
    assert pwd.verify_password(DEFAULT_ADMIN_PASSWORD, admin.password_hash)
    assert admin.password_changed_at is None  # 缺省密码 → 强制首登改密
    # 绑定:admin→SUPER_ADMIN、admin→default 租户、SUPER_ADMIN→全部权限点
    assert (
        await session.execute(
            text(
                "SELECT count(*) FROM public.sys_user_role ur"
                " JOIN public.sys_role r ON r.id=ur.role_id"
                " WHERE ur.user_id=:uid AND r.role_key='SUPER_ADMIN'"
            ),
            {"uid": admin.id},
        )
    ).scalar_one() == 1
    assert (
        await session.execute(
            text(
                "SELECT count(*) FROM public.sys_user_tenant ut"
                " JOIN public.sys_tenant t ON t.id=ut.tenant_id"
                " WHERE ut.user_id=:uid AND t.tenant_key='default'"
            ),
            {"uid": admin.id},
        )
    ).scalar_one() == 1
    assert (
        await session.execute(
            text(
                "SELECT count(*) FROM public.sys_role_permission rp"
                " JOIN public.sys_role r ON r.id=rp.role_id"
                " WHERE r.role_key='SUPER_ADMIN'"
            )
        )
    ).scalar_one() == 3
    # 推荐绑定(只增不摘):DEVELOPER 增量获得 chat:execute
    dev_perms = (
        await session.execute(
            text(
                "SELECT p.perm_code FROM public.sys_role_permission rp"
                " JOIN public.sys_role r ON r.id=rp.role_id"
                " JOIN public.sys_permission p ON p.id=rp.permission_id"
                " WHERE r.role_key='DEVELOPER'"
            )
        )
    ).scalars().all()
    assert list(dev_perms) == ["chat:execute"]


async def test_seed_idempotent_on_repeat(session: AsyncSession) -> None:
    await seed_all(session, get_settings())
    await seed_all(session, get_settings())

    async def _count(table: str) -> int:
        return (await session.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()  # noqa: S608  # 常量表名

    assert await _count("public.sys_user") == 1
    assert await _count("public.sys_role") == 2
    assert await _count("public.sys_permission") == 3
    assert await _count("public.sys_tenant") == 1
    assert await _count("public.sys_user_role") == 1
    assert await _count("public.sys_user_tenant") == 1
    assert await _count("public.sys_role_permission") == 4  # 超管 3 + DEVELOPER 推荐 1


async def test_seed_with_env_password_not_forced_to_change(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "admin_initial_password", "S3cure!")
    await seed_all(session, get_settings())
    row = (
        await session.execute(
            text(
                "SELECT password_hash, password_changed_at FROM public.sys_user"
                " WHERE username='admin'"
            )
        )
    ).one()
    assert pwd.verify_password("S3cure!", row.password_hash)
    assert row.password_changed_at is not None  # env 密码视为已改密


async def test_seed_never_overwrites_existing_rows(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 预置被线上改过的角色名与 admin 自改密码
    await session.execute(
        text(
            "INSERT INTO public.sys_role (id, role_key, role_name, data_scope, sql_scope)"
            " VALUES (gen_random_uuid(), 'DEVELOPER', '研发(线上改名)', 'DEPT', 'READ_DDL')"
        )
    )
    await session.commit()
    monkeypatch.setattr(get_settings(), "admin_initial_password", "S3cure!")
    await seed_all(session, get_settings())
    name = (
        await session.execute(
            text("SELECT role_name FROM public.sys_role WHERE role_key='DEVELOPER'")
        )
    ).scalar_one()
    assert name == "研发(线上改名)"  # 只增不改:不覆盖线上数据
    # 再次播种(env 密码不同)不改 admin 已设密码
    monkeypatch.setattr(get_settings(), "admin_initial_password", "An0ther!")
    await seed_all(session, get_settings())
    ph = (
        await session.execute(
            text("SELECT password_hash FROM public.sys_user WHERE username='admin'")
        )
    ).scalar_one()
    assert pwd.verify_password("S3cure!", ph)
