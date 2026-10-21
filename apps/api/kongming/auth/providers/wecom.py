"""企业微信扫码登录接入。授权页使用 `qrConnect`，后端按企业微信接口获取 access token 和 userid。

读取详细用户资料需要通讯录权限；无权限时以 userid 作为显示名。未验证的邮箱不参与账号关联。"""

from urllib.parse import urlencode

import httpx

from kongming.auth.models import NormalizedProfile, ProviderExchangeFailed
from kongming.auth.providers.base import OAuthClient, OAuthProviderBase, resp_json
from kongming.config import Settings

_API = "https://qyapi.weixin.qq.com"


def normalize(userinfo: dict[str, object], detail: dict[str, object] | None) -> NormalizedProfile:
    """getuserinfo 响应 + 详档响应(可空)→ 归一化 profile(纯函数,字段映射回归网)。"""
    userid = str(userinfo["userid"])
    name: str | None = None
    avatar: str | None = None
    if detail is not None and detail.get("errcode") == 0:
        if detail.get("name"):
            name = str(detail["name"])
        if detail.get("avatar"):
            avatar = str(detail["avatar"])
    return NormalizedProfile(
        external_id=userid,
        username=userid,
        display_name=name or userid,  # 详档缺失降级:以 UserId 作显示名
        email=None,
        email_verified=False,
        avatar_url=avatar,
    )


class WecomProvider(OAuthProviderBase):
    key = "wecom"
    display_name = "企业微信"
    pkce = False
    auth_url = "https://open.work.weixin.qq.com/wwopen/sso/qrConnect"
    token_url = ""  # noqa: S105  # 非标准两步令牌,fetch_profile 整体覆写,不走 authlib token 流
    scope = ""

    def client_id(self, s: Settings) -> str | None:
        return s.wecom_corpid

    def client_secret(self, s: Settings) -> str | None:
        return s.wecom_secret

    def redirect_uri(self, s: Settings) -> str:
        return s.wecom_redirect_uri

    def configured(self, s: Settings) -> bool:
        return bool(s.wecom_enabled and s.wecom_corpid and s.wecom_secret and s.wecom_agentid)

    def authorize_url(self, s: Settings, *, state: str, code_challenge: str | None) -> str:
        q = urlencode(
            {
                "appid": s.wecom_corpid or "",
                "agentid": s.wecom_agentid or "",
                "redirect_uri": self.redirect_uri(s),
                "state": state,
            }
        )
        return f"{self.auth_url}?{q}"

    async def fetch_raw_profile(self, client: OAuthClient) -> NormalizedProfile:
        raise NotImplementedError("企微为非标准两步令牌,走 fetch_profile 覆写")

    async def fetch_profile(
        self, s: Settings, *, code: str, code_verifier: str | None
    ) -> NormalizedProfile:
        async with httpx.AsyncClient(timeout=10, trust_env=False) as http:
            tok = resp_json(
                await http.get(
                    f"{_API}/cgi-bin/gettoken",
                    params={"corpid": s.wecom_corpid, "corpsecret": s.wecom_secret},
                ),
                self.display_name,
            )
            if tok.get("errcode") != 0:
                raise ProviderExchangeFailed(
                    f"企业微信登录失败({tok.get('errmsg', 'gettoken 异常')})"
                )
            if not tok.get("access_token"):
                raise ProviderExchangeFailed("企业微信登录失败(gettoken 响应缺 access_token)")
            access_token = str(tok["access_token"])
            userinfo = resp_json(
                await http.get(
                    f"{_API}/cgi-bin/auth/getuserinfo",
                    params={"access_token": access_token, "code": code},
                ),
                self.display_name,
            )
            if userinfo.get("errcode") != 0:
                raise ProviderExchangeFailed(
                    f"企业微信登录失败({userinfo.get('errmsg', 'code 交换异常')})"
                )
            # 非企业成员扫码:errcode=0 但无 userid(仅 openid/external_userid)
            if not userinfo.get("userid"):
                raise ProviderExchangeFailed("企业微信登录失败(非企业成员或不在应用可见范围)")
            detail_resp = await http.get(
                f"{_API}/cgi-bin/user/get",
                params={"access_token": access_token, "userid": str(userinfo["userid"])},
            )
            # 详档需应用通讯录查看权限;无权限/畸形响应(errcode!=0/非 JSON)一律降级,不阻断登录
            detail = None
            if detail_resp.status_code == 200:
                try:
                    detail = resp_json(detail_resp, self.display_name)
                except ProviderExchangeFailed:
                    detail = None
        return normalize(userinfo, detail)
