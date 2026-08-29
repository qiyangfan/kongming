"""钉钉插件(003):新版统一身份认证(OIDC 形态;旧版 app 签名机制不采用)。

协议形态(官方文档核验 2026-08-17):
- 授权 URL 为标准 OAuth2 参数 + prompt=consent;官方文档无 PKCE 形态 → pkce=False
- token 端点为 JSON 请求体(非标准 form)→ authlib 仅构造授权 URL,
  token/profile 由 fetch_profile 覆写 httpx 直写
- external_id 用 unionId(跨应用稳定标识),不用 openId(防同企业多应用身份分裂)
- email 无验证语义:不入管线(email_verified 恒 False,永不走 email 关联)
"""

import httpx

from kongming.auth.models import NormalizedProfile, ProviderExchangeFailed
from kongming.auth.providers.base import OAuthClient, OAuthProviderBase, resp_json
from kongming.config import Settings


def normalize(me: dict[str, object]) -> NormalizedProfile:
    """users/me 响应 → 归一化 profile(纯函数,字段映射回归网)。"""
    union_id = str(me["unionId"])
    nick = str(me["nick"]) if me.get("nick") else ""
    return NormalizedProfile(
        external_id=union_id,
        username=nick or union_id,  # nick 缺失兜底 unionId
        display_name=nick or union_id,
        email=None,
        email_verified=False,
        avatar_url=str(me["avatarUrl"]) if me.get("avatarUrl") else None,
    )


class DingTalkProvider(OAuthProviderBase):
    key = "dingtalk"
    display_name = "钉钉"
    pkce = False
    auth_url = "https://login.dingtalk.com/oauth2/auth"
    token_url = "https://api.dingtalk.com/v1.0/oauth2/userAccessToken"  # noqa: S105  # 端点常量,非密钥
    scope = "openid"

    def client_id(self, s: Settings) -> str | None:
        return s.dingtalk_client_id

    def client_secret(self, s: Settings) -> str | None:
        return s.dingtalk_client_secret

    def redirect_uri(self, s: Settings) -> str:
        return s.dingtalk_redirect_uri

    def authorize_url(self, s: Settings, *, state: str, code_challenge: str | None) -> str:
        # 官方推荐 prompt=consent 保授权页可见;其余参数走 authlib 标准构造
        c = self._client(s)
        url, _ = c.create_authorization_url(
            self.auth_url, state=state, scope=self.scope, prompt="consent"
        )
        return url

    async def fetch_raw_profile(self, client: OAuthClient) -> NormalizedProfile:
        raise NotImplementedError("钉钉 token 为 JSON 请求体,走 fetch_profile 覆写")

    async def fetch_profile(
        self, s: Settings, *, code: str, code_verifier: str | None
    ) -> NormalizedProfile:
        async with httpx.AsyncClient(timeout=10) as http:
            tok_resp = await http.post(
                self.token_url,
                json={
                    "clientId": s.dingtalk_client_id,
                    "clientSecret": s.dingtalk_client_secret,
                    "code": code,
                    "grantType": "authorization_code",
                },
            )
            if tok_resp.status_code != 200:
                raise ProviderExchangeFailed(
                    f"钉钉登录失败(code 交换异常:{tok_resp.status_code} {tok_resp.text[:200]})"
                )
            access_token = resp_json(tok_resp, self.display_name).get("accessToken")
            if not access_token:
                raise ProviderExchangeFailed("钉钉登录失败(code 交换异常:响应缺 accessToken)")
            me_resp = await http.get(
                "https://api.dingtalk.com/v1.0/contact/users/me",
                headers={"x-acs-dingtalk-access-token": str(access_token)},
            )
            if me_resp.status_code != 200:
                raise ProviderExchangeFailed(
                    f"钉钉登录失败(用户信息拉取异常:{me_resp.status_code} {me_resp.text[:200]})"
                )
        return normalize(resp_json(me_resp, self.display_name))
