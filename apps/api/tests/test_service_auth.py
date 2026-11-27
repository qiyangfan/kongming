"""服务只读 API 的 Keycloak 令牌校验:按 kid 选键、azp 白名单、失败同形。"""

import time
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from kongming.control.models import ControlAuthError
from kongming.control.service_auth import ServiceTokenVerifier

ISSUER = "https://idp.example"
CLIENT = "jinguan-read-kongming"


def _keypair(kid: str) -> tuple[str, dict[str, Any]]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    jwk = RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    jwk.update({"kid": kid, "use": "sig", "alg": "RS256"})
    return pem, jwk


def _token(pem: str, *, kid: str = "kid-1", azp: str = CLIENT) -> str:
    now = int(time.time())
    return jwt.encode(
        {"iss": ISSUER, "sub": "service", "exp": now + 60, "azp": azp},
        pem,
        algorithm="RS256",
        headers={"kid": kid},
    )


def _transport(jwk: dict[str, Any]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/openid-configuration":
            return httpx.Response(200, json={"issuer": ISSUER, "jwks_uri": f"{ISSUER}/jwks"})
        if request.url.path == "/jwks":
            return httpx.Response(200, json={"keys": [jwk]})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def _verifier(transport: httpx.MockTransport) -> ServiceTokenVerifier:
    return ServiceTokenVerifier(
        local_token="",
        issuer=ISSUER,
        allowed_client_ids=(CLIENT,),
        transport=transport,
    )


async def test_keycloak_service_token_verifies_selected_kid() -> None:
    pem, jwk = _keypair("kid-1")
    verifier = _verifier(_transport(jwk))
    assert await verifier.verify(f"Bearer {_token(pem)}") == CLIENT


async def test_unknown_kid_force_refresh_is_rate_limited() -> None:
    pem, jwk = _keypair("kid-1")
    calls = {"meta": 0, "jwks": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/openid-configuration":
            calls["meta"] += 1
            return httpx.Response(200, json={"issuer": ISSUER, "jwks_uri": f"{ISSUER}/jwks"})
        if request.url.path == "/jwks":
            calls["jwks"] += 1
            return httpx.Response(200, json={"keys": [jwk]})
        return httpx.Response(404)

    verifier = _verifier(httpx.MockTransport(handler))
    with pytest.raises(ControlAuthError):
        await verifier.verify(f"Bearer {_token(pem, kid='missing-1')}")
    assert calls["jwks"] == 2  # 初次拉取 + 未知 kid 强制刷新一次
    with pytest.raises(ControlAuthError):
        await verifier.verify(f"Bearer {_token(pem, kid='missing-2')}")
    assert calls["jwks"] == 2  # 冷却期内坏 token 不再触发 IdP 调用


async def test_keycloak_service_token_rejects_unknown_key_and_client() -> None:
    pem, jwk = _keypair("kid-1")
    other_pem, _ = _keypair("kid-2")
    verifier = _verifier(_transport(jwk))
    with pytest.raises(ControlAuthError):
        await verifier.verify(f"Bearer {_token(other_pem, kid='kid-2')}")
    with pytest.raises(ControlAuthError):
        await verifier.verify(f"Bearer {_token(pem, azp='other-client')}")
