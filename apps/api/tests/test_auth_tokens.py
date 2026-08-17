"""令牌生命周期(auth spec「令牌签发与 refresh 轮换」):轮换/复用吊销/登出/类型混用。"""

import asyncio

from httpx import AsyncClient

from tests.conftest import MakeUser


async def _login(client: AsyncClient) -> dict[str, str]:
    r = await client.post("/auth/login", json={"username": "dev", "password": "Passw0rd!"})
    assert r.status_code == 200
    return r.json()


async def test_refresh_rotates_and_old_ticket_invalidated(
    client: AsyncClient, make_user: MakeUser
) -> None:
    await make_user("dev", "Passw0rd!")
    pair = await _login(client)
    r = await client.post("/auth/refresh", json={"refresh_token": pair["refresh_token"]})
    assert r.status_code == 200
    pair2 = r.json()
    assert pair2["refresh_token"] != pair["refresh_token"]
    # 旧票即刻失效
    r = await client.post("/auth/refresh", json={"refresh_token": pair["refresh_token"]})
    assert r.status_code == 401


async def test_refresh_reuse_revokes_whole_family(client: AsyncClient, make_user: MakeUser) -> None:
    await make_user("dev", "Passw0rd!")
    pair1 = await _login(client)
    r = await client.post("/auth/refresh", json={"refresh_token": pair1["refresh_token"]})
    pair2 = r.json()
    # 攻击者持已轮换旧票 R1 再次刷新 → 401 且全家族吊销
    r = await client.post("/auth/refresh", json={"refresh_token": pair1["refresh_token"]})
    assert r.status_code == 401
    # 合法链上的 R2 同样被拒(家族已吊销)
    r = await client.post("/auth/refresh", json={"refresh_token": pair2["refresh_token"]})
    assert r.status_code == 401


async def test_logout_revokes_family(client: AsyncClient, make_user: MakeUser) -> None:
    await make_user("dev", "Passw0rd!")
    pair = await _login(client)
    r = await client.post("/auth/logout", json={"refresh_token": pair["refresh_token"]})
    assert r.status_code == 204
    r = await client.post("/auth/refresh", json={"refresh_token": pair["refresh_token"]})
    assert r.status_code == 401


async def test_refresh_rejects_access_token(client: AsyncClient, make_user: MakeUser) -> None:
    await make_user("dev", "Passw0rd!")
    pair = await _login(client)
    r = await client.post("/auth/refresh", json={"refresh_token": pair["access_token"]})
    assert r.status_code == 401


async def test_me_rejects_refresh_token(client: AsyncClient, make_user: MakeUser) -> None:
    await make_user("dev", "Passw0rd!")
    pair = await _login(client)
    r = await client.get("/auth/me", headers={"Authorization": f"Bearer {pair['refresh_token']}"})
    assert r.status_code == 401


async def test_concurrent_refresh_has_single_winner(
    client: AsyncClient, make_user: MakeUser
) -> None:
    """并发同票刷新:原子轮换保证仅一个胜者;败者按复用检测吊销全家族(spec 契约语义)。"""
    await make_user("dev", "Passw0rd!")
    pair = await _login(client)
    r1, r2 = await asyncio.gather(
        client.post("/auth/refresh", json={"refresh_token": pair["refresh_token"]}),
        client.post("/auth/refresh", json={"refresh_token": pair["refresh_token"]}),
    )
    codes = sorted([r1.status_code, r2.status_code])
    assert codes == [200, 401]  # 不允许双链并存(ADR-003 轮换失效保证)
    # 败者触发复用吊销:胜者的新票同家族,再刷亦 401
    winner = r1 if r1.status_code == 200 else r2
    r = await client.post("/auth/refresh", json={"refresh_token": winner.json()["refresh_token"]})
    assert r.status_code == 401


async def test_refresh_garbage_token(client: AsyncClient) -> None:
    r = await client.post("/auth/refresh", json={"refresh_token": "not-a-jwt"})
    assert r.status_code == 401
