"""Keycloak OIDC provider:发现文档/JWKS/nonce/state 一次性,以及禁止 email 关联。

孔明登录入口只有 Keycloak(ADR-0010):这里同时覆盖 provider 协议层与联邦回调链——
主体键等于 Keycloak sub、禁用账号拒绝、其它 provider(含已退役 hufu)路由不存在。
"""

import base64
import time
from collections.abc import Callable
from typing import Any, cast
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.models import NormalizedProfile, ProviderExchangeFailed
from kongming.auth.providers.keycloak import KeycloakProvider
from kongming.auth.router import REFRESH_COOKIE
from kongming.auth.seed import seed_all
from kongming.config import get_settings
from tests.conftest import MakeUser


def _cookie(token: str) -> dict[str, str]:
    """刷新/登出只认 HttpOnly cookie;按浏览器形态发 Cookie 头(端点不认 body)。"""
    return {"Cookie": f"{REFRESH_COOKIE}={token}"}


ISSUER = "http://idp.test"
KID = "k1"


def _b64(n: int) -> str:
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _rsa_pair(kid: str = KID) -> tuple[Any, dict[str, Any]]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = key.public_key().public_numbers()
    jwk = {
        "kty": "RSA",
        "kid": kid,
        "alg": "RS256",
        "use": "sig",
        "n": _b64(numbers.n),
        "e": _b64(numbers.e),
    }
    return key, jwk


def _configure(monkeypatch: pytest.MonkeyPatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "keycloak_issuer", ISSUER)
    monkeypatch.setattr(s, "keycloak_client_id", "kongming-keycloak")
    monkeypatch.setattr(s, "keycloak_client_secret", "test-secret")
    monkeypatch.setattr(s, "keycloak_redirect_uri", "http://localhost:5173/auth/keycloak/callback")


def _metadata() -> dict[str, Any]:
    return {
        "issuer": ISSUER,
        "authorization_endpoint": f"{ISSUER}/authorize",
        "token_endpoint": f"{ISSUER}/token",
        "jwks_uri": f"{ISSUER}/jwks",
    }


def _provider(monkeypatch: pytest.MonkeyPatch, claims: dict[str, Any]) -> KeycloakProvider:
    _configure(monkeypatch)
    key, jwk = _rsa_pair()
    id_token = jwt.encode(claims, key, algorithm="RS256", headers={"kid": KID})

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, json=_metadata())
        if req.url.path == "/token":
            return httpx.Response(200, json={"id_token": id_token})
        if req.url.path == "/jwks":
            return httpx.Response(200, json={"keys": [jwk]})
        return httpx.Response(404)

    return KeycloakProvider(transport=httpx.MockTransport(handler))


def _claims(nonce: str = "n-1", aud: str = "kongming-keycloak") -> dict[str, Any]:
    now = int(time.time())
    return {
        "iss": ISSUER,
        "aud": aud,
        "sub": "01a1f8e2-0000-7000-8000-000000000001",
        "exp": now + 300,
        "iat": now,
        "nonce": nonce,
        "preferred_username": "alice",
        "name": "Alice",
        "email": "alice@example.com",
        "email_verified": False,
    }


