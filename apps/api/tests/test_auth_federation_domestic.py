"""国内三家联邦(003):normalize 字段映射回归网 + options/start 集成。

协议形态按官方文档核验(2026-08-17):
- 企微 qrConnect:getuserinfo 返回小写 userid;详档 user/get 需通讯录权限,缺失降级
- 钉钉统一身份认证:token 为 JSON body;users/me 用 unionId 作跨应用稳定标识;无 PKCE
- 飞书:v2 token 用 client_id/client_secret(JSON);官方支持 PKCE(S256);union_id 同理
"""

from urllib.parse import parse_qs, urlparse

import pytest
from httpx import AsyncClient

from kongming.auth.providers import dingtalk, feishu, wecom
from kongming.config import get_settings


def _query(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


# ---- 企微 normalize:getuserinfo 响应 + 详档(可空)→ profile ----


def test_wecom_normalize_full_detail() -> None:
    userinfo = {"errcode": 0, "errmsg": "ok", "userid": "ZhangSan"}
    detail = {
        "errcode": 0,
        "errmsg": "ok",
        "userid": "ZhangSan",
        "name": "张三",
        "avatar": "https://wework.qpic.cn/head/a.jpg",
        "email": "zhangsan@corp.example",
    }
    p = wecom.normalize(userinfo, detail)
    assert p.external_id == "ZhangSan"
    assert p.username == "ZhangSan"
    assert p.display_name == "张三"
    assert p.avatar_url == "https://wework.qpic.cn/head/a.jpg"
    # email 无验证语义:一律不入管线,永不走 email 关联
    assert p.email is None and p.email_verified is False


def test_wecom_normalize_degrades_without_detail() -> None:
    """无通讯录权限取不到详档:display_name 降级为 UserId,登录链路不断。"""
    userinfo = {"errcode": 0, "errmsg": "ok", "userid": "ZhangSan"}
    p = wecom.normalize(userinfo, None)
    assert p.external_id == "ZhangSan"
    assert p.username == "ZhangSan"
    assert p.display_name == "ZhangSan"
    assert p.avatar_url is None


def test_wecom_normalize_degrades_on_detail_error() -> None:
    """详档返回业务错误(如 60011 无权限)同样走降级分支。"""
    userinfo = {"errcode": 0, "errmsg": "ok", "userid": "ZhangSan"}
    detail = {"errcode": 60011, "errmsg": "no privilege to access/modify contact"}
    p = wecom.normalize(userinfo, detail)
    assert p.external_id == "ZhangSan"
    assert p.display_name == "ZhangSan"
    assert p.avatar_url is None


# ---- 钉钉 normalize:users/me 响应 → profile ----


def test_dingtalk_normalize_uses_union_id() -> None:
    me = {
        "nick": "孔明",
        "unionId": "uni-abc",
        "openId": "open-xyz",
        "avatarUrl": "https://static.dingtalk.com/a.png",
        "mobile": "13800000000",
        "stateCode": "86",
    }
    p = dingtalk.normalize(me)
    assert p.external_id == "uni-abc"  # 跨应用稳定标识,不用 openId(防同企业多应用身份分裂)
    assert p.username == "孔明"
    assert p.display_name == "孔明"
    assert p.avatar_url == "https://static.dingtalk.com/a.png"
    assert p.email is None and p.email_verified is False


def test_dingtalk_normalize_nick_missing_falls_back_to_union_id() -> None:
    p = dingtalk.normalize({"unionId": "uni-abc", "openId": "open-xyz"})
    assert p.external_id == "uni-abc"
    assert p.username == "uni-abc"
    assert p.display_name == "uni-abc"
    assert p.avatar_url is None


# ---- 飞书 normalize:user_info 响应 data → profile ----


def test_feishu_normalize_prefers_en_name_for_username() -> None:
    data = {
        "name": "诸葛亮",
        "en_name": "Zhuge Liang",
        "avatar_url": "https://s3-imfile.feishucdn.com/a.png",
        "open_id": "ou_xxx",
        "union_id": "on_yyy",
        "user_id": "e33ggbyz",
        "email": "zhuge@example.com",
    }
    p = feishu.normalize(data)
    assert p.external_id == "on_yyy"  # union_id,不用 open_id(防多应用身份分裂)
    assert p.username == "Zhuge Liang"  # username 取 en_name
    assert p.display_name == "诸葛亮"
    assert p.avatar_url == "https://s3-imfile.feishucdn.com/a.png"
    assert p.email is None and p.email_verified is False


def test_feishu_normalize_en_name_empty_falls_back_to_name() -> None:
    data = {"name": "诸葛亮", "en_name": "", "union_id": "on_yyy"}
    p = feishu.normalize(data)
    assert p.external_id == "on_yyy"
    assert p.username == "诸葛亮"  # en_name 缺失兜底 name
    assert p.display_name == "诸葛亮"
    assert p.avatar_url is None


# ---- options / start 集成(002 语义 + 三家形态) ----


@pytest.fixture
def domestic_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "wecom_corpid", "ww-test-corp")
    monkeypatch.setattr(s, "wecom_secret", "ww-test-secret")
    monkeypatch.setattr(s, "wecom_agentid", "1000002")
    monkeypatch.setattr(s, "dingtalk_client_id", "dt-test-id")
    monkeypatch.setattr(s, "dingtalk_client_secret", "dt-test-secret")
    monkeypatch.setattr(s, "feishu_app_id", "cli_test_feishu")
    monkeypatch.setattr(s, "feishu_app_secret", "fs-test-secret")


