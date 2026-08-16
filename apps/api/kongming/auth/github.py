"""GitHub OAuth 联邦(ADR-003):标准 Authorization Code 流。

首次登录自动建号(syncMode=IMPORT 语义自管):
github login → /auth/github/start → GitHub 授权 → /auth/github/callback
→ 按 (auth_source=GITHUB, external_id) 查/建 sys_user → 签发本平台 JWT。
"""

from authlib.integrations.httpx_client import AsyncOAuth2Client

from kongming.config import get_settings

GITHUB_AUTH_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"  # noqa: S105 —— 端点常量,非密钥
GITHUB_USER_API = "https://api.github.com/user"


def client(state: str | None = None) -> AsyncOAuth2Client:
    s = get_settings()
    if not s.github_client_id or not s.github_client_secret:
        raise RuntimeError("GitHub OAuth 未配置(KM_GITHUB_CLIENT_ID/SECRET)")
    # redirect_uri 与 GitHub OAuth App 注册的 Authorization callback URL 必须精确一致;
    # dev 默认 vite 端口 5173,生产经 KM_GITHUB_REDIRECT_URI 覆盖
    return AsyncOAuth2Client(
        client_id=s.github_client_id,
        client_secret=s.github_client_secret,
        redirect_uri=s.github_redirect_uri,
        state=state,
    )


async def exchange_and_profile(code: str) -> dict[str, object]:
    """code 换 token → 拉 GitHub profile(显式关闭连接,不依赖 async-with 存根)。"""
    c = client()
    try:
        await c.fetch_token(GITHUB_TOKEN_URL, code=code)
        resp = await c.get(GITHUB_USER_API)
        resp.raise_for_status()
        profile: dict[str, object] = resp.json()
        return profile  # {id, login, name, email, ...}
    finally:
        await c.aclose()