async def test_authorize_url_carries_nonce_and_pkce(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _provider(monkeypatch, _claims())
    await provider.prepare(get_settings())
    url = provider.authorize_url(get_settings(), state="st", code_challenge="ch", nonce="n-1")
    q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
    assert url.startswith(f"{ISSUER}/authorize")
    assert q["response_type"] == "code"
    assert q["nonce"] == "n-1"
    assert q["code_challenge"] == "ch"
    assert q["code_challenge_method"] == "S256"


async def test_fetch_profile_verifies_id_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _provider(monkeypatch, _claims())
    profile = await provider.fetch_profile(get_settings(), code="c", code_verifier="v", nonce="n-1")
    assert profile.external_id == "01a1f8e2-0000-7000-8000-000000000001"
    assert profile.username == "alice"
    assert profile.display_name == "Alice"
    assert profile.email_verified is False


async def test_nonce_mismatch_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(monkeypatch, _claims(nonce="other"))
    with pytest.raises(ProviderExchangeFailed):
        await provider.fetch_profile(get_settings(), code="c", code_verifier="v", nonce="n-1")


async def test_missing_nonce_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(monkeypatch, _claims())
    with pytest.raises(ProviderExchangeFailed):
        await provider.fetch_profile(get_settings(), code="c", code_verifier="v", nonce=None)


async def test_audience_mismatch_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(monkeypatch, _claims(aud="someone-else"))
    with pytest.raises(ProviderExchangeFailed):
        await provider.fetch_profile(get_settings(), code="c", code_verifier="v", nonce="n-1")


async def test_options_lists_only_keycloak_when_configured(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch)
    r = await client.get("/auth/options")
    assert r.status_code == 200
    assert [p["provider"] for p in r.json()["providers"]] == ["keycloak"]


async def test_options_empty_when_keycloak_unconfigured(client: AsyncClient) -> None:
    r = await client.get("/auth/options")
    assert r.status_code == 200
    assert r.json()["providers"] == []


@pytest.mark.parametrize("provider", ["hufu", "github", "google", "feishu", "wecom", "dingtalk"])
async def test_removed_providers_not_registered(client: AsyncClient, provider: str) -> None:
    start = await client.get(f"/auth/{provider}/start")
    assert start.status_code == 404
    callback = await client.post(f"/auth/{provider}/callback", json={"code": "c0de", "state": "st"})
    assert callback.status_code == 404


async def test_local_login_and_password_change_removed(mcp_client: AsyncClient) -> None:
    # 带 lifespan 的客户端:根路径挂载的 MCP 入站在未就绪时会把未知路径兜成 503
    r = await mcp_client.post("/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code in {404, 405}
    r = await mcp_client.post(
        "/auth/password/change", json={"old_password": "a", "new_password": "bbbbbbbb"}
    )
    assert r.status_code in {404, 405}


async def _start(client: AsyncClient, provider: str = "keycloak") -> str:
    r = await client.get(f"/auth/{provider}/start")
    authorize_url = cast("str", r.json()["authorize_url"])
    return parse_qs(urlparse(authorize_url).query)["state"][0]


async def _callback(
    client: AsyncClient, state: str, *, provider: str = "keycloak"
) -> httpx.Response:
    return await client.post(f"/auth/{provider}/callback", json={"code": "c0de", "state": state})


PROFILE = NormalizedProfile(
    external_id="01a1f8e2-0000-7000-8000-000000000001",
    username="alice",
    display_name="Alice",
    email="shared@example.com",
    email_verified=True,
    avatar_url=None,
)


async def test_federation_creates_user_keyed_by_subject(
    client: AsyncClient,
    fake_keycloak: Callable[[NormalizedProfile], None],
    session: AsyncSession,
    bearer_headers: Callable[[str], dict[str, str]],
) -> None:
    await seed_all(session, get_settings())
    fake_keycloak(PROFILE)
    state = await _start(client)
    r = await _callback(client, state)
    assert r.status_code == 200
    me = await client.get("/auth/me", headers=bearer_headers(PROFILE.external_id))
    assert me.status_code == 200
    assert me.json()["user_id"] == PROFILE.external_id  # sys_user.id 即Keycloak sub
    assert me.json()["username"] == "alice-keycloak"
    assert me.json()["role_keys"] == ["DEVELOPER"]
    bound = (
        await session.execute(
            text(
                "SELECT user_id FROM public.user_identities"
                " WHERE provider='KEYCLOAK' AND external_id=:e"
            ),
            {"e": PROFILE.external_id},
        )
    ).scalar_one()
    assert str(bound) == PROFILE.external_id


async def test_federation_is_idempotent_for_same_subject(
    client: AsyncClient,
    fake_keycloak: Callable[[NormalizedProfile], None],
    session: AsyncSession,
) -> None:
    await seed_all(session, get_settings())
    fake_keycloak(PROFILE)
    assert (await _callback(client, await _start(client))).status_code == 200
    assert (await _callback(client, await _start(client))).status_code == 200
    assert (
        await session.execute(
            text("SELECT count(*) FROM public.sys_user WHERE id=:i"), {"i": PROFILE.external_id}
        )
    ).scalar_one() == 1


async def test_federation_never_links_by_email(
    client: AsyncClient,
    fake_keycloak: Callable[[NormalizedProfile], None],
    make_user: MakeUser,
    session: AsyncSession,
) -> None:
    await seed_all(session, get_settings())
    existing = await make_user("localdev", email="shared@example.com")
    fake_keycloak(PROFILE)
    r = await _callback(client, await _start(client))
    assert r.status_code == 200
    me = await client.get(
        "/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"}
    )
    assert me.json()["user_id"] != existing  # 不按 email 合并
    bound = (
        await session.execute(
            text(
                "SELECT user_id FROM public.user_identities"
                " WHERE provider='KEYCLOAK' AND external_id=:e"
            ),
            {"e": PROFILE.external_id},
        )
    ).scalar_one()
    assert str(bound) != existing


async def test_federation_rejects_disabled_account(
    client: AsyncClient,
    fake_keycloak: Callable[[NormalizedProfile], None],
    make_user: MakeUser,
    session: AsyncSession,
) -> None:
    await seed_all(session, get_settings())
    uid = await make_user("disabled-user", status="DISABLED")
    await session.execute(
        text(
            "INSERT INTO public.user_identities (id, user_id, provider, external_id)"
            " VALUES (gen_random_uuid(), :u, 'KEYCLOAK', :e)"
        ),
        {"u": uid, "e": PROFILE.external_id},
    )
    await session.commit()
    fake_keycloak(PROFILE)
    r = await _callback(client, await _start(client))
    assert r.status_code == 403


def _cookie_path(set_cookie: list[str], name: str) -> str | None:
    for raw in set_cookie:
        parts = [part.strip() for part in raw.split(";")]
        if parts[0].startswith(f"{name}="):
            for attr in parts[1:]:
                if attr.lower().startswith("path="):
                    return attr.split("=", 1)[1]
    return None


def _path_covers(cookie_path: str, request_path: str) -> bool:
    """RFC 6265 path-match:请求路径是否落在 cookie Path 内。"""
    if request_path == cookie_path:
        return True
    if not request_path.startswith(cookie_path):
        return False
    return cookie_path.endswith("/") or request_path[len(cookie_path)] == "/"


async def test_auth_cookies_cover_spa_api_prefix(
    client: AsyncClient, fake_keycloak: Callable[[NormalizedProfile], None], session: AsyncSession
) -> None:
    """前端统一经 /api 前缀调用:state/refresh cookie 的 Path 须覆盖 /api/auth/*,
    否则回调报 state 未绑定、启动刷新 401。"""
    await seed_all(session, get_settings())
    fake_keycloak(PROFILE)
    start = await client.get("/auth/keycloak/start")
    state_path = _cookie_path(start.headers.get_list("set-cookie"), "km_oidc_state")
    assert state_path is not None and _path_covers(state_path, "/api/auth/keycloak/callback")
    state = parse_qs(urlparse(cast("str", start.json()["authorize_url"])).query)["state"][0]

    done = await _callback(client, state)
    assert done.status_code == 200
    refresh_path = _cookie_path(done.headers.get_list("set-cookie"), "km_refresh")
    assert refresh_path is not None and _path_covers(refresh_path, "/api/auth/refresh")


async def test_state_is_single_use(
    client: AsyncClient, fake_keycloak: Callable[[NormalizedProfile], None], session: AsyncSession
) -> None:
    await seed_all(session, get_settings())
    fake_keycloak(PROFILE)
    state = await _start(client)
    assert (await _callback(client, state)).status_code == 200
    replay = await _callback(client, state)
    assert replay.status_code == 401  # GETDEL 一次性消费


async def test_forged_state_rejected(
    client: AsyncClient, fake_keycloak: Callable[[NormalizedProfile], None], session: AsyncSession
) -> None:
    await seed_all(session, get_settings())
    fake_keycloak(PROFILE)
    r = await _callback(client, "forged-state")
    assert r.status_code == 401


async def test_jwks_refreshes_after_key_rotation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)
    key1, jwk1 = _rsa_pair()
    key2, jwk2 = _rsa_pair("k2")
    state: dict[str, Any] = {
        "kid": KID,
        "key": key1,
        "keys": [jwk1],
        "jwks_calls": 0,
    }

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, json=_metadata())
        if req.url.path == "/token":
            token = jwt.encode(
                _claims(),
                state["key"],
                algorithm="RS256",
                headers={"kid": cast("str", state["kid"])},
            )
            return httpx.Response(200, json={"id_token": token})
        if req.url.path == "/jwks":
            state["jwks_calls"] = int(cast("int", state["jwks_calls"])) + 1
            return httpx.Response(200, json={"keys": state["keys"]})
        return httpx.Response(404)

    provider = KeycloakProvider(transport=httpx.MockTransport(handler))
    first = await provider.fetch_profile(get_settings(), code="c", code_verifier="v", nonce="n-1")
    assert first.external_id == "01a1f8e2-0000-7000-8000-000000000001"

    state.update(kid="k2", key=key2, keys=[jwk1, jwk2])
    second = await provider.fetch_profile(get_settings(), code="c", code_verifier="v", nonce="n-1")
    assert second.external_id == first.external_id
    assert cast("int", state["jwks_calls"]) == 2


