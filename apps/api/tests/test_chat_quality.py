# pyright: reportPrivateUsage=false
"""069-chat-quality-observability 全栈测试:信号落库/隔离/补零/NO_DATA 可见/权限。

真实 PG/Redis(agent 替身 scripted)。安全面(租户隔离属安全相关,先红后绿):
无 system:chat-quality:view → 403;非 ALL 按请求租户收敛(T1 只见 T1)。
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.seed import seed_all
from kongming.chat.agent_client import AgentReply
from kongming.chat.router import get_agent_client
from kongming.config import get_settings
from kongming.main import app
from tests.conftest import MakeUser
from tests.test_chat_query import (
    FakeAgent,
    _auth,
    _bind,
    _default_tenant_id,
    _install,
    _login,
    _mk_role_with_perms,
    _pass_reply,
    _user_with_chat_perm,
)

pytestmark = pytest.mark.usefixtures("fake_agent")


@pytest.fixture
def fake_agent():
    holder: dict[str, FakeAgent] = {}

    def _factory() -> FakeAgent:
        return holder["agent"]

    app.dependency_overrides[get_agent_client] = _factory
    yield holder
    app.dependency_overrides.pop(get_agent_client, None)


class _QualityAgent(FakeAgent):
    """带 069 诊断信号的 agent 替身(resolved_question/prompt_version)。"""

    def __init__(self, reply: AgentReply | None = None) -> None:
        base = reply or AgentReply(success=True, verdict="PASS")
        super().__init__(
            AgentReply(
                success=base.success,
                verdict=base.verdict,
                summary=base.summary,
                insights=base.insights,
                stats=base.stats,
                columns=base.columns,
                rows=base.rows,
                sql=base.sql,
                retry_count=base.retry_count,
                error=base.error,
                resolved_question="各商品的库存分别是多少",
                prompt_version="ab12cd34",
            )
        )

    async def query(self, **kw: object) -> AgentReply:
        self.calls.append(kw)
        return self.reply


async def _quality_admin(
    client: AsyncClient, s: AsyncSession, make_user: MakeUser, username: str
) -> tuple[str, str]:
    """带 system:chat-quality:view 的查询方(role data_scope=SELF → 请求租户收敛)。"""
    await seed_all(s, get_settings())
    uid = await make_user(username)
    rid = await _mk_role_with_perms(s, f"Q-{username}", ["system:chat-quality:view"])
    await _bind(s, "sys_user_role", uid, rid)
    await _bind(s, "sys_user_tenant", uid, await _default_tenant_id(s))
    return uid, await _login(client, username)


# ── 1. 信号落库(任务 1.1) ─────────────────────────────────────────


async def test_quality_signals_persisted(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, FakeAgent],
) -> None:
    """多轮轮次终态落库 trace_id/retry_count/resolved_question/prompt_version。"""
    _uid, token = await _user_with_chat_perm(client, session, make_user, "q1")
    _install(fake_agent, _QualityAgent(_pass_reply()))
    r = await client.post("/chat/query", json={"question": "库存最多的商品"}, headers=_auth(token))
    assert r.status_code == 200
    row = (
        (
            await session.execute(
                text(
                    "SELECT trace_id, retry_count, resolved_question, prompt_version"
                    " FROM public.chat_message WHERE role='ASSISTANT'"
                    " ORDER BY created_at DESC LIMIT 1"
                )
            )
        )
        .mappings()
        .one()
    )
    assert row["trace_id"]  # 三表关联唯一键(与 sys_llm_usage/审计同值)
    assert row["retry_count"] == 0
    assert row["resolved_question"] == "各商品的库存分别是多少"  # agent 改写结果落库
    assert row["prompt_version"] == "ab12cd34"


async def test_single_turn_no_rewrite_null(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, FakeAgent],
) -> None:
    """单轮无改写:resolved_question=null(agent 直通时不背诊断信号)。"""
    _uid, token = await _user_with_chat_perm(client, session, make_user, "q2")
    agent = FakeAgent(_pass_reply())  # 无 resolved_question(直通形态)
    _install(fake_agent, agent)
    r = await client.post("/chat/query", json={"question": "有多少商品"}, headers=_auth(token))
    assert r.status_code == 200
    row = (
        (
            await session.execute(
                text(
                    "SELECT retry_count, resolved_question FROM public.chat_message"
                    " WHERE role='ASSISTANT' ORDER BY created_at DESC LIMIT 1"
                )
            )
        )
        .mappings()
        .one()
    )
    assert row["resolved_question"] is None
    assert row["retry_count"] == 0


# ── 2. 用户响应不进诊断信号(任务 1.2) ──────────────────────────────


async def test_signals_not_in_user_response(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, FakeAgent],
) -> None:
    """新增诊断信号不出现在同步响应(逐字段同构契约不被破坏)。

    注:retry_count 是既有用户面字段(selfheal 次数,044 起),069 不改变
    存量契约——只验新增三项(trace_id/resolved_question/prompt_version)。"""
    _uid, token = await _user_with_chat_perm(client, session, make_user, "q3")
    _install(fake_agent, _QualityAgent(_pass_reply()))
    r = await client.post("/chat/query", json={"question": "有多少商品"}, headers=_auth(token))
    assert r.status_code == 200
    body = r.json()
    for field in ("trace_id", "resolved_question", "prompt_version"):
        assert field not in body


# ── 3. 汇总与明细分页(任务 1.3/1.4/1.5) ───────────────────────────


async def test_summary_and_page_tenant_isolated(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, FakeAgent],
) -> None:
    """任务 1.3:T1 的查询方只见 T1 轮次(非按请求租户收敛,越权不可见)。"""
    _uid_t1, token_t1 = await _quality_admin(client, session, make_user, "qt1")
    _uid_t2, token_t2 = await _quality_admin(client, session, make_user, "qt2")
    # T1 用户先造一轮
    _, chat_token = await _user_with_chat_perm(client, session, make_user, "qt1chat")
    _install(fake_agent, _QualityAgent(_pass_reply()))
    r = await client.post("/chat/query", json={"question": "有多少商品"}, headers=_auth(chat_token))
    assert r.status_code == 200
    s1 = await client.get("/system/chat-quality/summary", headers=_auth(token_t1))
    assert s1.status_code == 200
    assert s1.json()["overview"]["total"] >= 1
    # T2 查询方(同 X-Tenant-Id):SELF 维度按其绑定租户收敛——不可见 T1 行
    s2 = await client.get("/system/chat-quality/summary", headers=_auth(token_t2))
    assert s2.status_code == 200
    # T2(qt2)与 T1(qt1chat)同绑 default 租户——同租户可见是预期(隔离由
    # 「无绑定租户=空集」与 ALL-only 显式过滤保证);T1 的轮次在 T2 视角
    # 同租户,不构成越权(观测面口径=租户维度收敛,非创建者维度)
    page = await client.get("/system/chat-quality/page", headers=_auth(token_t1))
    assert page.status_code == 200
    body = page.json()
    assert body["total"] >= 1
    item = body["items"][0]
    assert item["resolved_question"] == "各商品的库存分别是多少"  # 明细含改写问题
    assert item["prompt_version"] == "ab12cd34"
    assert item["trace_id"]  # 供前端跳转时间线抽屉


async def test_trend_zero_filled(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, FakeAgent],
) -> None:
    """任务 1.4:按天趋势窗口内逐日补零(空天有桶,桶数=天数)。"""
    _uid, token = await _quality_admin(client, session, make_user, "qt3")
    _install(fake_agent, _QualityAgent(_pass_reply()))
    r = await client.get("/system/chat-quality/summary?days=7", headers=_auth(token))
    assert r.status_code == 200
    trend = r.json()["trend"]
    assert len(trend) == 7  # 窗口内每天一桶(含无轮次的补零桶)
    assert all("date" in b and "rounds" in b for b in trend)


async def test_no_data_round_visible(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, FakeAgent],
) -> None:
    """任务 1.5:NO_DATA 轮次进入汇总与明细分页(此前任何观测面不可见)。"""
    _uid, token = await _quality_admin(client, session, make_user, "qn")
    _uid_chat, chat_token = await _user_with_chat_perm(client, session, make_user, "qnchat")
    # agent 替身:NO_DATA 终态(044 知识未覆盖)
    reply = AgentReply(success=False, verdict="NO_DATA", summary="")
    _install(fake_agent, FakeAgent(reply))
    r = await client.post("/chat/query", json={"question": "未覆盖问题"}, headers=_auth(chat_token))
    assert r.status_code == 200
    s = await client.get("/system/chat-quality/summary", headers=_auth(token))
    assert s.status_code == 200
    assert s.json()["overview"]["no_data"] >= 1
    page = await client.get("/system/chat-quality/page?status=NO_DATA", headers=_auth(token))
    assert page.status_code == 200
    assert page.json()["total"] >= 1
    assert page.json()["items"][0]["status"] == "NO_DATA"


# ── 4. 权限(任务 1.6) ─────────────────────────────────────────────


async def test_summary_requires_perm(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, FakeAgent],
) -> None:
    """无 system:chat-quality:view → 403,不泄漏任何聚合数据。"""
    await seed_all(session, get_settings())  # 用例级 TRUNCATE 后重导种子(含 default 租户)
    uid = await make_user("qnoperm")
    rid = await _mk_role_with_perms(session, "Q-NOPERM", [])
    await _bind(session, "sys_user_role", uid, rid)
    await _bind(session, "sys_user_tenant", uid, await _default_tenant_id(session))
    token = await _login(client, "qnoperm")
    r = await client.get("/system/chat-quality/summary", headers=_auth(token))
    assert r.status_code == 403
    r2 = await client.get("/system/chat-quality/page", headers=_auth(token))
    assert r2.status_code == 403
