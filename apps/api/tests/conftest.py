"""集成测试用独立 PostgreSQL 库与 Redis db15;测试环境变量须在导入 kongming
模块前设置(配置与连接客户端被缓存)。"""

import asyncio
import os
import socket
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
# 测试用运维超管:种子把该Keycloak sub 绑到 SUPER_ADMIN,不创建可登录账号。
os.environ.setdefault("KM_ADMIN_SUBJECT", "00000000-0000-7000-8000-000000000001")
# Keycloak是唯一登录入口:默认未配置,接入场景由 test_auth_keycloak 显式注入
for _k in ("KM_KEYCLOAK_ISSUER", "KM_KEYCLOAK_CLIENT_ID", "KM_KEYCLOAK_CLIENT_SECRET"):
    os.environ.setdefault(_k, "")
os.environ.setdefault("KM_KEYCLOAK_ENABLED", "true")
# 清空精排配置,避免读取本机 .env 后发起真实请求;
# 配置场景由 test_doc_rerank 显式构造 Settings。
for _k in ("KM_RERANK_PROVIDER", "KM_RERANK_API_KEY", "KM_RERANK_MODEL"):
    os.environ.setdefault(_k, "")

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
    "control.snapshot",
    "control.command_receipt",
    "control.usage_outbox",
    "control.registration",
    "control.identity_state",
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
    "public.sys_agent_collab_worker",
    "public.sys_agent_collab_run",
    "public.sys_agent_task_step",
    "public.sys_agent_task",
    "public.tool_http_connectors",
)


last_subject: str | None = None


def login_token(subject: str | None = None) -> str:
    """测试凭据:显式 subject > make_user 最近主体 > KM_ADMIN_SUBJECT。"""
    from kongming.auth import tokens
    from kongming.config import get_settings

    uid = subject or last_subject or get_settings().admin_subject
    if not uid:
        raise RuntimeError("测试未安排主体:先 make_user 或设置 KM_ADMIN_SUBJECT")
    return tokens.issue(uid, "access")


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
def _loopback_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """测试不得发起外网连接:非回环 TCP 目标直接失败,外呼必须显式 mock。"""
    real_connect = socket.socket.connect

    def guarded(self: socket.socket, address: tuple[Any, ...] | str | bytes) -> None:
        host: object = address[0] if isinstance(address, tuple) else address
        # 本机回环与 unix socket(路径形态)放行,其余目标一律视为越界外呼
        if (
            isinstance(host, str)
            and host not in {"127.0.0.1", "::1", "localhost"}
            and not host.startswith("/")
        ):
            raise OSError(f"测试禁止外网连接: {host}")
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)


@pytest.fixture(autouse=True)
async def _drain_tasks() -> AsyncIterator[None]:
    """用例结束后取消仍在跑的后台任务:fire-and-forget 任务跨用例存活会与
    下一条用例的 TRUNCATE/登录观测到中间态(顺序相关的 401/DBAPIError 假红)。"""
    yield
    current = asyncio.current_task()
    pending = [t for t in asyncio.all_tasks() if t is not current and not t.done()]
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


@pytest.fixture(autouse=True)
async def _clean(_migrated_db: None) -> AsyncIterator[None]:  # pyright: ignore[reportUnusedFunction]
    """用例级隔离:TRUNCATE 全部身份/审计表 + Redis 库清空(pytest 按名发现)。"""
    global last_subject
    last_subject = None
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
    from kongming.chat.dependencies import get_agent_client
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

    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c,
    ):
        yield c


MakeUser = Callable[..., Awaitable[str]]


@pytest.fixture
def make_user(session: AsyncSession) -> MakeUser:
    """直插 sys_user 的 arrange 助手(不依赖被测实现,保持测试独立)。

    行 ID 由库生成(等价于Keycloak sub);需要携带身份调用接口时用 bearer_headers。
    """

    async def _make(
        username: str = "dev",
        *,
        status: str = "ACTIVE",
        email: str | None = None,
        nickname: str = "",
    ) -> str:
        uid = (
            await session.execute(
                text(
                    "INSERT INTO public.sys_user (id, username, email, nickname, status)"
                    " VALUES (gen_random_uuid(), :u, :e, :n, :st) RETURNING id"
                ),
                {"u": username, "e": email, "n": nickname, "st": status},
            )
        ).scalar_one()
        await session.commit()
        global last_subject
        last_subject = str(uid)
        return str(uid)

    return _make


@pytest.fixture
def bearer_headers() -> Callable[[str], dict[str, str]]:
    """本地签发 access 令牌(孔明已无账密登录;测试直接经签名模块造凭据)。"""

    def _headers(subject: str) -> dict[str, str]:
        from kongming.auth import tokens

        return {"Authorization": f"Bearer {tokens.issue(subject, 'access')}"}

    return _headers


@pytest.fixture
def fake_keycloak(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[["NormalizedProfile"], None]:
    """把Keycloak provider 替换为返回固定资料的非 OIDC 桩。

    只替换 profile 来源,state 一次性、账号关联与建号规则仍走真实实现。
    """
    from kongming.auth import providers

    def _install(profile: "NormalizedProfile") -> None:
        class _Fake:
            key = "keycloak"
            display_name = "统一身份"
            pkce = False
            oidc = False

            def configured(self, settings: object) -> bool:
                return True

            def authorize_url(
                self, settings: object, *, state: str, code_challenge: str | None = None
            ) -> str:
                return f"https://idp.example/authorize?state={state}"

            async def fetch_profile(
                self, settings: object, *, code: str, code_verifier: str | None = None
            ) -> "NormalizedProfile":
                return profile

            async def end_session_url(self, settings: object) -> str:
                return "https://idp.example/end_session?client_id=kongming"

        monkeypatch.setitem(providers.REGISTRY, "keycloak", _Fake())

    return _install
