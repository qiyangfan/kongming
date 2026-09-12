"""联邦登录(auth spec「联邦登录(连接器形态)」):options/start/state/404 全链路。"""

import asyncio
from collections.abc import Callable
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.models import NormalizedProfile
from kongming.config import get_settings

PROFILE = NormalizedProfile(
    external_id="42",
    username="octocat",
    display_name="The Octocat",
    email=None,
    email_verified=False,
    avatar_url=None,
)


def _query(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


async def test_options_lists_configured_providers(client: AsyncClient) -> None:
    r = await client.get("/auth/options")
    assert r.status_code == 200
    keys = [p["provider"] for p in r.json()["providers"]]
    assert "github" in keys and "google" in keys


async def test_options_filters_unconfigured(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "google_client_id", None)
    r = await client.get("/auth/options")
    keys = [p["provider"] for p in r.json()["providers"]]
    assert "github" in keys and "google" not in keys


async def test_github_start_returns_authorize_url_with_state(client: AsyncClient) -> None:
    r = await client.get("/auth/github/start")
    assert r.status_code == 200
    url = r.json()["authorize_url"]
    assert url.startswith("https://github.com/login/oauth/authorize")
    q = _query(url)
    assert q["client_id"] == "test-gh-id"
    assert q["state"]  # 一次性 state 已下发
    assert "code_challenge" not in q  # github 不声明 PKCE


async def test_google_start_includes_pkce(client: AsyncClient) -> None:
    r = await client.get("/auth/google/start")
    assert r.status_code == 200
    url = r.json()["authorize_url"]
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth")
    q = _query(url)
    assert q["code_challenge"] and q["code_challenge_method"] == "S256"


async def _start_and_callback(client: AsyncClient, provider: str, state: str | None = None):
    """完整 start→callback 链路;可注入伪造 state。"""
    if state is None:
        r = await client.get(f"/auth/{provider}/start")
        state = _query(r.json()["authorize_url"])["state"]
    return await client.post(f"/auth/{provider}/callback", json={"code": "c0de", "state": state})


async def test_callback_success_issues_platform_tokens(
    client: AsyncClient,
    fake_provider: Callable[[str, NormalizedProfile], None],
    session: AsyncSession,
) -> None:

    from kongming.auth.seed import seed_all

    await seed_all(session, get_settings())  # 自动建号需默认角色/租户就位
    fake_provider("github", PROFILE)
    r = await _start_and_callback(client, "github")
    assert r.status_code == 200
    body = r.json()
    # 只含本平台令牌对字段:OAuth access token 永不泄到前端
    assert set(body) == {"access_token", "refresh_token", "token_type", "must_change_password"}
    assert body["token_type"] == "Bearer"
    assert body["must_change_password"] is False  # 联邦账号无本地密码,不适用强制改密
    # 换发的 access token 可用
    r = await client.get("/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert r.status_code == 200
    assert r.json()["username"].startswith("octocat")


async def test_callback_reused_state_rejected(
    client: AsyncClient,
    fake_provider: Callable[[str, NormalizedProfile], None],
    session: AsyncSession,
) -> None:
    from kongming.auth.seed import seed_all

    await seed_all(session, get_settings())
    fake_provider("github", PROFILE)
    r = await client.get("/auth/github/start")
    state = _query(r.json()["authorize_url"])["state"]
    first = await _start_and_callback(client, "github", state)
    assert first.status_code == 200
    second = await _start_and_callback(client, "github", state)  # 同一 state 重放
    assert second.status_code == 401


async def test_callback_forged_state_rejected(
    client: AsyncClient, fake_provider: Callable[[str, NormalizedProfile], None]
) -> None:
    fake_provider("github", PROFILE)
    r = await _start_and_callback(client, "github", state="forged-state")
    assert r.status_code == 401


async def test_callback_expired_state_rejected(
    client: AsyncClient,
    fake_provider: Callable[[str, NormalizedProfile], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_provider("github", PROFILE)
    monkeypatch.setattr(get_settings(), "auth_state_ttl_sec", 1)
    r = await client.get("/auth/github/start")
    state = _query(r.json()["authorize_url"])["state"]
    await asyncio.sleep(1.2)
    r = await _start_and_callback(client, "github", state)
    assert r.status_code == 401


async def test_unknown_provider_404(client: AsyncClient) -> None:
    assert (await client.get("/auth/nosuch/start")).status_code == 404
    r = await client.post("/auth/nosuch/callback", json={"code": "x", "state": "y"})
    assert r.status_code == 404


async def test_unconfigured_provider_404(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "google_client_id", None)
    assert (await client.get("/auth/google/start")).status_code == 404
    r = await client.post("/auth/google/callback", json={"code": "x", "state": "y"})
    assert r.status_code == 404
