"""GitHub 插件(ADR-006 首发):Authorization Code 流,不声明 PKCE。

email 取 /user/emails 中 primary 条目及其 verified 标记——只有已验证 email
才允许 account_link 关联既有账号(spec「联邦账号关联与自动建号」)。
"""

from authlib.integrations.httpx_client import AsyncOAuth2Client

from kongming.auth.models import NormalizedProfile
from kongming.auth.providers.base import OAuthProviderBase
from kongming.config import Settings


class GitHubProvider(OAuthProviderBase):
    key = "github"
    display_name = "GitHub"
    pkce = False
    auth_url = "https://github.com/login/oauth/authorize"
    token_url = "https://github.com/login/oauth/access_token"  # noqa: S105  # 端点常量,非密钥
    scope = "read:user user:email"

    def client_id(self, s: Settings) -> str | None:
        return s.github_client_id

    def client_secret(self, s: Settings) -> str | None:
        return s.github_client_secret

    def redirect_uri(self, s: Settings) -> str:
        return s.github_redirect_uri

    async def fetch_raw_profile(self, client: AsyncOAuth2Client) -> NormalizedProfile:
        resp = await client.get("https://api.github.com/user")
        resp.raise_for_status()
        raw: dict[str, object] = resp.json()

        email: str | None = None
        email_verified = False
        emails_resp = await client.get("https://api.github.com/user/emails")
        if emails_resp.status_code == 200:
            entries: list[dict[str, object]] = emails_resp.json()
            primary = next((e for e in entries if e.get("primary")), None)
            chosen = primary or next((e for e in entries if e.get("verified")), None)
            if chosen:
                email = str(chosen["email"])
                email_verified = bool(chosen.get("verified"))

        login = str(raw["login"])
        return NormalizedProfile(
            external_id=str(raw["id"]),
            username=login,
            display_name=str(raw.get("name") or login),
            email=email,
            email_verified=email_verified,
            avatar_url=None if raw.get("avatar_url") is None else str(raw["avatar_url"]),
        )