async def test_end_session_url_omits_empty_post_logout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)
    s = get_settings()
    monkeypatch.setattr(s, "keycloak_post_logout_redirect_uri", "")

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path.endswith("/.well-known/openid-configuration")
        return httpx.Response(200, json={**_metadata(), "end_session_endpoint": f"{ISSUER}/logout"})

    provider = KeycloakProvider(transport=httpx.MockTransport(handler))
    url = await provider.end_session_url(s)

    assert url is not None
    query = parse_qs(urlparse(url).query)
    assert "client_id" in query
    assert "post_logout_redirect_uri" not in query

    monkeypatch.setattr(s, "keycloak_post_logout_redirect_uri", "http://localhost:5173/login")
    url = await provider.end_session_url(s)
    assert url is not None
    assert parse_qs(urlparse(url).query)["post_logout_redirect_uri"] == [
        "http://localhost:5173/login"
    ]


async def test_start_is_rate_limited(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    fake_keycloak: Callable[[NormalizedProfile], None],
) -> None:
    fake_keycloak(PROFILE)
    from kongming.auth import router as auth_router
    from kongming.auth.login_limit import LoginRateLimiter

    monkeypatch.setattr(auth_router, "_login_limiter", LoginRateLimiter())
    monkeypatch.setattr(get_settings(), "auth_login_rate_per_minute", 1)

    assert (await client.get("/auth/keycloak/start")).status_code == 200
    limited = await client.get("/auth/keycloak/start")
    assert limited.status_code == 429


