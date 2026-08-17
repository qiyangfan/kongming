"""当前用户信息(auth spec「当前用户信息」):真实角色/权限点/租户/账号状态实时核查。"""

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.seed import seed_all
from kongming.config import get_settings
from tests.conftest import MakeUser


async def _login(client: AsyncClient, username: str, password: str) -> dict[str, str]:
    r = await client.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200
    return r.json()


async def test_me_returns_real_roles_perms_tenants(
    client: AsyncClient, session: AsyncSession
) -> None:
    await seed_all(session, get_settings())
    pair = await _login(client, "admin", "admin123")  # 种子缺省密码
    r = await client.get("/auth/me", headers={"Authorization": f"Bearer {pair['access_token']}"})
    assert r.status_code == 200
    body = r.json()
    assert body["username"] == "admin"
    assert "SUPER_ADMIN" in body["role_keys"]
    assert "system:user:manage" in body["perm_codes"]
    assert "system:role:manage" in body["perm_codes"]
    assert any(t["tenant_key"] == "default" for t in body["tenants"])
    # 缺省密码播种 → 强制首登改密
    assert body["must_change_password"] is True


async def test_me_user_without_roles_gets_empty_lists(
    client: AsyncClient, make_user: MakeUser
) -> None:
    await make_user("dev", "Passw0rd!")
    pair = await _login(client, "dev", "Passw0rd!")
    r = await client.get("/auth/me", headers={"Authorization": f"Bearer {pair['access_token']}"})
    assert r.status_code == 200  # 认证与授权分离:无角色不阻断查询本身
    body = r.json()
    assert body["role_keys"] == [] and body["perm_codes"] == [] and body["tenants"] == []


async def test_me_disabled_user_rejected_live(
    client: AsyncClient, make_user: MakeUser, session: AsyncSession
) -> None:
    await make_user("dev", "Passw0rd!")
    pair = await _login(client, "dev", "Passw0rd!")
    await session.execute(text("UPDATE public.sys_user SET status='DISABLED' WHERE username='dev'"))
    await session.commit()
    r = await client.get("/auth/me", headers={"Authorization": f"Bearer {pair['access_token']}"})
    assert r.status_code == 401


async def test_me_soft_deleted_user_rejected_live(
    client: AsyncClient, make_user: MakeUser, session: AsyncSession
) -> None:
    await make_user("dev", "Passw0rd!")
    pair = await _login(client, "dev", "Passw0rd!")
    await session.execute(text("UPDATE public.sys_user SET deleted_at=now() WHERE username='dev'"))
    await session.commit()
    r = await client.get("/auth/me", headers={"Authorization": f"Bearer {pair['access_token']}"})
    assert r.status_code == 401


async def test_me_without_token_401(client: AsyncClient) -> None:
    assert (await client.get("/auth/me")).status_code == 401


async def test_me_with_garbage_token_401(client: AsyncClient) -> None:
    r = await client.get("/auth/me", headers={"Authorization": "Bearer garbage"})
    assert r.status_code == 401
