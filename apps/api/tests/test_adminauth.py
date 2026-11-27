# pyright: reportPrivateUsage=false
"""管理台认证回归:独立 client/cookie/30m 会话、角色闸与会话隔离。

IdP 用非 OIDC 桩(只替换 profile 来源),state 一次性与建号规则走真实实现。
"""

from typing import Any, cast

import jwt
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.adminauth.provider import AdminKeycloakProvider
from kongming.auth.models import NormalizedProfile, RequiredRoleMissing
from kongming.auth.seed import seed_all
from kongming.config import Settings, get_settings


class _FakeAdminProvider:
    key = "keycloak"
    display_name = "统一身份(管理台)"
    pkce = False
    oidc = False

    def configured(self, settings: object) -> bool:
        return True

    def authorize_url(
        self, settings: object, *, state: str, code_challenge: str | None = None
    ) -> str:
        return f"https://idp.example/authorize?state={state}"

    async def fetch_profile(
        self, settings: object, *, code: str, code_verifier: str | None = None
    ) -> NormalizedProfile:
        return NormalizedProfile(
            external_id="00000000-0000-7000-8000-00000000ad01",
            username="admin",
            display_name="管理员",
            email=None,
            email_verified=False,
            avatar_url=None,
        )


@pytest.fixture
def fake_admin_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    import kongming.adminauth.router as admin_router

    monkeypatch.setattr(admin_router, "_registry", {"keycloak": cast(Any, _FakeAdminProvider())})


def test_admin_provider_requires_client_role() -> None:
    settings = get_settings()
    provider = AdminKeycloakProvider(required_role="kongming-admin", role_client_id="kw")
    with pytest.raises(RequiredRoleMissing):
        provider._require_role(settings, {"realm_access": {"roles": ["DEVELOPER"]}})
    provider._require_role(settings, {"resource_access": {"kw": {"roles": ["kongming-admin"]}}})
    provider._require_role(settings, {"realm_access": {"roles": ["kongming-admin"]}})


def test_admin_provider_reads_required_role_from_settings() -> None:
    settings = Settings(admin_keycloak_required_role="kongming-admin")
    provider = AdminKeycloakProvider()
    with pytest.raises(RequiredRoleMissing):
        provider._require_role(settings, {"realm_access": {"roles": ["DEVELOPER"]}})
    provider._require_role(settings, {"realm_access": {"roles": ["kongming-admin"]}})


async def test_admin_login_refresh_logout_flow(
    client: AsyncClient, session: AsyncSession, fake_admin_provider: None
) -> None:
    await seed_all(session, get_settings())
    r = await client.get("/admin-auth/keycloak/start")
    assert r.status_code == 200, r.text
    url = r.json()["authorize_url"]
    state = url.split("state=")[1]
    assert "km_admin_oidc_state" in r.cookies

    r = await client.post("/admin-auth/keycloak/callback", json={"code": "c", "state": state})
    assert r.status_code == 200, r.text
    pair = r.json()
    assert pair["access_token"]
    claims = jwt.decode(pair["access_token"], options={"verify_signature": False})
    assert int(claims["exp"]) - int(claims["iat"]) == get_settings().admin_access_ttl_sec
    refresh_claims = jwt.decode(pair["refresh_token"], options={"verify_signature": False})
    assert refresh_claims["surf"] == "admin"
    session_ttl = get_settings().admin_session_ttl_sec
    assert int(refresh_claims["abs_exp"]) - int(refresh_claims["iat"]) in (
        session_ttl - 1,
        session_ttl,
    )
    set_cookie = r.headers.get("set-cookie", "")
    assert "km_admin_refresh=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "Max-Age=1800" in set_cookie
    assert "SameSite=lax" in set_cookie.lower() or "samesite=lax" in set_cookie.lower()

    me = await client.get(
        "/admin-auth/me", headers={"Authorization": f"Bearer {pair['access_token']}"}
    )
    assert me.status_code == 200, me.text

    refreshed = await client.post("/admin-auth/refresh")
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["refresh_token"] != pair["refresh_token"]

    out = await client.post("/admin-auth/logout")
    assert out.status_code == 204
    again = await client.post("/admin-auth/refresh")
    assert again.status_code == 401


async def test_admin_session_not_accepted_as_user_refresh(
    client: AsyncClient, session: AsyncSession, fake_admin_provider: None
) -> None:
    await seed_all(session, get_settings())
    r = await client.get("/admin-auth/keycloak/start")
    state = r.json()["authorize_url"].split("state=")[1]
    callback = await client.post(
        "/admin-auth/keycloak/callback", json={"code": "c", "state": state}
    )
    assert callback.status_code == 200
    admin_refresh = callback.json()["refresh_token"]
    # 用户面刷新只认 km_refresh;管理台 refresh cookie 不构成用户会话
    r = await client.post("/auth/refresh")
    assert r.status_code == 401
    # 即使把管理台 refresh 票据塞进用户面 cookie,认证面不匹配仍拒绝
    client.cookies.set("km_refresh", admin_refresh)
    crossed = await client.post("/auth/refresh")
    assert crossed.status_code == 401


async def test_admin_callback_state_must_match_browser(
    client: AsyncClient, session: AsyncSession, fake_admin_provider: None
) -> None:
    await seed_all(session, get_settings())
    r = await client.post("/admin-auth/keycloak/callback", json={"code": "c", "state": "forged"})
    assert r.status_code == 401