async def test_jwks_network_error_is_normalized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, json=_metadata())
        raise httpx.ConnectError("jwks down", request=req)

    provider = KeycloakProvider(transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderExchangeFailed):
        await provider._jwks_document(  # pyright: ignore[reportPrivateUsage]
            {"jwks_uri": f"{ISSUER}/jwks"}, force=True
        )


async def test_callback_requires_state_cookie(
    client: AsyncClient, fake_keycloak: Callable[[NormalizedProfile], None]
) -> None:
    fake_keycloak(PROFILE)
    start = await client.get("/auth/keycloak/start")
    state = cast("dict[str, str]", start.json())["authorize_url"].split("state=")[1].split("&")[0]
    client.cookies.clear()
    r = await client.post("/auth/keycloak/callback", json={"code": "c0de", "state": state})
    assert r.status_code == 401


async def test_callback_rejects_mismatched_state_cookie(
    client: AsyncClient,
    session: AsyncSession,
    fake_keycloak: Callable[[NormalizedProfile], None],
) -> None:
    await seed_all(session, get_settings())
    fake_keycloak(PROFILE)
    state = await _start(client)
    assert (await _callback(client, "forged-state")).status_code == 401
    # 错误回调不消费服务端 state,原 state 仍可完成一次合法登录
    assert (await _callback(client, state)).status_code == 200
    assert "km_oidc_state" not in client.cookies  # 成功后即刻清除


