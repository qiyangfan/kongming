"""集成测试用独立 PostgreSQL 库与 Redis db15;测试环境变量须在导入 kongming
模块前设置(配置与连接客户端被缓存)。"""

import asyncio
import os
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

API_ROOT = Path(__file__).resolve().parent.parent

os.environ.setdefault(
    "KM_DATABASE_URL",
    "postgresql+asyncpg://kongming:kongming@localhost:5432/kongming_test",
)
# 测试 Redis 启用了密码认证，连接 URL 需要包含密码。
os.environ.setdefault("KM_REDIS_URL", "redis://:redis123@localhost:6379/15")
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
    # 清空精排配置,避免读取本机 .env 后发起真实请求;
    # 配置场景由 test_doc_rerank 显式构造 Settings。
    "KM_RERANK_PROVIDER",
    "KM_RERANK_API_KEY",
    "KM_RERANK_MODEL",
):
    os.environ.setdefault(_k, "")
# 固定三家 redirect_uri,避免本机 .env 指向其他域名。
os.environ.setdefault("KM_WECOM_REDIRECT_URI", "http://localhost:5173/auth/wecom/callback")
os.environ.setdefault("KM_DINGTALK_REDIRECT_URI", "http://localhost:5173/auth/dingtalk/callback")
os.environ.setdefault("KM_FEISHU_REDIRECT_URI", "http://localhost:5173/auth/feishu/callback")
# 启用联邦登录配置,避免本机 .env 改变测试条件。
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

if TYPE_CHECKING:
    from kongming.auth.models import NormalizedProfile

TEST_DB = "kongming_test"

TRUNCATED = (
    "rag.query_history",
    "rag.table_descriptions",
    "public.chat_message",
    "public.chat_session",
    "public.chat_suggestion",
    "public.report_section",
    "public.report_version",
    "public.report",
    "public.sys_oper_log",
    "public.sys_sql_audit",
    "public.user_identities",
    "public.sys_role_permission",
    "public.sys_role_dept",
    "public.sys_user_role",
    "public.sys_user_tenant",
    "public.sys_permission",
    "public.sys_role",
    "public.sys_user",
    "public.sys_dept",
    "public.sys_tenant",
    "public.sys_datasource",
    "public.sys_tool_audit",
    "public.sys_announcement",
    "public.sys_guardrail_audit",
    "public.sys_guardrail_rule",
    "public.sys_llm_usage",
    "public.user_memory",
    "public.user_prefs",
    "rag.table_relations",
    "rag.metric_definitions",
    "rag.document_chunks",
    "rag.documents",
    "public.sys_api_key",
    "public.sys_bot_definition",
    "public.sys_sandbox_run",
    "public.sys_mcp_server",
    "public.sys_workflow_run",
    "public.sys_workflow_def",
    "public.sys_attachment",
    "public.sys_trigger_run",
    "public.sys_trigger_email_cursor",
    "public.sys_trigger_definition",
    "public.sys_team_member",
    "public.sys_team",
    "public.sys_bot_favorite",
    "public.sys_bot_rating",
    "public.sys_bot_version",
    "public.sys_invoice",
    "public.sys_billing_plan",
    "public.sys_quota_policy",
    "public.sys_payment",
    "public.sys_agent_collab_worker",
    "public.sys_agent_collab_run",
    "public.sys_agent_task_step",
    "public.sys_agent_task",
    "public.tool_http_connectors",
)


def _sync_dsn() -> str:
    return os.environ["KM_DATABASE_URL"].replace("+asyncpg", "")


def db_conn() -> dict[str, object]:
    """从 KM_DATABASE_URL 派生本机测试库连接参数(host/port/user/password/db)。

    动态数据源类用例以「同实例异库/异 schema」形态注册数据源,连接参数
    不得硬编码——端口被其他项目占用会集体假败。"""
    from urllib.parse import unquote, urlparse

    p = urlparse(_sync_dsn())
    return {
        "host": p.hostname or "localhost",
        "port": p.port or 5432,
        "username": p.username or "kongming",
        "password": unquote(p.password or ""),
        "database": (p.path or "/").lstrip("/") or "kongming_test",
    }


