"""服务身份令牌提供者。

managed 部署的两种合法形态:
- 本地 pre-shared(仅回环控制面,dev/单机演练):固定 token 直出;
- Keycloak client credentials:按发现文档换短时 token,提前 30s 刷新。
"""

import asyncio
import time
from typing import Any, cast

import httpx

from kongming.control.models import ControlAuthError, ControlUnavailable
from kongming.control.ports import TokenProvider

_META_TTL_SEC = 300.0
_REFRESH_SKEW_SEC = 30


class StaticTokenProvider:
    """本地 pre-shared 服务身份(仅回环控制面地址;生产由配置校验拒绝)。"""

    def __init__(self, token: str) -> None:
        self._token = token

    async def token(self) -> str:
        if not self._token:
            raise ControlAuthError("未配置本地服务身份令牌")
        return self._token


class KeycloakClientCredentialsProvider:
    def __init__(
        self,
        *,
        issuer: str,
        client_id: str,
        client_secret: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._issuer = issuer.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._transport = transport
        self._meta: dict[str, Any] | None = None
        self._meta_at = 0.0
        self._token = ""
        self._token_exp = 0.0
        self._lock = asyncio.Lock()

    def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=10, transport=self._transport, trust_env=False)

    async def _metadata(self) -> dict[str, Any]:
        if self._meta is not None and time.monotonic() - self._meta_at < _META_TTL_SEC:
            return self._meta
        async with self._lock:
            if self._meta is not None and time.monotonic() - self._meta_at < _META_TTL_SEC:
                return self._meta
            url = f"{self._issuer}/.well-known/openid-configuration"
            try:
                async with self._http() as http:
                    resp = await http.get(url, headers={"Accept": "application/json"})
            except httpx.HTTPError as e:
                raise ControlUnavailable("服务身份发现文档不可用") from e
            if resp.status_code != 200:
                raise ControlUnavailable(f"服务身份发现文档不可用({resp.status_code})")
            data = cast("dict[str, Any]", resp.json())
            if data.get("issuer") != self._issuer:
                raise ControlUnavailable("服务身份发现文档 issuer 不符")
            endpoint = data.get("token_endpoint")
            if not isinstance(endpoint, str) or not endpoint:
                raise ControlUnavailable("服务身份发现文档缺 token_endpoint")
            self._meta, self._meta_at = data, time.monotonic()
            return data

    async def token(self) -> str:
        if self._token and time.monotonic() < self._token_exp - _REFRESH_SKEW_SEC:
            return self._token
        async with self._lock:
            if self._token and time.monotonic() < self._token_exp - _REFRESH_SKEW_SEC:
                return self._token
            meta = await self._metadata()
            try:
                async with self._http() as http:
                    resp = await http.post(
                        cast("str", meta["token_endpoint"]),
                        data={"grant_type": "client_credentials"},
                        auth=(self._client_id, self._client_secret),
                        headers={"Accept": "application/json"},
                    )
            except httpx.HTTPError as e:
                raise ControlUnavailable("服务身份换令牌失败") from e
            if resp.status_code in (401, 403):
                raise ControlAuthError(f"服务身份被拒绝({resp.status_code})")
            if resp.status_code < 200 or resp.status_code >= 300:
                raise ControlUnavailable(f"服务身份换令牌失败({resp.status_code})")
            body = cast("dict[str, Any]", resp.json())
            token = body.get("access_token")
            if not isinstance(token, str) or not token:
                raise ControlUnavailable("服务身份未返回 access_token")
            expires_in = body.get("expires_in", 60)
            ttl = float(expires_in) if isinstance(expires_in, (int, float)) else 60.0
            self._token, self._token_exp = token, time.monotonic() + max(ttl, 30.0)
            return token


def build_token_provider(
    *,
    local_token: str,
    issuer: str,
    client_id: str,
    client_secret: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> TokenProvider:
    """按配置优先本地 pre-shared,否则 Keycloak client credentials。"""
    if (local_token or "").strip():
        return StaticTokenProvider(local_token.strip())
    if issuer and client_id and client_secret:
        return KeycloakClientCredentialsProvider(
            issuer=issuer, client_id=client_id, client_secret=client_secret, transport=transport
        )
    raise ControlUnavailable("未配置服务身份")