async def test_callback_rejects_non_ascii_state_without_crash(
    client: AsyncClient,
    session: AsyncSession,
    fake_keycloak: Callable[[NormalizedProfile], None],
) -> None:
    """非 ASCII state 一律 401;不得在恒定时间比较处抛出 TypeError。"""
    await seed_all(session, get_settings())
    fake_keycloak(PROFILE)
    await _start(client)
    assert (await _callback(client, "中文-state")).status_code == 401


async def test_concurrent_tab_logins_keep_multiple_states(
    client: AsyncClient,
    session: AsyncSession,
    fake_keycloak: Callable[[NormalizedProfile], None],
) -> None:
    """两个 tab 先后发起登录:state 共存,各自回调互不覆盖。"""
    await seed_all(session, get_settings())
    fake_keycloak(PROFILE)
    first = await _start(client)
    second = await _start(client)
    raw = client.cookies.get("km_oidc_state")
    assert raw is not None
    assert set(raw.split(".")) == {first, second}
    assert (await _callback(client, first)).status_code == 200
    assert client.cookies.get("km_oidc_state") == second
    assert (await _callback(client, second)).status_code == 200
    assert "km_oidc_state" not in client.cookies


async def test_state_cookie_caps_concurrent_states(
    client: AsyncClient,
    session: AsyncSession,
    fake_keycloak: Callable[[NormalizedProfile], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """并发 state 上限 8:超出丢最旧,最新 state 仍可完成登录。"""
    from kongming.auth import router as auth_router
    from kongming.auth.login_limit import LoginRateLimiter

    monkeypatch.setattr(auth_router, "_login_limiter", LoginRateLimiter())
    await seed_all(session, get_settings())
    fake_keycloak(PROFILE)
    states = [await _start(client) for _ in range(10)]
    raw = client.cookies.get("km_oidc_state")
    assert raw is not None
    assert raw.split(".") == states[2:]
    assert (await _callback(client, states[0])).status_code == 401
    assert (await _callback(client, states[-1])).status_code == 200


async def test_logout_sso_revokes_and_redirects(
    client: AsyncClient,
    session: AsyncSession,
    fake_keycloak: Callable[[NormalizedProfile], None],
) -> None:
    await seed_all(session, get_settings())
    fake_keycloak(PROFILE)
    pair = (await _callback(client, await _start(client))).json()
    r = await client.post("/auth/logout/sso", headers=_cookie(pair["refresh_token"]))
    assert r.status_code == 200
    assert "end_session" in cast("dict[str, str]", r.json())["redirect_url"]
    refreshed = await client.post("/auth/refresh", headers=_cookie(pair["refresh_token"]))
    assert refreshed.status_code == 401


async def test_logout_sso_uses_refresh_cookie_and_clears_it(
    client: AsyncClient,
    session: AsyncSession,
    fake_keycloak: Callable[[NormalizedProfile], None],
) -> None:
    await seed_all(session, get_settings())
    fake_keycloak(PROFILE)
    await _callback(client, await _start(client))
    assert "km_refresh" in client.cookies

    r = await client.post("/auth/logout/sso", json={})

    assert r.status_code == 200
    assert "km_refresh" not in client.cookies
    assert (await client.post("/auth/refresh", json={})).status_code == 401


async def test_logout_sso_survives_discovery_outage(
    client: AsyncClient,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    fake_keycloak: Callable[[NormalizedProfile], None],
) -> None:
    await seed_all(session, get_settings())
    fake_keycloak(PROFILE)
    pair = (await _callback(client, await _start(client))).json()
    from kongming.auth import providers
    from kongming.auth.models import ProviderExchangeFailed

    async def _boom(settings: object) -> str:
        raise ProviderExchangeFailed("发现文档不可达")

    monkeypatch.setattr(providers.REGISTRY["keycloak"], "end_session_url", _boom)
    r = await client.post("/auth/logout/sso", headers=_cookie(pair["refresh_token"]))

    assert r.status_code == 200
    assert r.json()["redirect_url"] is None  # 本地登出仍完成,交给前端回落
    refreshed = await client.post("/auth/refresh", headers=_cookie(pair["refresh_token"]))
    assert refreshed.status_code == 401
