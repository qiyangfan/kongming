"""测试基建:真实 PG(kongming_test 独立库)+ Redis db15 上的全栈集成测试。

安全规则类(config.yaml rules):登录/锁定/令牌轮换/联邦 state 一律经 HTTP 端到端验证。
env 必须在任何 kongming 导入前指向测试基础设施(get_settings/engine/redis 均 lru_cache 一次固化)。
"""

import asyncio
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

API_ROOT = Path(__file__).resolve().parent.parent

os.environ.setdefault(
    "KM_DATABASE_URL",
    "postgresql+asyncpg://kongming:kongming@localhost:5432/kongming_test",
)
os.environ.setdefault("KM_REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("KM_INTERNAL_TOKEN", "test-internal-token")
# 联邦两家默认已配置;「过滤未配置」场景由各测试用 monkeypatch 摘除
os.environ.setdefault("KM_GITHUB_CLIENT_ID", "test-gh-id")
os.environ.setdefault("KM_GITHUB_CLIENT_SECRET", "test-gh-secret")
os.environ.setdefault("KM_GOOGLE_CLIENT_ID", "test-gg-id")
os.environ.setdefault("KM_GOOGLE_CLIENT_SECRET", "test-gg-secret")
# 国内三家默认"未配置"(空串经 env 优先级盖住本地 .env 的真实凭证,防渗入测试面);
# 「配置后」场景由 domestic 测试 monkeypatch 设置
for _k in (
    "KM_WECOM_CORPID",
    "KM_WECOM_SECRET",
    "KM_WECOM_AGENTID",
    "KM_DINGTALK_CLIENT_ID",
    "KM_DINGTALK_CLIENT_SECRET",
    "KM_FEISHU_APP_ID",
    "KM_FEISHU_APP_SECRET",
):
    os.environ.setdefault(_k, "")
# 三家 redirect_uri 同样钉回默认值(本地 .env 可能指向隧道域名)
os.environ.setdefault("KM_WECOM_REDIRECT_URI", "http://localhost:5173/auth/wecom/callback")
os.environ.setdefault("KM_DINGTALK_REDIRECT_URI", "http://localhost:5173/auth/dingtalk/callback")
os.environ.setdefault("KM_FEISHU_REDIRECT_URI", "http://localhost:5173/auth/feishu/callback")
# 联邦总开关钉为开(本地 .env 可能关停某家,如企微;防渗入测试面)
for _k in (
    "KM_GITHUB_ENABLED",
    "KM_GOOGLE_ENABLED",
    "KM_WECOM_ENABLED",
    "KM_DINGTALK_ENABLED",
    "KM_FEISHU_ENABLED",
):
    os.environ.setdefault(_k, "true")

import asyncpg  # noqa: E402
import pytest  # noqa: E402
from alembic.config import Config  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from alembic import command  # noqa: E402
from kongming.db import get_sessionmaker  # noqa: E402
from kongming.redis_client import get_redis  # noqa: E402

TEST_DB = "kongming_test"

TRUNCATED = (
    "public.sys_oper_log",
    "public.user_identities",
    "public.sys_role_permission",
    "public.sys_user_role",
    "public.sys_user_tenant",
    "public.sys_permission",
    "public.sys_role",
    "public.sys_tenant",
    "public.sys_user",
)


def _sync_dsn() -> str:
    return os.environ["KM_DATABASE_URL"].replace("+asyncpg", "")


def _admin_url() -> str:
    """维护库(建测试库用):同主机同凭证,库名换 postgres。"""
    return _sync_dsn().rsplit("/", 1)[0] + "/postgres"


@pytest.fixture(scope="session", autouse=True)
def _migrated_db() -> None:
    """会话级:缺库则建 + 程序化 alembic upgrade head。"""

    async def _ensure() -> None:
        conn = await asyncpg.connect(_admin_url())
        try:
            exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname=$1", TEST_DB)
            if not exists:
                await conn.execute(f'CREATE DATABASE "{TEST_DB}"')
        finally:
            await conn.close()

    asyncio.run(_ensure())
    cfg = Config(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "alembic"))
    command.upgrade(cfg, "head")


@pytest.fixture(autouse=True)
async def _clean(_migrated_db: None) -> AsyncIterator[None]:
    """用例级隔离:TRUNCATE 全部身份/审计表 + Redis 库清空。"""
    async with get_sessionmaker()() as s:
        await s.execute(text(f"TRUNCATE {', '.join(TRUNCATED)} CASCADE"))
        await s.commit()
    await get_redis().flushdb()
    yield


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as s:
        yield s


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    from kongming.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


MakeUser = Callable[..., Awaitable[str]]


@pytest.fixture
def make_user(session: AsyncSession) -> MakeUser:
    """直插 sys_user 的 arrange 助手(不依赖被测实现,保持测试独立)。"""

    async def _make(
        username: str = "dev",
        password: str | None = "Passw0rd!",
        *,
        status: str = "ACTIVE",
        email: str | None = None,
        nickname: str = "",
        password_changed: bool = True,
    ) -> str:
        from kongming.auth import password as pwd

        ph = pwd.hash_password(password) if password is not None else None
        uid = (
            await session.execute(
                text(
                    "INSERT INTO public.sys_user"
                    " (id, username, email, password_hash, nickname, status, password_changed_at)"
                    " VALUES (gen_random_uuid(), :u, :e, :ph, :n, :st,"
                    "         CASE WHEN :changed THEN now() ELSE NULL END)"
                    " RETURNING id"
                ),
                {
                    "u": username,
                    "e": email,
                    "ph": ph,
                    "n": nickname,
                    "st": status,
                    "changed": password_changed,
                },
            )
        ).scalar_one()
        await session.commit()
        return str(uid)

    return _make


@pytest.fixture
def fake_provider(monkeypatch: pytest.MonkeyPatch) -> Callable[[str, object], None]:
    """联邦 HTTP 交换替身:把 name 为 key 的 provider 换成返回固定 profile 的假插件。

    真插件的 code→token→profile 外呼不进测试;授权 URL 由假插件原样携带 state 返回,
    走通 start→callback 全链路。
    """
    from kongming.auth import providers
    from kongming.auth.models import NormalizedProfile

    def _install(key: str, profile: NormalizedProfile) -> None:
        class _Fake:
            pkce = False
            display_name = key.title()

            def __init__(self) -> None:
                self.key = key

            def configured(self, settings: object) -> bool:
                return True

            def authorize_url(
                self, settings: object, *, state: str, code_challenge: str | None = None
            ) -> str:
                return f"https://fake.example/{key}/authorize?state={state}"

            async def fetch_profile(
                self, settings: object, *, code: str, code_verifier: str | None = None
            ) -> NormalizedProfile:
                return profile

        monkeypatch.setitem(providers.REGISTRY, key, _Fake())

    return _install
