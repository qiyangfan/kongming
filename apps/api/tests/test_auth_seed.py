"""启动种子(rbac spec「种子数据」):齐备/幂等/只增不改/超管按Keycloak sub 绑定。

种子不创建任何可登录账号(ADR-0010):超管身份只能来自 `KM_ADMIN_SUBJECT`
指向的、Keycloak已建过档的 sys_user 行。
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.seed import seed_all
from kongming.config import get_settings
from tests.conftest import MakeUser


async def test_seed_empty_db_creates_everything(session: AsyncSession) -> None:
    await seed_all(session, get_settings())
    roles = (
        await session.execute(text("SELECT role_key, sort FROM public.sys_role ORDER BY sort"))
    ).all()
    assert [(r.role_key, r.sort) for r in roles] == [("SUPER_ADMIN", 1), ("DEVELOPER", 2)]
    perms = (
        (await session.execute(text("SELECT perm_code FROM public.sys_permission ORDER BY 1")))
        .scalars()
        .all()
    )
    assert perms == [
        "bot:run",
        "chat:execute",
        "chat:feedback:submit",
        # 仅超管种子覆盖,不进 DEVELOPER 推荐集(治理面+显式授予制)
        "connector:manage",
        "developer:portal",
        "report:export",
        "report:generate",
        "report:read",
        "system:audit:view",
        "system:billing:manage",
        # 仅超管种子覆盖,不进 DEVELOPER 推荐集
        "system:chat-quality:view",
        # 控制面状态与本地命令(管理台)
        "system:control:manage",
        "system:control:view",
        "system:datasource:manage",
        "system:document:manage",
        "system:feedback:handle",
        "system:feedback:view",
        "system:guardrail:manage",
        "system:llm-usage:view",
        "system:mcp:manage",
        "system:rag:manage",
        "system:role:manage",
        "system:semantic:manage",
        "system:skill:manage",
        "system:team:manage",
        "system:tool:manage",
        "system:trigger:manage",
        "system:user:manage",
        "system:workflow:manage",
        "task:create",
        "task:manage",
        "task:read",
        "tool:announcement:create",
        "tool:code:execute",
        "tool:compute",
        # 仅超管种子覆盖,不进 DEVELOPER 推荐集(治理面+显式授予制)
        "tool:http:call",
        "tool:mcp:use",
        "tool:rag:retrieve",
        "tool:sql:execute",
        "tool:sql:validate",
        # 仅超管种子覆盖,不进 DEVELOPER 推荐集(显式授予制)
        "tool:websearch:search",
        "workflow:execute",
    ]
    tenants = (
        (await session.execute(text("SELECT tenant_key FROM public.sys_tenant"))).scalars().all()
    )
    assert tenants == ["default"]
    # 只播种配置的Keycloak sub 授权资料,不创建可登录账号
    assert (await session.execute(text("SELECT count(*) FROM public.sys_user"))).scalar_one() == 1
    assert (
        await session.execute(
            text("SELECT id FROM public.sys_user WHERE id=:i"),
            {"i": get_settings().admin_subject},
        )
    ).scalar_one() is not None
    assert (
        await session.execute(
            text(
                "SELECT count(*) FROM public.sys_role_permission rp"
                " JOIN public.sys_role r ON r.id=rp.role_id"
                " WHERE r.role_key='SUPER_ADMIN'"
            )
        )
    ).scalar_one() == 42  # 全量权限点数(见 seed.PERMISSIONS)
    # DEVELOPER 默认绑定聊天执行、反馈提交和三项只读权限;公告写工具需显式授予。
    dev_perms = (
        (
            await session.execute(
                text(
                    "SELECT p.perm_code FROM public.sys_role_permission rp"
                    " JOIN public.sys_role r ON r.id=rp.role_id"
                    " JOIN public.sys_permission p ON p.id=rp.permission_id"
                    " WHERE r.role_key='DEVELOPER' ORDER BY 1"
                )
            )
        )
        .scalars()
        .all()
    )
    assert list(dev_perms) == [
        "bot:run",
        "chat:execute",
        "chat:feedback:submit",
        "developer:portal",
        "report:export",
        "report:generate",
        "report:read",
        "task:create",
        "task:manage",
        "task:read",
        "tool:compute",
        "tool:rag:retrieve",
        "tool:sql:execute",
        "tool:sql:validate",
        "workflow:execute",
    ]


async def test_seed_idempotent_on_repeat(
    session: AsyncSession, make_user: MakeUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    uid = await make_user("alice")
    monkeypatch.setattr(get_settings(), "admin_subject", uid)
    await seed_all(session, get_settings())
    await seed_all(session, get_settings())

    async def _count(table: str) -> int:
        return (await session.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()  # noqa: S608  # 常量表名

    assert await _count("public.sys_user") == 1
    assert await _count("public.sys_role") == 2
    assert await _count("public.sys_permission") == 42
    assert await _count("public.sys_tenant") == 1
    assert await _count("public.sys_user_role") == 1
    assert await _count("public.sys_user_tenant") == 1
    # 超管全量 42 + DEVELOPER 推荐 15
    assert await _count("public.sys_role_permission") == 57


async def test_seed_binds_admin_subject(
    session: AsyncSession, make_user: MakeUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    uid = await make_user("ops")
    monkeypatch.setattr(get_settings(), "admin_subject", uid)
    await seed_all(session, get_settings())
    assert (
        await session.execute(
            text(
                "SELECT count(*) FROM public.sys_user_role ur"
                " JOIN public.sys_role r ON r.id=ur.role_id"
                " WHERE ur.user_id=:uid AND r.role_key='SUPER_ADMIN'"
            ),
            {"uid": uid},
        )
    ).scalar_one() == 1
    assert (
        await session.execute(
            text(
                "SELECT count(*) FROM public.sys_user_tenant ut"
                " JOIN public.sys_tenant t ON t.id=ut.tenant_id"
                " WHERE ut.user_id=:uid AND t.tenant_key='default'"
            ),
            {"uid": uid},
        )
    ).scalar_one() == 1


async def test_seed_precreates_configured_admin_subject(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    subject = "01a1f8e2-0000-7000-8000-00000000dead"
    monkeypatch.setattr(get_settings(), "admin_subject", subject)
    await seed_all(session, get_settings())
    # 只预建该Keycloak sub 的授权资料与身份映射,无本地密码
    assert (await session.execute(text("SELECT count(*) FROM public.sys_user"))).scalar_one() == 1
    assert (
        await session.execute(
            text(
                "SELECT count(*) FROM public.user_identities"
                " WHERE provider='KEYCLOAK' AND external_id=:s"
            ),
            {"s": subject},
        )
    ).scalar_one() == 1
    assert (
        await session.execute(text("SELECT count(*) FROM public.sys_user_role"))
    ).scalar_one() == 1


async def test_seed_never_overwrites_existing_rows(
    session: AsyncSession, make_user: MakeUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 预置被线上改过的角色名
    await session.execute(
        text(
            "INSERT INTO public.sys_role (id, role_key, role_name, data_scope, sql_scope)"
            " VALUES (gen_random_uuid(), 'DEVELOPER', '研发(线上改名)', 'DEPT', 'READ_DDL')"
        )
    )
    await session.commit()
    uid = await make_user("ops")
    monkeypatch.setattr(get_settings(), "admin_subject", uid)
    await seed_all(session, get_settings())
    name = (
        await session.execute(
            text("SELECT role_name FROM public.sys_role WHERE role_key='DEVELOPER'")
        )
    ).scalar_one()
    assert name == "研发(线上改名)"  # 只增不改:不覆盖线上数据
    # 再次播种(超管配置改到别人)保留既有超管绑定,不摘除
    other = await make_user("ops2")
    monkeypatch.setattr(get_settings(), "admin_subject", other)
    await seed_all(session, get_settings())
    assert (
        await session.execute(
            text(
                "SELECT count(*) FROM public.sys_user_role ur"
                " JOIN public.sys_role r ON r.id=ur.role_id"
                " WHERE ur.user_id=:uid AND r.role_key='SUPER_ADMIN'"
            ),
            {"uid": uid},
        )
    ).scalar_one() == 1
