"""chat 019 集成测试:agent_mode 分支/ReAct 工具注入/pending_tool_calls 聚合。

FakeAgent 替身记录 react_tools 透传;pending 聚合用真实 sys_tool_audit 行
(PENDING_CONFIRM 经独立事务提交,聚合读取独立短会话——与生产同形态)。
"""
# pyright: reportPrivateUsage=false

import json
from collections.abc import Callable, Coroutine, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.seed import seed_all
from kongming.chat.agent_client import AgentReply
from kongming.chat.router import get_agent_client
from kongming.config import get_settings
from kongming.db import get_sessionmaker
from kongming.main import app
from tests.conftest import MakeUser
from tests.test_chat_query import _auth, _bind, _default_tenant_id, _login


class FakeAgent:
    def __init__(self, reply: AgentReply | None = None) -> None:
        self.reply = reply or AgentReply(success=True, verdict="PASS", summary="好")
        self.calls: list[dict[str, object]] = []
        self.stream_calls: list[dict[str, object]] = []

    async def query(self, **kw: object) -> AgentReply:
        self.calls.append(kw)
        return self.reply

    async def query_stream(self, **kw: object) -> None:
        self.stream_calls.append(kw)
        # 生产签名 StreamFrameHandler = Callable[[str, str], Coroutine[Any, Any, None]]
        on_frame = cast("Callable[[str, str], Coroutine[Any, Any, None]]", kw["on_frame"])
        # 019 流式:tool_call 帧 + result 帧(终态与同步同构 JSON)
        import dataclasses

        await on_frame(
            "tool_call",
            json.dumps({"tool_name": "execute_sql", "status": "running"}),
        )
        await on_frame(
            "tool_call",
            json.dumps({"tool_name": "execute_sql", "status": "done"}),
        )
        await on_frame("result", json.dumps(dataclasses.asdict(self.reply)))

    async def rewrite_title(self, *, question: str) -> str | None:
        return None


class _PendingInsertAgent(FakeAgent):
    """query 被调时按捕获的 trace_id 插一条 PENDING_CONFIRM(模拟 agent 写工具挂起)。"""

    async def query(self, **kw: object) -> AgentReply:
        await super().query(**kw)
        async with get_sessionmaker()() as s:
            # 聚合读「已提交」行:独立事务提交(与生产 writer 同形态)
            await s.execute(
                text(
                    "INSERT INTO public.sys_tool_audit"
                    " (id, trace_id, user_id, tenant_id, tool_name, args_digest, status,"
                    "  expires_at, created_at)"
                    " VALUES (gen_random_uuid(), :t, :u, :t2, 'create_system_announcement',"
                    "         '{\"title\": \"明日维护\"}', 'PENDING_CONFIRM', :exp, now())"
                ),
                {
                    "t": kw["trace_id"],
                    "u": kw["user_id"],
                    "t2": await self._tenant_id(s),
                    "exp": datetime.now(UTC) + timedelta(minutes=15),
                },
            )
            await s.commit()
        return self.reply

    @staticmethod
    async def _tenant_id(s: AsyncSession) -> str:
        return str(
            (
                await s.execute(text("SELECT id FROM public.sys_tenant WHERE tenant_key='default'"))
            ).scalar_one()
        )


@pytest.fixture
def fake_agent() -> Iterator[dict[str, FakeAgent]]:
    holder: dict[str, FakeAgent] = {}
    app.dependency_overrides[get_agent_client] = lambda: holder["agent"]
    yield holder
    app.dependency_overrides.pop(get_agent_client, None)


async def _user(
    client: AsyncClient, s: AsyncSession, make_user: MakeUser, username: str
) -> tuple[str, str]:
    """DEVELOPER(含三件套工具权限)+ default 租户,返回 (user_id, token)。"""
    await seed_all(s, get_settings())
    uid = await make_user(username)
    rid = (
        await s.execute(text("SELECT id FROM public.sys_role WHERE role_key='DEVELOPER'"))
    ).scalar_one()
    await _bind(s, "sys_user_role", uid, str(rid))
    await _bind(s, "sys_user_tenant", uid, await _default_tenant_id(s))
    return uid, await _login(client, username)


# ============ agent_mode 分支 ============


async def test_default_recipe_sends_no_tools(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, FakeAgent],
) -> None:
    _, token = await _user(client, session, make_user, "am_recipe")
    fake_agent["agent"] = FakeAgent()
    r = await client.post("/chat/query", json={"question": "有多少商品"}, headers=_auth(token))
    assert r.status_code == 200
    assert fake_agent["agent"].calls[0]["react_tools"] is None  # 老链路零改动


