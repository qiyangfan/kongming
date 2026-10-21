"""飞书 OAuth 接入。授权支持 PKCE；后端通过 v2 token 接口换取用户令牌，再读取用户资料。

以 union_id 作为跨应用用户标识，用户名优先取 en_name；未验证的邮箱不参与账号关联。"""

from typing import cast

import httpx

from kongming.auth.models import NormalizedProfile, ProviderExchangeFailed
from kongming.auth.providers.base import OAuthClient, OAuthProviderBase, resp_json
from kongming.config import Settings


def normalize(data: dict[str, object]) -> NormalizedProfile:
    """user_info 响应 data 对象 → 归一化 profile(纯函数,字段映射回归网)。"""
    union_id = str(data["union_id"])
    name = str(data["name"]) if data.get("name") else ""
    en_name = str(data["en_name"]) if data.get("en_name") else ""
    return NormalizedProfile(
        external_id=union_id,
        username=en_name or name or union_id,  # en_name 兜底 name,再兜底 union_id
        display_name=name or en_name or union_id,
        email=None,
        email_verified=False,
        avatar_url=str(data["avatar_url"]) if data.get("avatar_url") else None,
    )


class FeishuProvider(OAuthProviderBase):
    key = "feishu"
    display_name = "飞书"
    pkce = True  # 官方支持:授权阶段 code_challenge(S256),换 token 阶段 code_verifier
    auth_url = "https://accounts.feishu.cn/open-apis/authen/v1/authorize"
    token_url = "https://open.feishu.cn/open-apis/authen/v2/oauth/token"  # noqa: S105  # 端点常量,非密钥
    scope = ""  # 基础登录无需 scope;offline_access 等增量授权非本场景

    def client_id(self, s: Settings) -> str | None:
        return s.feishu_app_id

    def client_secret(self, s: Settings) -> str | None:
        return s.feishu_app_secret

    def redirect_uri(self, s: Settings) -> str:
        return s.feishu_redirect_uri

    async def fetch_raw_profile(self, client: OAuthClient) -> NormalizedProfile:
        raise NotImplementedError("飞书 v2 token 为 JSON 请求体,走 fetch_profile 覆写")

    async def fetch_profile(
        self, s: Settings, *, code: str, code_verifier: str | None
    ) -> NormalizedProfile:
        body: dict[str, str] = {
            "grant_type": "authorization_code",
            "client_id": s.feishu_app_id or "",
            "client_secret": s.feishu_app_secret or "",
            "code": code,
            # 授权请求包含 redirect_uri 时,换取 token 也必须携带该参数。
            "redirect_uri": self.redirect_uri(s),
        }
        if code_verifier is not None:
            body["code_verifier"] = code_verifier
        async with httpx.AsyncClient(timeout=10, trust_env=False) as http:
            tok = resp_json(await http.post(self.token_url, json=body), self.display_name)
            if tok.get("error") or tok.get("code") != 0:
                reason = tok.get("error_description") or tok.get("error", "code 交换异常")
                raise ProviderExchangeFailed(f"飞书登录失败({reason})")
            if not tok.get("access_token"):
                raise ProviderExchangeFailed("飞书登录失败(code 交换异常:响应缺 access_token)")
            info_resp = await http.get(
                "https://open.feishu.cn/open-apis/authen/v1/user_info",
                headers={"Authorization": f"Bearer {tok['access_token']}"},
            )
            info = resp_json(info_resp, self.display_name)
            if info.get("code") != 0:
                raise ProviderExchangeFailed(f"飞书登录失败({info.get('msg', '用户信息拉取异常')})")
        data = info.get("data")
        if not isinstance(data, dict):
            raise ProviderExchangeFailed("飞书登录失败(用户信息响应缺 data)")
        return normalize(cast("dict[str, object]", data))