def _admin_url() -> str:
    """维护库(建测试库用):同主机同凭证,库名换 postgres。"""
    return _sync_dsn().rsplit("/", 1)[0] + "/postgres"


@asynccontextmanager  # pyright: ignore[reportDeprecated]  # async 生成器误选 typeshed 弃用重载
async def migrated_tmp_db() -> AsyncIterator[tuple[AsyncSession, Config]]:
    """为迁移往返测试创建独立临时数据库(用后即删),避免已删除列的目录
    占位在共享测试库累积;返回 (会话, Alembic 配置)。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    tmp_db = f"{TEST_DB}_mig{uuid4().hex[:8]}"
    conn = await cast("Any", asyncpg).connect(_admin_url())
    try:
        await conn.execute(f'CREATE DATABASE "{tmp_db}"')
    finally:
        await conn.close()
    # CREATE 之后全程收进 try/finally:upgrade 失败(恰是 roundtrip 要抓的
    # 形态)也要删除临时库,避免测试运行后残留。
    try:
        # 保留 +asyncpg driver 段:env.py 以 async engine 跑迁移(同步 psycopg2 未装)
        tmp_url = os.environ["KM_DATABASE_URL"].rsplit("/", 1)[0] + f"/{tmp_db}"
        cfg = Config(str(API_ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(API_ROOT / "alembic"))
        cfg.set_main_option("sqlalchemy.url", tmp_url)  # env.py 尊重程序化注入
        # env.py 内部 asyncio.run:事件循环内直调会冲突,经线程隔离
        await asyncio.to_thread(command.upgrade, cfg, "head")
        engine = create_async_engine(tmp_url)
        try:
            maker = async_sessionmaker(engine, expire_on_commit=False)
            async with maker() as s:
                yield s, cfg
        finally:
            await engine.dispose()
    finally:
        conn = await cast("Any", asyncpg).connect(_admin_url())
        try:
            await conn.execute(f'DROP DATABASE "{tmp_db}" WITH (FORCE)')
        finally:
            await conn.close()


@pytest.fixture(scope="session", autouse=True)
def _migrated_db() -> None:  # pyright: ignore[reportUnusedFunction]
    """会话级:缺库则建 + 程序化 alembic upgrade head(pytest 按名发现,非引用访问)。"""

    async def _ensure() -> None:
        # asyncpg 无 py.typed:模块成员推断部分 Unknown,表达式级 cast 收口
        conn = await cast("Any", asyncpg).connect(_admin_url())
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
async def _clean(_migrated_db: None) -> AsyncIterator[None]:  # pyright: ignore[reportUnusedFunction]
    """用例级隔离:TRUNCATE 全部身份/审计表 + Redis 库清空(pytest 按名发现)。"""
    async with get_sessionmaker()() as s:
        await s.execute(text(f"TRUNCATE {', '.join(TRUNCATED)} CASCADE"))
        await s.commit()
    # redis-py flushdb 返回类型标注不全,实例经 cast 收口
    await cast("Any", get_redis()).flushdb()
    yield


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as s:
        yield s


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    from kongming.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
def fake_agent() -> Iterator[dict[str, Any]]:
    """为 MCP 入站和 Web 请求提供可替换的 Agent 客户端。"""
    from kongming.chat.router import get_agent_client
    from kongming.main import app

    holder: dict[str, Any] = {}
    app.dependency_overrides[get_agent_client] = lambda: holder["agent"]
    yield holder
    app.dependency_overrides.pop(get_agent_client, None)


@pytest.fixture
async def mcp_client() -> AsyncIterator[AsyncClient]:
    """带 lifespan 的客户端:SDK 入站 app 依赖会话管理器运行(ASGITransport
    不触发 lifespan),/mcp 入站测试专用(其余测试用免 lifespan 的 client)。"""
    from kongming.main import app

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
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
def fake_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[str, "NormalizedProfile"], None]:
    """将指定联邦 provider 替换为返回固定用户资料的测试实现。"""
    from kongming.auth import providers

    def _install(key: str, profile: "NormalizedProfile") -> None:
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
            ) -> "NormalizedProfile":
                return profile

        monkeypatch.setitem(providers.REGISTRY, key, _Fake())

    return _install