async def test_options_lists_five_when_domestic_configured(
    client: AsyncClient, domestic_configured: None
) -> None:
    r = await client.get("/auth/options")
    assert r.status_code == 200
    keys = [p["provider"] for p in r.json()["providers"]]
    assert keys == ["github", "google", "wecom", "dingtalk", "feishu"]


async def test_wecom_start_url_form(client: AsyncClient, domestic_configured: None) -> None:
    r = await client.get("/auth/wecom/start")
    assert r.status_code == 200
    url = r.json()["authorize_url"]
    assert url.startswith("https://open.work.weixin.qq.com/wwopen/sso/qrConnect?")
    q = _query(url)
    assert q["appid"] == "ww-test-corp"  # qrConnect 参数名是 appid 非 client_id
    assert q["agentid"] == "1000002"
    assert q["redirect_uri"] == "http://localhost:5173/auth/wecom/callback"
    assert q["state"]
    assert "code_challenge" not in q  # 企微无 PKCE


async def test_dingtalk_start_url_form(client: AsyncClient, domestic_configured: None) -> None:
    r = await client.get("/auth/dingtalk/start")
    assert r.status_code == 200
    url = r.json()["authorize_url"]
    assert url.startswith("https://login.dingtalk.com/oauth2/auth?")
    q = _query(url)
    assert q["client_id"] == "dt-test-id"
    assert q["response_type"] == "code"
    assert q["scope"] == "openid"
    assert q["prompt"] == "consent"
    assert q["state"]
    assert "code_challenge" not in q  # 钉钉统一认证官方文档无 PKCE 形态


async def test_feishu_start_url_form(client: AsyncClient, domestic_configured: None) -> None:
    r = await client.get("/auth/feishu/start")
    assert r.status_code == 200
    url = r.json()["authorize_url"]
    assert url.startswith("https://accounts.feishu.cn/open-apis/authen/v1/authorize?")
    q = _query(url)
    assert q["client_id"] == "cli_test_feishu"
    assert q["response_type"] == "code"
    assert q["state"]
    # 飞书官方支持 PKCE:授权阶段 code_challenge,换 token 阶段 code_verifier
    assert q["code_challenge"] and q["code_challenge_method"] == "S256"


async def test_domestic_unconfigured_keeps_404(client: AsyncClient) -> None:
    """002 语义回归:三家已注册但未配置 → start/callback 404,options 不出现。"""
    for key in ("wecom", "dingtalk", "feishu"):
        assert (await client.get(f"/auth/{key}/start")).status_code == 404
        r = await client.post(f"/auth/{key}/callback", json={"code": "x", "state": "y"})
        assert r.status_code == 404
    r = await client.get("/auth/options")
    keys = [p["provider"] for p in r.json()["providers"]]
    assert "wecom" not in keys and "dingtalk" not in keys and "feishu" not in keys


async def test_enabled_switch_overrides_credentials(
    client: AsyncClient, domestic_configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """总开关(003 追加):KM_<P>_ENABLED=false 即时停用,凭证保留;其余家不受影响。
    wecom 覆盖自定义 configured 路径,feishu 覆盖基类 getattr 路径。"""
    s = get_settings()
    monkeypatch.setattr(s, "wecom_enabled", False)
    monkeypatch.setattr(s, "feishu_enabled", False)
    r = await client.get("/auth/options")
    keys = [p["provider"] for p in r.json()["providers"]]
    assert "wecom" not in keys and "feishu" not in keys and "dingtalk" in keys
    assert (await client.get("/auth/wecom/start")).status_code == 404
    assert (await client.get("/auth/feishu/start")).status_code == 404
    r = await client.post("/auth/wecom/callback", json={"code": "x", "state": "y"})
    assert r.status_code == 404
