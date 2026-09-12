"""Google 插件(ADR-006 首发第二家):OIDC userinfo,声明 PKCE(S256)。

email_verified 直接来自 userinfo;username 取 email 本地部分(sub 兜底脱冲突由
account_link 的用户名去重负责)。
"""

from kongming.auth.models import NormalizedProfile
from kongming.auth.providers.base import OAuthClient, OAuthProviderBase
from kongming.config import Settings


class GoogleProvider(OAuthProviderBase):
    key = "google"
    display_name = "Google"
    pkce = True
    auth_url = "https://accounts.google.com/o/oauth2/v2/auth"
    token_url = "https://oauth2.googleapis.com/token"  # noqa: S105  # 端点常量,非密钥
    scope = "openid email profile"

    def client_id(self, s: Settings) -> str | None:
        return s.google_client_id

    def client_secret(self, s: Settings) -> str | None:
        return s.google_client_secret

    def redirect_uri(self, s: Settings) -> str:
        return s.google_redirect_uri

    async def fetch_raw_profile(self, client: OAuthClient) -> NormalizedProfile:
        resp = await client.get("https://openidconnect.googleapis.com/v1/userinfo")
        resp.raise_for_status()
        raw: dict[str, object] = resp.json()

        sub = str(raw["sub"])
        email = None if raw.get("email") is None else str(raw["email"])
        username = email.split("@", 1)[0] if email else f"user-{sub[:8]}"
        return NormalizedProfile(
            external_id=sub,
            username=username,
            display_name=str(raw.get("name") or username),
            email=email,
            email_verified=bool(raw.get("email_verified")),
            avatar_url=None if raw.get("picture") is None else str(raw["picture"]),
        )
