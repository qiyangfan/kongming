"""Keycloak client credentials 令牌提供者:发现文档与换令牌不因同锁重入死锁。"""

import asyncio

import httpx
import pytest

from kongming.control.identity import KeycloakClientCredentialsProvider
from kongming.control.models import ControlUnavailable

_ISSUER = "http://keycloak.test/realms/longzhong"


def _provider(handler: httpx.MockTransport) -> KeycloakClientCredentialsProvider:
    return KeycloakClientCredentialsProvider(
        issuer=_ISSUER,
        client_id="kongming-control",
        client_secret="secret",
        transport=handler,
    )


def test_token_provider_fetches_discovery_and_token() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(
                200,
                json={
                    "issuer": _ISSUER,
                    "token_endpoint": _ISSUER + "/protocol/openid-connect/token",
                },
            )
        if request.url.path.endswith("/protocol/openid-connect/token"):
            return httpx.Response(200, json={"access_token": "short-lived", "expires_in": 300})
        return httpx.Response(404)

    async def run() -> str:
        return await asyncio.wait_for(_provider(httpx.MockTransport(handler)).token(), timeout=2)

    assert asyncio.run(run()) == "short-lived"
    assert calls.count("/realms/longzhong/.well-known/openid-configuration") == 1
    assert calls.count("/realms/longzhong/protocol/openid-connect/token") == 1


def test_token_provider_rejects_bad_issuer() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "issuer": "http://evil.example",
                "token_endpoint": "http://evil.example/token",
            },
        )

    async def run() -> None:
        await asyncio.wait_for(_provider(httpx.MockTransport(handler)).token(), timeout=2)

    with pytest.raises(ControlUnavailable, match="issuer 不符"):
        asyncio.run(run())
