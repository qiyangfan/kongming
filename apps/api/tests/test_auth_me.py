"""当前用户信息(auth spec「当前用户信息」):真实角色/权限点/租户/账号状态实时核查。"""

from collections.abc import Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.seed import seed_all
from kongming.config import get_settings
from tests.conftest import MakeUser


async def test_me_returns_real_roles_perms_tenants(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    bearer_headers: Callable[[str], dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uid = await make_user("admin")
    monkeypatch.setattr(get_settings(), "admin_subject", uid)
    await seed_all(session, get_settings())  # 运维超管按 KM_ADMIN_SUBJECT 绑定
    r = await client.get("/auth/me", headers=bearer_headers(uid))
    assert r.status_code == 200
    body = r.json()
    assert body["user_id"] == uid  # 主体键即虎符 sub
    assert "SUPER_ADMIN" in body["role_keys"]
    assert "system:user:manage" in body["perm_codes"]
    assert "system:role:manage" in body["perm_codes"]
    assert any(t["tenant_key"] == "default" for t in body["tenants"])


async def test_me_user_without_roles_gets_empty_lists(
    client: AsyncClient, make_user: MakeUser, bearer_headers: Callable[[str], dict[str, str]]
) -> None:
    uid = await make_user("dev")
    r = await client.get("/auth/me", headers=bearer_headers(uid))
    assert r.status_code == 200  # 认证与授权分离:无角色不阻断查询本身
    body = r.json()
    assert body["role_keys"] == [] and body["perm_codes"] == [] and body["tenants"] == []


async def test_me_disabled_user_rejected_live(
    client: AsyncClient,
    make_user: MakeUser,
    session: AsyncSession,
    bearer_headers: Callable[[str], dict[str, str]],
) -> None:
    uid = await make_user("dev")
    await session.execute(text("UPDATE public.sys_user SET status='DISABLED' WHERE username='dev'"))
    await session.commit()
    r = await client.get("/auth/me", headers=bearer_headers(uid))
    assert r.status_code == 401


async def test_me_soft_deleted_user_rejected_live(
    client: AsyncClient,
    make_user: MakeUser,
    session: AsyncSession,
    bearer_headers: Callable[[str], dict[str, str]],
) -> None:
    uid = await make_user("dev")
    await session.execute(text("UPDATE public.sys_user SET deleted_at=now() WHERE username='dev'"))
    await session.commit()
    r = await client.get("/auth/me", headers=bearer_headers(uid))
    assert r.status_code == 401


async def test_me_perms_exclude_disabled_role(
    client: AsyncClient,
    make_user: MakeUser,
    session: AsyncSession,
    bearer_headers: Callable[[str], dict[str, str]],
) -> None:
    """未启用角色不授予功能权限——与数据范围侧同一道闸。

    原实现只让 `status` 影响数据范围解析、不影响权限点联查,于是「禁用角色」这个
    治理动作的实际后果是:权限照旧、行级过滤整条消失 → 可见面反而放宽成全表。
    两侧同闸后,禁用的效果只能是收紧。
    """
    await seed_all(session, get_settings())
    uid = await make_user("half")
    rid = (
        await session.execute(
            text(
                "INSERT INTO public.sys_role (id, role_key, role_name, data_scope)"
                " VALUES (gen_random_uuid(), 'R_HALF', 'R_HALF', 'SELF') RETURNING id"
            )
        )
    ).scalar_one()
    pid = (
        await session.execute(
            text("SELECT id FROM public.sys_permission WHERE perm_code = 'system:user:manage'")
        )
    ).scalar_one()
    await session.execute(
        text("INSERT INTO public.sys_role_permission (role_id, permission_id) VALUES (:r, :p)"),
        {"r": rid, "p": pid},
    )
    await session.execute(
        text("INSERT INTO public.sys_user_role (user_id, role_id) VALUES (:u, :r)"),
        {"u": uid, "r": rid},
    )
    await session.commit()

    headers = bearer_headers(uid)
    body = (await client.get("/auth/me", headers=headers)).json()
    assert "system:user:manage" in body["perm_codes"]

    await session.execute(
        text("UPDATE public.sys_role SET status = 'DISABLED' WHERE id = :r"), {"r": rid}
    )
    await session.commit()

    after = (await client.get("/auth/me", headers=headers)).json()
    assert after["perm_codes"] == [], "禁用角色的权限点必须一并失效"
    assert (await client.get("/system/users", headers=headers)).status_code == 403


async def test_me_without_token_401(client: AsyncClient) -> None:
    assert (await client.get("/auth/me")).status_code == 401


async def test_me_with_garbage_token_401(client: AsyncClient) -> None:
    r = await client.get("/auth/me", headers={"Authorization": "Bearer garbage"})
    assert r.status_code == 401
