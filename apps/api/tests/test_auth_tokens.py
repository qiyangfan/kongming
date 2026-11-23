"""令牌生命周期(auth spec「令牌签发与 refresh 轮换」):轮换/复用吊销/登出/类型混用。

孔明已无本地登录入口(ADR-0010),凭据经联邦回调签发;此处用固定 profile 的
Keycloak桩走完整回调链,保证被测的 refresh 语义面对的是真实签发的令牌对。
"""

import asyncio
from collections.abc import Callable
from typing import cast
from urllib.parse import parse_qs, urlparse

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.models import NormalizedProfile
from kongming.auth.seed import seed_all
from kongming.config import get_settings

PROFILE = NormalizedProfile(
    external_id="01a1f8e2-0000-7000-8000-0000000000aa",
    username="dev",
    display_name="Dev",
    email=None,
    email_verified=False,
    avatar_url=None,
)


async def _login(
    client: AsyncClient, fake_keycloak: Callable[[NormalizedProfile], None]
) -> dict[str, str]:
    fake_keycloak(PROFILE)
    r = await client.get("/auth/keycloak/start")
    start_body = cast("dict[str, str]", r.json())
    state = parse_qs(urlparse(start_body["authorize_url"]).query)["state"][0]
    r = await client.post("/auth/keycloak/callback", json={"code": "c0de", "state": state})
    assert r.status_code == 200
    body: dict[str, str] = r.json()
    return body


async def test_refresh_rotates_and_old_ticket_invalidated(
    client: AsyncClient,
    session: AsyncSession,
    fake_keycloak: Callable[[NormalizedProfile], None],
) -> None:
    await seed_all(session, get_settings())  # 联邦建号依赖默认角色/租户
    pair = await _login(client, fake_keycloak)
    r = await client.post("/auth/refresh", json={"refresh_token": pair["refresh_token"]})
    assert r.status_code == 200
    pair2 = r.json()
    assert pair2["refresh_token"] != pair["refresh_token"]
    # 旧票即刻失效
    r = await client.post("/auth/refresh", json={"refresh_token": pair["refresh_token"]})
    assert r.status_code == 401


async def test_refresh_reuse_revokes_whole_family(
    client: AsyncClient,
    session: AsyncSession,
    fake_keycloak: Callable[[NormalizedProfile], None],
) -> None:
    await seed_all(session, get_settings())  # 联邦建号依赖默认角色/租户
    pair1 = await _login(client, fake_keycloak)
    r = await client.post("/auth/refresh", json={"refresh_token": pair1["refresh_token"]})
    pair2 = r.json()
    # 已轮换的 refresh token 再次使用时，拒绝请求并吊销整个 token 家族。
    r = await client.post("/auth/refresh", json={"refresh_token": pair1["refresh_token"]})
    assert r.status_code == 401
    # 同一 token 家族中的其他 refresh token 也随之失效。
    r = await client.post("/auth/refresh", json={"refresh_token": pair2["refresh_token"]})
    assert r.status_code == 401


async def test_logout_revokes_family(
    client: AsyncClient,
    session: AsyncSession,
    fake_keycloak: Callable[[NormalizedProfile], None],
) -> None:
    await seed_all(session, get_settings())  # 联邦建号依赖默认角色/租户
    pair = await _login(client, fake_keycloak)
    r = await client.post("/auth/logout", json={"refresh_token": pair["refresh_token"]})
    assert r.status_code == 204
    r = await client.post("/auth/refresh", json={"refresh_token": pair["refresh_token"]})
    assert r.status_code == 401


async def test_refresh_rejects_access_token(
    client: AsyncClient,
    session: AsyncSession,
    fake_keycloak: Callable[[NormalizedProfile], None],
) -> None:
    await seed_all(session, get_settings())  # 联邦建号依赖默认角色/租户
    pair = await _login(client, fake_keycloak)
    r = await client.post("/auth/refresh", json={"refresh_token": pair["access_token"]})
    assert r.status_code == 401


async def test_me_rejects_refresh_token(
    client: AsyncClient,
    session: AsyncSession,
    fake_keycloak: Callable[[NormalizedProfile], None],
) -> None:
    await seed_all(session, get_settings())  # 联邦建号依赖默认角色/租户
    pair = await _login(client, fake_keycloak)
    r = await client.get("/auth/me", headers={"Authorization": f"Bearer {pair['refresh_token']}"})
    assert r.status_code == 401


async def test_concurrent_refresh_has_single_winner(
    client: AsyncClient,
    session: AsyncSession,
    fake_keycloak: Callable[[NormalizedProfile], None],
) -> None:
    """并发同票刷新:原子轮换保证仅一个胜者;败者按复用检测吊销全家族(spec 契约语义)。"""
    await seed_all(session, get_settings())  # 联邦建号依赖默认角色/租户
    pair = await _login(client, fake_keycloak)
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
