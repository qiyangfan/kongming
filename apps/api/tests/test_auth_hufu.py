"""虎符(hufu) OIDC provider:发现文档/JWKS/nonce,以及禁止 email 自动关联。"""

import base64
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
from kongming.auth.providers.hufu import HufuProvider
from kongming.auth.seed import seed_all
from kongming.config import get_settings
from tests.conftest import MakeUser

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
    monkeypatch.setattr(s, "hufu_issuer", ISSUER)
    monkeypatch.setattr(s, "hufu_client_id", "kongming-hufu")
    monkeypatch.setattr(s, "hufu_client_secret", "test-secret")
    monkeypatch.setattr(s, "hufu_redirect_uri", "http://localhost:5173/auth/hufu/callback")


def _provider(monkeypatch: pytest.MonkeyPatch, claims: dict[str, Any]) -> HufuProvider:
    _configure(monkeypatch)
    key, jwk = _rsa_pair()
    id_token = jwt.encode(claims, key, algorithm="RS256", headers={"kid": KID})

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}/authorize",
                    "token_endpoint": f"{ISSUER}/token",
                    "jwks_uri": f"{ISSUER}/jwks",
                },
            )
        if req.url.path == "/token":
            return httpx.Response(200, json={"id_token": id_token})
        if req.url.path == "/jwks":
            return httpx.Response(200, json={"keys": [jwk]})
        return httpx.Response(404)

    return HufuProvider(transport=httpx.MockTransport(handler))


def _claims(nonce: str = "n-1", aud: str = "kongming-hufu") -> dict[str, Any]:
    import time

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


async def test_audience_mismatch_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(monkeypatch, _claims(aud="someone-else"))
    with pytest.raises(ProviderExchangeFailed):
        await provider.fetch_profile(get_settings(), code="c", code_verifier="v", nonce="n-1")


async def test_options_lists_hufu_when_configured(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch)
    r = await client.get("/auth/options")
    keys = [p["provider"] for p in r.json()["providers"]]
    assert "hufu" in keys


async def _callback(client: AsyncClient, provider: str) -> httpx.Response:
    r = await client.get(f"/auth/{provider}/start")
    authorize_url = cast("str", r.json()["authorize_url"])
    state = parse_qs(urlparse(authorize_url).query)["state"][0]
    return await client.post(f"/auth/{provider}/callback", json={"code": "c0de", "state": state})


async def test_hufu_never_links_by_email(
    client: AsyncClient,
    fake_provider: Callable[[str, NormalizedProfile], None],
    make_user: MakeUser,
    session: AsyncSession,
) -> None:
    await seed_all(session, get_settings())
    existing = await make_user("localdev", "Passw0rd!", email="shared@example.com")
    profile = NormalizedProfile(
        external_id="hufu-sub-1",
        username="alice",
        display_name="Alice",
        email="shared@example.com",
        email_verified=True,
        avatar_url=None,
    )
    fake_provider("hufu", profile)
    r = await _callback(client, "hufu")
    assert r.status_code == 200
    me = await client.get(
        "/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"}
    )
    assert me.json()["user_id"] != existing  # 不按 email 合并
    bound = (
        await session.execute(
            text(
                "SELECT user_id FROM public.user_identities"
                " WHERE provider='HUFU' AND external_id='hufu-sub-1'"
            )
        )
    ).scalar_one()
    assert str(bound) != existing


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
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}/authorize",
                    "token_endpoint": f"{ISSUER}/token",
                    "jwks_uri": f"{ISSUER}/jwks",
                },
            )
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

    provider = HufuProvider(transport=httpx.MockTransport(handler))
    first = await provider.fetch_profile(get_settings(), code="c", code_verifier="v", nonce="n-1")
    assert first.external_id == "01a1f8e2-0000-7000-8000-000000000001"

    state.update(kid="k2", key=key2, keys=[jwk1, jwk2])
    second = await provider.fetch_profile(get_settings(), code="c", code_verifier="v", nonce="n-1")
    assert second.external_id == first.external_id
    assert cast("int", state["jwks_calls"]) == 2
