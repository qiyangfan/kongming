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

from kongming.control.capabilities import SERVICE_ID
from kongming.control.models import ControlAuthError, ControlUnavailable

log = logging.getLogger(__name__)

_META_TTL_SEC = 300.0
_CLOCK_LEEWAY_SEC = 60
# 未知 kid 触发的强制刷新最小间隔:读者面未认证,不能让每个坏 token 打一轮 IdP
_FORCE_REFRESH_MIN_SEC = 30.0


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
        self._jwk: dict[str, PyJWK] | None = None
        self._jwk_at = 0.0
        self._force_at = 0.0
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
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as e:
            raise ControlAuthError("服务令牌格式非法") from e
        kid = header.get("kid")
        candidates = _select_keys(await self._signing_keys(), kid)
        if not candidates:
            # 密钥轮换后缓存缺新 kid:限速强制刷新(坏 token 不能放大 IdP 调用)
            now = time.monotonic()
            if now - self._force_at >= _FORCE_REFRESH_MIN_SEC:
                self._force_at = now
                candidates = _select_keys(await self._signing_keys(force=True), kid)
        if not candidates:
            raise ControlAuthError("服务令牌签名密钥不存在")
        claims: dict[str, Any] | None = None
        for key in candidates:
            try:
                claims = jwt.decode(
                    token,
                    key,
                    algorithms=["RS256"],
                    issuer=self._issuer,
                    audience=SERVICE_ID,
                    leeway=_CLOCK_LEEWAY_SEC,
                    options={"require": ["iss", "exp", "sub"]},
                )
                break
            except jwt.InvalidSignatureError:
                continue
            except jwt.PyJWTError as e:
                raise ControlAuthError("服务令牌校验失败") from e
        if claims is None:
            raise ControlAuthError("服务令牌校验失败")
        return self._authorized_client(claims)

    def _authorized_client(self, claims: dict[str, Any]) -> str:
        """校验调用方白名单与读者 scope;返回 client id。"""
        client_id = claims.get("azp") or claims.get("client_id")
        if not isinstance(client_id, str) or client_id not in self._allowed:
            raise ControlAuthError("服务令牌调用方不在读者白名单")
        scope = claims.get("scope")
        granted: set[str] = set(scope.split()) if isinstance(scope, str) else set()
        if "service:read" not in granted:
            raise ControlAuthError("服务令牌缺 service:read scope")
        return client_id

    async def _signing_keys(self, *, force: bool = False) -> dict[str, PyJWK]:
        if not force and self._jwk is not None and time.monotonic() - self._jwk_at < _META_TTL_SEC:
            return self._jwk
        async with self._lock:
            if (
                not force
                and self._jwk is not None
                and time.monotonic() - self._jwk_at < _META_TTL_SEC
            ):
                return self._jwk
            keys = _parse_jwks(await self._fetch_jwks())
            self._jwk, self._jwk_at = keys, time.monotonic()
            return keys

    async def _fetch_jwks(self) -> list[object]:
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
        return cast("list[object]", keys)


def _parse_jwks(keys: list[object]) -> dict[str, PyJWK]:
    """JWKS → kid 索引;仅收 RSA 签名键,加密键/未知类型跳过;畸形 RSA 条目整体拒绝。"""
    found: dict[str, PyJWK] = {}
    for item in keys:
        if not isinstance(item, dict):
            raise ControlUnavailable("服务身份 JWKS 条目非法")
        entry = cast("dict[str, Any]", item)
        if entry.get("kty") != "RSA" or entry.get("use") not in (None, "sig"):
            continue
        alg = entry.get("alg")
        if isinstance(alg, str) and alg and alg != "RS256":
            continue
        try:
            jwk = PyJWK.from_dict(entry)
        except (jwt.PyJWTError, ValueError) as e:
            raise ControlUnavailable("服务身份 JWKS 非法") from e
        kid = entry.get("kid")
        found[str(kid) if isinstance(kid, str) and kid else f"#{len(found)}"] = jwk
    if not found:
        raise ControlUnavailable("服务身份 JWKS 无 RSA 密钥")
    return found


def _select_keys(keys: dict[str, PyJWK], kid: object) -> list[PyJWK]:
    """按 token 头 kid 选键;无 kid 时逐个尝试(轮换期兼容)。"""
    if isinstance(kid, str) and kid:
        key = keys.get(kid)
        return [key] if key is not None else []
    return list(keys.values())


def secrets_equal(a: str, b: str) -> bool:
    import secrets

    return secrets.compare_digest(a.encode(), b.encode())
