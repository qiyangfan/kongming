"""自助改密(auth spec「自助改密」):哈希更新/标志消除/全家族吊销/异常分支。"""

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth import password as pwd
from tests.conftest import MakeUser


async def _login(client: AsyncClient, password: str = "Passw0rd!") -> dict[str, str]:
    r = await client.post("/auth/login", json={"username": "dev", "password": password})
    assert r.status_code == 200
    return r.json()


async def test_change_password_ok(
    client: AsyncClient, make_user: MakeUser, session: AsyncSession
) -> None:
    await make_user("dev", "Passw0rd!", password_changed=False)
    pair = await _login(client)
    r = await client.post(
        "/auth/password/change",
        json={"current_password": "Passw0rd!", "new_password": "N3wPass!!"},
        headers={"Authorization": f"Bearer {pair['access_token']}"},
    )
    assert r.status_code == 204
    row = (
        await session.execute(
            text(
                "SELECT password_hash, password_changed_at FROM public.sys_user"
                " WHERE username='dev'"
            )
        )
    ).one()
    assert pwd.verify_password("N3wPass!!", row.password_hash)
    assert row.password_changed_at is not None  # 首登改密标志消除


async def test_change_password_revokes_all_families(
    client: AsyncClient, make_user: MakeUser
) -> None:
    await make_user("dev", "Passw0rd!")
    pair = await _login(client)
    # 另一"设备"也登录
    pair2 = await _login(client)
    r = await client.post(
        "/auth/password/change",
        json={"current_password": "Passw0rd!", "new_password": "N3wPass!!"},
        headers={"Authorization": f"Bearer {pair['access_token']}"},
    )
    assert r.status_code == 204
    # 两个会话的 refresh 家族均被吊销
    for p in (pair, pair2):
        r = await client.post("/auth/refresh", json={"refresh_token": p["refresh_token"]})
        assert r.status_code == 401
    # 新密码可登录且不再强制改密
    r = await client.post("/auth/login", json={"username": "dev", "password": "N3wPass!!"})
    assert r.status_code == 200
    assert r.json()["must_change_password"] is False
    # 旧密码不可登录
    r = await client.post("/auth/login", json={"username": "dev", "password": "Passw0rd!"})
    assert r.status_code == 401


async def test_change_password_wrong_current(client: AsyncClient, make_user: MakeUser) -> None:
    await make_user("dev", "Passw0rd!")
    pair = await _login(client)
    r = await client.post(
        "/auth/password/change",
        json={"current_password": "wrong", "new_password": "N3wPass!!"},
        headers={"Authorization": f"Bearer {pair['access_token']}"},
    )
    assert r.status_code == 400
    # 密码不变:旧密码仍可登录
    r = await client.post("/auth/login", json={"username": "dev", "password": "Passw0rd!"})
    assert r.status_code == 200


async def test_change_password_federated_without_local_password(
    client: AsyncClient, make_user: MakeUser
) -> None:
    # 纯联邦账号:无 password_hash。直接造 access token 走 /auth/me 同款签发
    uid = await make_user("dev", None)
    from kongming.auth import tokens

    access = tokens.issue(uid, "access")
    r = await client.post(
        "/auth/password/change",
        json={"current_password": "whatever", "new_password": "N3wPass!!"},
        headers={"Authorization": f"Bearer {access}"},
    )
    assert r.status_code == 400
    assert "无本地密码" in r.json()["detail"]


async def test_change_password_requires_auth(client: AsyncClient) -> None:
    r = await client.post(
        "/auth/password/change",
        json={"current_password": "a", "new_password": "b"},
    )
    assert r.status_code == 401