async def test_react_injects_authorized_tools(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, FakeAgent],
) -> None:
    _, token = await _user(client, session, make_user, "am_react")
    fake_agent["agent"] = FakeAgent()
    r = await client.post(
        "/chat/query",
        json={"question": "有多少商品", "agent_mode": "react"},
        headers=_auth(token),
    )
    assert r.status_code == 200
    tools_any = fake_agent["agent"].calls[0]["react_tools"]
    tools = cast("list[dict[str, Any]]", tools_any)
    # isinstance 守卫保持在原始对象上(运行时语义不变)
    assert isinstance(tools_any, list) and len(tools) == 3  # DEVELOPER 只读三件套
    names = {t["name"] for t in tools}
    assert names == {"validate_sql", "execute_sql", "rag_retrieve"}
    # 注入 schema 形态:name/description/parameters/write/require_confirm
    for t in tools:
        assert t["description"] and t["parameters"]["type"] == "object"
        assert t["write"] is False and t["require_confirm"] is False


async def test_react_no_tool_perms_injects_empty(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, FakeAgent],
) -> None:
    """无工具权限点用户走 react:注入空工具集(退化纯文本,不报错)。"""
    await seed_all(session, get_settings())
    uid = await make_user("am_noperm")
    from tests.test_chat_query import _mk_role_with_perms

    rid = await _mk_role_with_perms(session, "NO_TOOL", ["chat:execute"])
    await _bind(session, "sys_user_role", uid, rid)
    await _bind(session, "sys_user_tenant", uid, await _default_tenant_id(session))
    token = await _login(client, "am_noperm")
    fake_agent["agent"] = FakeAgent()
    r = await client.post(
        "/chat/query",
        json={"question": "你好", "agent_mode": "react"},
        headers=_auth(token),
    )
    assert r.status_code == 200
    assert fake_agent["agent"].calls[0]["react_tools"] == []


async def test_invalid_agent_mode_422(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, FakeAgent],
) -> None:
    _, token = await _user(client, session, make_user, "am_bad")
    fake_agent["agent"] = FakeAgent()
    r = await client.post(
        "/chat/query",
        json={"question": "有多少商品", "agent_mode": "turbo"},
        headers=_auth(token),
    )
    assert r.status_code == 422


async def test_pending_tool_calls_aggregated(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, FakeAgent],
) -> None:
    """写工具挂起:响应 pending_tool_calls 聚合本 trace 的 PENDING_CONFIRM 行。"""
    _, token = await _user(client, session, make_user, "am_pending")
    fake_agent["agent"] = _PendingInsertAgent()
    r = await client.post(
        "/chat/query",
        json={"question": "发布公告", "agent_mode": "react"},
        headers=_auth(token),
    )
    assert r.status_code == 200
    pending = r.json()["pending_tool_calls"]
    assert len(pending) == 1
    assert pending[0]["tool_name"] == "create_system_announcement"
    assert pending[0]["confirm_id"]
    assert "明日维护" in pending[0]["args_digest"]


async def test_pending_empty_on_recipe(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, FakeAgent],
) -> None:
    _, token = await _user(client, session, make_user, "am_empty")
    fake_agent["agent"] = FakeAgent()
    r = await client.post("/chat/query", json={"question": "有多少商品"}, headers=_auth(token))
    assert r.json()["pending_tool_calls"] == []


async def test_react_stream_tool_call_frames(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, FakeAgent],
) -> None:
    """流式 react:tool_call 事件在白名单内透传,result 终态照常。"""
    _, token = await _user(client, session, make_user, "am_stream")
    fake_agent["agent"] = FakeAgent()
    r = await client.post(
        "/chat/query/stream",
        json={"question": "有多少商品", "agent_mode": "react"},
        headers=_auth(token),
    )
    assert r.status_code == 200
    events = [ln[7:] for ln in r.text.splitlines() if ln.startswith("event: ")]
    assert events.count("tool_call") == 2  # running + done 透传
    assert events[-1] == "result"
    stream_kw = fake_agent["agent"].stream_calls[0]
    # cast 仅定型,收窄后的成员访问不再带 Unknown(运行时不变)
    assert (
        isinstance(stream_kw["react_tools"], list)
        and len(cast("list[object]", stream_kw["react_tools"])) == 3
    )
