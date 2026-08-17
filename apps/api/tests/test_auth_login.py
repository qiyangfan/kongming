"""登录安全语义(auth spec「账密登录」):无差别 401 / 锁定 / 禁用 / 首登改密。"""

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import MakeUser


async def test_login_success(client: AsyncClient, make_user: MakeUser) -> None:
    await make_user("dev", "Passw0rd!")
    r = await client.post("/auth/login", json={"username": "dev", "password": "Passw0rd!"})
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "Bearer"
    assert body["access_token"] and body["refresh_token"]
    assert body["must_change_password"] is False


async def test_login_wrong_password_and_unknown_user_indistinguishable(
    client: AsyncClient, make_user: MakeUser
) -> None:
    await make_user("dev", "Passw0rd!")
    r1 = await client.post("/auth/login", json={"username": "dev", "password": "wrong"})
    r2 = await client.post("/auth/login", json={"username": "ghost", "password": "wrong"})
    assert r1.status_code == 401 and r2.status_code == 401
    assert r1.json() == r2.json()  # 文案无差别,不泄露账号是否存在


async def test_login_lockout_after_max_fails(client: AsyncClient, make_user: MakeUser) -> None:
    await make_user("dev", "Passw0rd!")
    for _ in range(4):
        r = await client.post("/auth/login", json={"username": "dev", "password": "wrong"})
        assert r.status_code == 401
    r = await client.post("/auth/login", json={"username": "dev", "password": "wrong"})
    assert r.status_code == 429
    assert "锁定" in r.json()["detail"]
    # 锁定期内正确密码亦被拒
    r = await client.post("/auth/login", json={"username": "dev", "password": "Passw0rd!"})
    assert r.status_code == 429


async def test_login_success_resets_fail_counter(client: AsyncClient, make_user: MakeUser) -> None:
    await make_user("dev", "Passw0rd!")
    for _ in range(4):
        await client.post("/auth/login", json={"username": "dev", "password": "wrong"})
    ok = await client.post("/auth/login", json={"username": "dev", "password": "Passw0rd!"})
    assert ok.status_code == 200
    # 计数已清零:再失败 1 次不触发锁定
    r = await client.post("/auth/login", json={"username": "dev", "password": "wrong"})
    assert r.status_code == 401


async def test_login_disabled_forbidden(client: AsyncClient, make_user: MakeUser) -> None:
    await make_user("dev", "Passw0rd!", status="DISABLED")
    r = await client.post("/auth/login", json={"username": "dev", "password": "Passw0rd!"})
    assert r.status_code == 403
    assert "禁用" in r.json()["detail"]


async def test_login_disabled_does_not_count_toward_lockout(
    client: AsyncClient, make_user: MakeUser
) -> None:
    await make_user("dev", "Passw0rd!", status="DISABLED")
    for _ in range(6):
        r = await client.post("/auth/login", json={"username": "dev", "password": "Passw0rd!"})
        assert r.status_code == 403  # 始终 403,不因计数升级为 429


async def test_login_soft_deleted_rejected(
    client: AsyncClient, make_user: MakeUser, session: AsyncSession
) -> None:
    from sqlalchemy import text

    await make_user("dev", "Passw0rd!")
    await session.execute(text("UPDATE public.sys_user SET deleted_at=now() WHERE username='dev'"))
    await session.commit()
    r = await client.post("/auth/login", json={"username": "dev", "password": "Passw0rd!"})
    assert r.status_code == 401  # 软删视同不存在(与未知用户无差别)


async def test_login_must_change_password_flag(client: AsyncClient, make_user: MakeUser) -> None:
    await make_user("dev", "Passw0rd!", password_changed=False)
    r = await client.post("/auth/login", json={"username": "dev", "password": "Passw0rd!"})
    assert r.status_code == 200
    assert r.json()["must_change_password"] is True


async def test_login_updates_last_login_at(
    client: AsyncClient, make_user: MakeUser, session: AsyncSession
) -> None:
    from sqlalchemy import text

    await make_user("dev", "Passw0rd!")
    await client.post("/auth/login", json={"username": "dev", "password": "Passw0rd!"})
    ts = (
        await session.execute(
            text("SELECT last_login_at FROM public.sys_user WHERE username='dev'")
        )
    ).scalar_one()
    assert ts is not None
