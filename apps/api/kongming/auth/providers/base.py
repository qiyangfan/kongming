"""OAuth2 插件基类(ADR-006 连接器内核):authlib 协议管道收敛于此。

新增 provider = 一个声明式子类(端点/scope/PKCE/凭证读取/profile 归一化),
路由与前端零改动。
"""

from abc import ABC, abstractmethod

import httpx
from authlib.integrations.httpx_client import AsyncOAuth2Client

from kongming.auth.models import NormalizedProfile, ProviderExchangeFailed
from kongming.config import Settings


def resp_json(resp: httpx.Response, provider_name: str) -> dict[str, object]:
    """provider HTTP 响应 → JSON 对象;非 JSON/非对象 → ProviderExchangeFailed(防 500)。"""
    try:
        data = resp.json()
    except ValueError as e:
        raise ProviderExchangeFailed(
            f"{provider_name}登录失败(响应格式异常:{resp.status_code})"
        ) from e
    if not isinstance(data, dict):
        raise ProviderExchangeFailed(f"{provider_name}登录失败(响应格式异常:{resp.status_code})")
    return data


class OAuthProviderBase(ABC):
    key: str
    display_name: str
    pkce: bool
    auth_url: str
    token_url: str
    scope: str

    @abstractmethod
    def client_id(self, s: Settings) -> str | None: ...

    @abstractmethod
    def client_secret(self, s: Settings) -> str | None: ...

    @abstractmethod
    def redirect_uri(self, s: Settings) -> str: ...

    @abstractmethod
    async def fetch_raw_profile(self, client: AsyncOAuth2Client) -> NormalizedProfile:
        """token 已就绪的 client → 拉取并归一化 profile(协议差异止步于此)。"""
        ...

    def configured(self, s: Settings) -> bool:
        # 联邦总开关(默认开):KM_<KEY>_ENABLED=false 即停用该家,无需摘除凭证
        enabled = bool(getattr(s, f"{self.key}_enabled", True))
        return enabled and bool(self.client_id(s) and self.client_secret(s))

    def _client(self, s: Settings) -> AsyncOAuth2Client:
        return AsyncOAuth2Client(
            client_id=self.client_id(s),
            client_secret=self.client_secret(s),
            redirect_uri=self.redirect_uri(s),
        )

    def authorize_url(self, s: Settings, *, state: str, code_challenge: str | None) -> str:
        c = self._client(s)
        extra: dict[str, str] = {}
        if code_challenge is not None:
            extra = {"code_challenge": code_challenge, "code_challenge_method": "S256"}
        url, _ = c.create_authorization_url(self.auth_url, state=state, scope=self.scope, **extra)
        return url

    async def fetch_profile(
        self, s: Settings, *, code: str, code_verifier: str | None
    ) -> NormalizedProfile:
        c = self._client(s)
        try:
            kwargs: dict[str, str] = {}
            if code_verifier is not None:
                kwargs["code_verifier"] = code_verifier
            await c.fetch_token(self.token_url, code=code, **kwargs)
            return await self.fetch_raw_profile(c)
        finally:
            await c.aclose()
