"""服务只读 API 的调用方认证:本地 pre-shared 或 Keycloak 服务 token。

本地 token 只用于回环演练;managed 生产必须走 Keycloak(校验 iss/exp/azp)。
认证失败一律 401/403 同形,不泄露"差在哪"。
"""

import asyncio
import logging
import time
from typing import Any, cast

import httpx
import jwt
from jwt import PyJWK

from kongming.control.models import ControlAuthError, ControlUnavailable

log = logging.getLogger(__name__)

_META_TTL_SEC = 300.0
_CLOCK_LEEWAY_SEC = 60


class ServiceTokenVerifier:
    def __init__(
        self,
        *,
        local_token: str,
        issuer: str,
        allowed_client_ids: tuple[str, ...],
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._local = local_token.strip()
        self._issuer = issuer.rstrip("/")
        self._allowed = frozenset(c for c in allowed_client_ids if c)
        self._transport = transport
        self._jwk: Any = None
        self._jwk_at = 0.0
        self._lock = asyncio.Lock()

    @property
    def configured(self) -> bool:
        return bool(self._local or (self._issuer and self._allowed))

    async def verify(self, authorization: str) -> str:
        """返回调用方标识(本地=local-reader;Keycloak=client id)。"""
        if not authorization.startswith("Bearer "):
            raise ControlAuthError("缺少服务身份")
        token = authorization.removeprefix("Bearer ").strip()
        if not token:
            raise ControlAuthError("缺少服务身份")
        if self._local and secrets_equal(token, self._local):
            return "local-reader"
        if not self._issuer or not self._allowed:
            raise ControlAuthError("未配置 Keycloak 服务身份校验")
        return await self._verify_keycloak(token)

    async def _verify_keycloak(self, token: str) -> str:
        key = await self._signing_key()
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                issuer=self._issuer,
                leeway=_CLOCK_LEEWAY_SEC,
                options={"require": ["iss", "exp", "sub"]},
            )
        except jwt.PyJWTError as e:
            raise ControlAuthError("服务令牌校验失败") from e
        client_id = claims.get("azp") or claims.get("client_id")
        if not isinstance(client_id, str) or client_id not in self._allowed:
            raise ControlAuthError("服务令牌调用方不在读者白名单")
        return client_id

    async def _signing_key(self) -> Any:
        if self._jwk is not None and time.monotonic() - self._jwk_at < _META_TTL_SEC:
            return self._jwk
        async with self._lock:
            if self._jwk is not None and time.monotonic() - self._jwk_at < _META_TTL_SEC:
                return self._jwk
            url = f"{self._issuer}/.well-known/openid-configuration"
            try:
                async with httpx.AsyncClient(
                    timeout=10, transport=self._transport, trust_env=False
                ) as http:
                    meta = await http.get(url, headers={"Accept": "application/json"})
                    if meta.status_code != 200:
                        raise ControlUnavailable("服务身份发现文档不可用")
                    jwks_uri = meta.json().get("jwks_uri")
                    if not isinstance(jwks_uri, str) or not jwks_uri:
                        raise ControlUnavailable("服务身份发现文档缺 jwks_uri")
                    doc = await http.get(jwks_uri, headers={"Accept": "application/json"})
                    if doc.status_code != 200:
                        raise ControlUnavailable("服务身份 JWKS 不可用")
                    keys = doc.json().get("keys")
            except httpx.HTTPError as e:
                raise ControlUnavailable("服务身份 JWKS 拉取失败") from e
            except ValueError as e:
                raise ControlUnavailable("服务身份 JWKS 非法") from e
            if not isinstance(keys, list) or not keys:
                raise ControlUnavailable("服务身份 JWKS 为空")
            dicts = cast("list[dict[str, Any]]", keys)
            self._jwk = [PyJWK.from_dict(k) for k in dicts if k.get("kty") == "RSA"]
            self._jwk_at = time.monotonic()
            if not self._jwk:
                raise ControlUnavailable("服务身份 JWKS 无 RSA 密钥")
            return self._jwk


def secrets_equal(a: str, b: str) -> bool:
    import secrets

    return secrets.compare_digest(a.encode(), b.encode())
