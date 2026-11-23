"""Keycloak(keycloak)统一身份 provider:标准 OIDC 授权码 + PKCE,后端独占令牌。

发现文档/JWKS 按需拉取并短时缓存;id_token 用 PyJWT 验 RS256 + iss/aud/exp/nonce,
主体只取 `sub`。Keycloak下发的 email 一律未验证,且按契约禁止按 email 自动关联。
"""

import asyncio
import time
from typing import Any, cast
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx
import jwt
from jwt import PyJWK

from kongming.auth.models import NormalizedProfile, ProviderExchangeFailed
from kongming.auth.providers.base import OAuthProviderBase
from kongming.config import Settings

_META_TTL_SEC = 300.0
_CLOCK_LEEWAY_SEC = 60


class KeycloakProvider(OAuthProviderBase):
    key = "keycloak"
    display_name = "统一身份"
    pkce = True
    oidc = True
    scope = "openid profile email"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        # transport 仅供测试注入(MockTransport),生产为 None 走真实网络
        self._transport = transport
        self._meta: dict[str, Any] | None = None
        self._meta_at = 0.0
        self._jwks: dict[str, Any] | None = None
        self._jwks_at = 0.0
        self._lock = asyncio.Lock()

    def client_id(self, s: Settings) -> str | None:
        return s.keycloak_client_id

    def client_secret(self, s: Settings) -> str | None:
        return s.keycloak_client_secret

    def redirect_uri(self, s: Settings) -> str:
        return s.keycloak_redirect_uri

    def configured(self, s: Settings) -> bool:
        return bool(
            s.keycloak_enabled
            and s.keycloak_issuer
            and s.keycloak_client_id
            and s.keycloak_client_secret
        )

    async def prepare(self, s: Settings) -> None:
        await self._ensure_metadata(s)

    def authorize_url(
        self,
        s: Settings,
        *,
        state: str,
        code_challenge: str | None,
        nonce: str | None = None,
    ) -> str:
        meta = self._meta
        if meta is None:
            raise ProviderExchangeFailed("统一身份登录未就绪(发现文档缺失)")
        q = urlencode(
            {
                "response_type": "code",
                "client_id": self.client_id(s) or "",
                "redirect_uri": self.redirect_uri(s),
                "scope": self.scope,
                "state": state,
                "nonce": nonce or "",
                "code_challenge": code_challenge or "",
                "code_challenge_method": "S256",
            }
        )
        return f"{cast('str', meta['authorization_endpoint'])}?{q}"

    async def end_session_url(self, s: Settings) -> str | None:
        """构造Keycloak RP-Initiated Logout 地址;发现文档无端点时返回 None。"""
        meta = await self._ensure_metadata(s)
        endpoint = meta.get("end_session_endpoint")
        if not isinstance(endpoint, str) or not endpoint:
            return None
        parsed = urlparse(endpoint)
        q = dict(parse_qsl(parsed.query))
        q["client_id"] = self.client_id(s) or ""
        if s.keycloak_post_logout_redirect_uri:
            q["post_logout_redirect_uri"] = s.keycloak_post_logout_redirect_uri
        return urlunparse(parsed._replace(query=urlencode(q)))

    async def fetch_profile(
        self,
        s: Settings,
        *,
        code: str,
        code_verifier: str | None,
        nonce: str | None = None,
    ) -> NormalizedProfile:
        meta = await self._ensure_metadata(s)
        id_token = await self._exchange(s, meta, code, code_verifier)
        claims = await self._verify(s, meta, id_token, nonce)
        return _profile_from_claims(claims)

    def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=10, transport=self._transport, trust_env=False)

    async def _ensure_metadata(self, s: Settings) -> dict[str, Any]:
        if not s.keycloak_issuer:
            raise ProviderExchangeFailed("统一身份登录未配置")
        if self._meta is not None and time.monotonic() - self._meta_at < _META_TTL_SEC:
            return self._meta
        async with self._lock:
            if self._meta is not None and time.monotonic() - self._meta_at < _META_TTL_SEC:
                return self._meta
            issuer = s.keycloak_issuer.rstrip("/")
            url = f"{issuer}/.well-known/openid-configuration"
            try:
                async with self._http() as http:
                    resp = await http.get(url, headers={"Accept": "application/json"})
            except httpx.HTTPError as e:
                raise ProviderExchangeFailed("统一身份发现文档不可用") from e
            if resp.status_code != 200:
                raise ProviderExchangeFailed(f"统一身份发现文档不可用({resp.status_code})")
            data = _json_object(resp)
            if data.get("issuer") != issuer:
                raise ProviderExchangeFailed("统一身份发现文档 issuer 不符")
            for field in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
                if not isinstance(data.get(field), str) or not data[field]:
                    raise ProviderExchangeFailed(f"统一身份发现文档缺 {field}")
            self._meta, self._meta_at = data, time.monotonic()
            return data

    async def _exchange(
        self, s: Settings, meta: dict[str, Any], code: str, code_verifier: str | None
    ) -> str:
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri(s),
            "client_id": self.client_id(s) or "",
            "code_verifier": code_verifier or "",
        }
        async with self._http() as http:
            resp = await http.post(
                cast("str", meta["token_endpoint"]),
                data=form,
                auth=(self.client_id(s) or "", self.client_secret(s) or ""),
                headers={"Accept": "application/json"},
            )
        if resp.status_code < 200 or resp.status_code >= 300:
            raise ProviderExchangeFailed(f"统一身份换码失败({resp.status_code})")
        body = _json_object(resp)
        id_token = body.get("id_token")
        if not isinstance(id_token, str) or not id_token:
            raise ProviderExchangeFailed("统一身份未返回 id_token")
        return id_token

    async def _verify(
        self, s: Settings, meta: dict[str, Any], id_token: str, nonce: str | None
    ) -> dict[str, Any]:
        try:
            header = jwt.get_unverified_header(id_token)
        except jwt.PyJWTError as e:
            raise ProviderExchangeFailed("统一身份 id_token 格式非法") from e
        jwk = await self._key_for(meta, header.get("kid"))
        try:
            claims = jwt.decode(
                id_token,
                jwk,
                algorithms=["RS256"],
                audience=self.client_id(s),
                issuer=cast("str", meta["issuer"]),
                leeway=_CLOCK_LEEWAY_SEC,
                options={"require": ["iss", "aud", "exp", "iat", "sub"]},
            )
        except jwt.PyJWTError as e:
            raise ProviderExchangeFailed("统一身份 id_token 校验失败") from e
        if nonce is None or claims.get("nonce") != nonce:
            raise ProviderExchangeFailed("统一身份 nonce 校验失败")
        return claims

    async def _key_for(self, meta: dict[str, Any], kid: object) -> Any:
        if not isinstance(kid, str) or not kid:
            raise ProviderExchangeFailed("统一身份 id_token 缺 kid")
        key = self._select_key(await self._jwks_document(meta), kid)
        if key is None:
            # 密钥轮换后旧 JWKS 缓存不含新 kid:强制刷新一次,避免登录失败到 TTL。
            key = self._select_key(await self._jwks_document(meta, force=True), kid)
        if key is None:
            raise ProviderExchangeFailed("统一身份 JWKS 缺对应 kid")
        return key

    async def _jwks_document(self, meta: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
        if (
            not force
            and self._jwks is not None
            and time.monotonic() - self._jwks_at < _META_TTL_SEC
        ):
            return self._jwks
        async with self._lock:
            if (
                not force
                and self._jwks is not None
                and time.monotonic() - self._jwks_at < _META_TTL_SEC
            ):
                return self._jwks
            try:
                async with self._http() as http:
                    resp = await http.get(cast("str", meta["jwks_uri"]))
            except httpx.HTTPError as e:
                raise ProviderExchangeFailed("统一身份 JWKS 不可用") from e
            if resp.status_code != 200:
                raise ProviderExchangeFailed("统一身份 JWKS 不可用")
            jwks = _json_object(resp)
            self._jwks, self._jwks_at = jwks, time.monotonic()
            return jwks

    @staticmethod
    def _select_key(jwks: dict[str, Any], kid: str) -> Any:
        keys = jwks.get("keys")
        if not isinstance(keys, list):
            raise ProviderExchangeFailed("统一身份 JWKS 格式非法")
        for item in cast("list[dict[str, Any]]", keys):
            if item.get("kid") == kid:
                try:
                    return PyJWK(item).key
                except (jwt.PyJWTError, ValueError) as e:
                    raise ProviderExchangeFailed("统一身份 JWK 非法") from e
        return None


def _json_object(resp: httpx.Response) -> dict[str, Any]:
    try:
        data = resp.json()
    except ValueError as e:
        raise ProviderExchangeFailed("统一身份响应格式异常") from e
    if not isinstance(data, dict):
        raise ProviderExchangeFailed("统一身份响应格式异常")
    return cast("dict[str, Any]", data)


def _profile_from_claims(claims: dict[str, Any]) -> NormalizedProfile:
    sub = str(claims["sub"])
    raw_email = claims.get("email")
    email = raw_email if isinstance(raw_email, str) and raw_email else None
    username = claims.get("preferred_username")
    if not isinstance(username, str) or not username:
        username = email.split("@", 1)[0] if email else f"user-{sub[:8]}"
    display = claims.get("name")
    return NormalizedProfile(
        external_id=sub,
        username=username,
        display_name=(display if isinstance(display, str) and display else username)[:64],
        email=email,
        email_verified=bool(claims.get("email_verified", False)),
        avatar_url=None,
    )
