# pyright: reportPrivateUsage=false
"""语音轮关联字段(voice_turn_token)的问答域接线(voice-call「语音触发问答走同一条问答链路」)。

请求纯增可选字段:语音轮凭它绑定在途通话并订阅终态;打字提问不携带、行为逐字不变。
非法字段 400 且不落用户消息、不回调 Agent 面;系统发起取消与用户显式取消等效。
"""

import asyncio
import json
from typing import Any, cast

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.seed import seed_all
from kongming.config import get_settings
from kongming.main import app
from tests.conftest import MakeUser
from tests.test_chat_query import _auth, _bind, _default_tenant_id, _login
from tests.test_chat_stream import StreamFakeAgent, _frames_for, _install, _pass_reply

TID = "t-voice-1"


@pytest.fixture
def fake_agent() -> Any:
    holder: dict[str, StreamFakeAgent] = {}

    def _factory() -> StreamFakeAgent:
        return holder["agent"]

    from kongming.chat.dependencies import get_agent_client

    app.dependency_overrides[get_agent_client] = _factory
    yield holder
    app.dependency_overrides.pop(get_agent_client, None)


async def _user(client: AsyncClient, s: AsyncSession, make_user: MakeUser, name: str) -> str:
    await seed_all(s, get_settings())
    uid = await make_user(name)
    rid = (
        await s.execute(text("SELECT id FROM public.sys_role WHERE role_key='DEVELOPER'"))
    ).scalar_one()
    await _bind(s, "sys_user_role", uid, str(rid))
    await _bind(s, "sys_user_tenant", uid, await _default_tenant_id(s))
    await s.commit()
    return _login(uid)


async def test_voice_turn_binds_and_settles(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """合法关联字段:校验通过,trace 绑定,终态回填(取消也可代为发起)。"""
    import kongming.chat.dependencies as deps

    class _Registry:
        def __init__(self) -> None:
            self.validated: list[tuple[str, str]] = []
            self.bound: list[tuple[str, str]] = []
            self.settled: list[tuple[str, str]] = []

        def validate(self, *, token: str, user_id: str) -> bool:
            self.validated.append((token, user_id))
            return True

        def bind(self, *, token: str, trace_id: str) -> bool:
            self.bound.append((token, trace_id))
            return True

        def settle(self, *, trace_id: str, user_id: str, outcome: object) -> bool:
            verdict = cast("Any", outcome).get("verdict") if isinstance(outcome, dict) else None
            self.settled.append((trace_id, str(verdict)))
            return True

    registry = _Registry()
    monkeypatch.setattr(deps, "_VOICE_REGISTRY", registry)
    reply = _pass_reply()
    _install(fake_agent, StreamFakeAgent(_frames_for(reply), reply=reply))
    token = await _user(client, session, make_user, "vt-ok")

    r = await client.post(
        "/chat/query/stream",
        json={"question": "有多少商品", "voice_turn_token": "vt-tok"},
        headers={**_auth(token), "X-Trace-Id": TID},
    )
    assert r.status_code == 200
    assert ("vt-tok", uid_of(token)) in registry.validated
    assert registry.bound and registry.bound[0][0] == "vt-tok"
    assert registry.bound[0][1] == TID
    assert registry.settled and registry.settled[0] == (TID, "PASS")


def uid_of(token: str) -> str:
    import jwt

    claims = jwt.decode(token, options={"verify_signature": False})
    return str(claims["sub"])


async def test_voice_turn_invalid_returns_400_without_side_effects(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """字段不存在/已使用/非本人:400,不落用户消息、不回调 Agent 面。"""
    import kongming.chat.dependencies as deps

    class _Registry:
        def validate(self, *, token: str, user_id: str) -> bool:
            return token == "good-token"

        def bind(self, *, token: str, trace_id: str) -> bool:
            return True

        def settle(self, *, trace_id: str, user_id: str, outcome: object) -> bool:
            return True

    monkeypatch.setattr(deps, "_VOICE_REGISTRY", _Registry())
    token = await _user(client, session, make_user, "vt-bad")
    _install(fake_agent, StreamFakeAgent(_frames_for(_pass_reply()), reply=_pass_reply()))
    for tok in ("no-such", "used-once", "not-mine"):
        r = await client.post(
            "/chat/query/stream",
            json={"question": "有多少商品", "voice_turn_token": tok},
            headers={**_auth(token), "X-Trace-Id": f"{TID}-{tok}"},
        )
        assert r.status_code == 400, (tok, r.status_code)
    rows = (await session.execute(text("SELECT count(*) FROM public.chat_message"))).scalar_one()
    assert rows == 0  # 未落任何消息
    agent = fake_agent.get("agent")
    assert agent is None or not agent.stream_calls  # 未回调 Agent 面


async def test_typing_request_unchanged_without_field(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: Any,
) -> None:
    """不带关联字段:请求校验、编排与响应逐字不变(零回归)。"""
    reply = _pass_reply()
    _install(fake_agent, StreamFakeAgent(_frames_for(reply), reply=reply))
    token = await _user(client, session, make_user, "vt-plain")
    r = await client.post(
        "/chat/query/stream",
        json={"question": "有多少商品"},
        headers={**_auth(token), "X-Trace-Id": TID},
    )
    assert r.status_code == 200
    frames = [json.loads(ln[6:]) for ln in r.text.splitlines() if ln.startswith("data: ")]
    assert frames[-1]["type"] == "result"
    assert frames[-1]["data"]["success"] is True


async def test_settle_rejects_same_trace_from_other_user() -> None:
    """trace 可由请求头指定:回填必须同时匹配用户,不能把别人的结果串给本人。"""
    from kongming.voice.binding import VoiceTurnRegistry

    registry = VoiceTurnRegistry()
    tok_u1 = registry.issue(user_id="u1", call_id="c1")
    tok_u2 = registry.issue(user_id="u2", call_id="c2")
    assert registry.validate(token=tok_u1, user_id="u1")
    assert registry.validate(token=tok_u2, user_id="u2")
    assert registry.bind(token=tok_u1, trace_id="same-trace")
    assert registry.bind(token=tok_u2, trace_id="same-trace")
    assert registry.settle(trace_id="same-trace", user_id="u2", outcome={"verdict": "PASS"})
    assert await registry.wait_terminal(token=tok_u1, timeout_sec=0.01) is None
    assert registry.settle(trace_id="same-trace", user_id="u1", outcome={"verdict": "PASS"})
    assert cast("dict[str, Any]", await registry.wait_terminal(token=tok_u1))["verdict"] == "PASS"


async def test_registry_full_lifecycle_with_system_cancel() -> None:
    """注册表全生命周期(纯单测):签发→校验→绑定→终态;已用 token 再验被拒。"""
    from kongming.voice.binding import VoiceTurnRegistry

    registry = VoiceTurnRegistry()
    tok = registry.issue(user_id="u1", call_id="c1")
    assert registry.validate(token=tok, user_id="u1") is True
    assert registry.validate(token=tok, user_id="u1") is False  # 已用:防重放
    assert registry.bind(token=tok, trace_id=TID) is True
    assert registry.settle(trace_id=TID, user_id="u1", outcome={"verdict": "PASS"}) is True
    outcome = await registry.wait_terminal(token=tok, timeout_sec=1)
    assert cast("dict[str, Any]", outcome)["verdict"] == "PASS"
    _ = asyncio  # 保持异步形态(事件等待已在 wait_terminal 内)


async def test_drop_call_unblocks_pending_waiters() -> None:
    """通话结束清理关联字段时,仍在 wait_terminal/wait_bound 的任务须立即唤醒。"""
    from kongming.voice.binding import VoiceTurnRegistry

    registry = VoiceTurnRegistry()
    tok = registry.issue(user_id="u1", call_id="c-drop")
    assert registry.validate(token=tok, user_id="u1") is True
    waiter = asyncio.create_task(registry.wait_terminal(token=tok))
    await asyncio.sleep(0)
    registry.drop_call(call_id="c-drop")
    outcome = await asyncio.wait_for(waiter, timeout=1.0)
    assert cast("dict[str, Any]", outcome)["verdict"] == "CANCELLED"


class _HangingAgent:
    """出一帧后挂起的流式替身:abort_query 放行并正常收尾(供取消竞态驱动)。"""

    def __init__(self) -> None:
        self.release = asyncio.Event()

    async def query_stream(self, *, on_frame: Any, **kw: object) -> None:
        await on_frame(
            "stage", json.dumps({"node": "understand", "status": "running", "retry_count": 0})
        )
        await self.release.wait()

    async def abort_query(self, *, trace_id: str) -> bool:
        self.release.set()
        return True


def _registry_recorder() -> Any:
    """记录 validate/bind/settle 的替身注册表。"""

    class _Registry:
        def __init__(self) -> None:
            self.settled: list[tuple[str, str]] = []

        def validate(self, *, token: str, user_id: str) -> bool:
            return token == "vt-tok"

        def bind(self, *, token: str, trace_id: str) -> bool:
            return True

        def settle(self, *, trace_id: str, user_id: str, outcome: object) -> bool:
            verdict = cast("Any", outcome).get("verdict") if isinstance(outcome, dict) else None
            self.settled.append((trace_id, str(verdict)))
            return True

    return _Registry()


async def test_voice_turn_settles_cancelled_when_turn_cancelled(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """语音轮被取消(新问顶掉旧轮):取消收敛也回填关联字段(CANCELLED)。"""
    import kongming.chat.dependencies as deps

    agent = _HangingAgent()
    _install(fake_agent, agent)  # type: ignore[arg-type]
    registry = _registry_recorder()
    monkeypatch.setattr(deps, "_VOICE_REGISTRY", registry)
    token = await _user(client, session, make_user, "vt-cancel")

    # ASGI 传输会读完整个响应体:取消以并行任务发起,主请求等流自然收口
    async def _cancel_later() -> Any:
        await asyncio.sleep(0.5)  # stage 已出,轮在途
        return await client.post(f"/chat/query/stream/{TID}/cancel", headers=_auth(token))

    cancel_task = asyncio.create_task(_cancel_later())
    r = await client.post(
        "/chat/query/stream",
        json={"question": "有多少商品", "voice_turn_token": "vt-tok"},
        headers={**_auth(token), "X-Trace-Id": TID},
    )
    settled = await asyncio.wait_for(cancel_task, 10)
    assert settled.status_code == 200
    assert "已取消" in r.text
    assert registry.settled and registry.settled[-1] == (TID, "CANCELLED")


async def test_voice_turn_settles_failed_when_agent_stream_breaks(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """agent 流中断(传输失败收敛):失败终态也回填关联字段(FAILED)。"""
    import kongming.chat.dependencies as deps
    from kongming.chat.agent_client import AgentUnavailable

    class _BrokenAgent:
        async def query_stream(self, *, on_frame: Any, **kw: object) -> None:
            await on_frame(
                "stage", json.dumps({"node": "understand", "status": "running", "retry_count": 0})
            )
            raise AgentUnavailable("stream broken")

    _install(fake_agent, _BrokenAgent())  # type: ignore[arg-type]
    registry = _registry_recorder()
    monkeypatch.setattr(deps, "_VOICE_REGISTRY", registry)
    token = await _user(client, session, make_user, "vt-broken")

    r = await client.post(
        "/chat/query/stream",
        json={"question": "有多少商品", "voice_turn_token": "vt-tok"},
        headers={**_auth(token), "X-Trace-Id": TID},
    )
    assert r.status_code == 200
    assert "error" in r.text
    assert registry.settled and registry.settled[-1] == (TID, "FAILED")


async def test_voice_turn_settles_on_memory_short_circuit(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """语音问句命中记忆短路(不经后台消费):终态同样回填关联字段。"""
    import kongming.chat.dependencies as deps

    _install(fake_agent, StreamFakeAgent(_frames_for(_pass_reply()), reply=_pass_reply()))
    registry = _registry_recorder()
    monkeypatch.setattr(deps, "_VOICE_REGISTRY", registry)
    token = await _user(client, session, make_user, "vt-memory")

    r = await client.post(
        "/chat/query/stream",
        json={"question": "请记住:我看报表只关心月度口径", "voice_turn_token": "vt-tok"},
        headers={**_auth(token), "X-Trace-Id": TID},
    )
    assert r.status_code == 200
    assert "已记住" in r.text
    assert registry.settled and registry.settled[-1] == (TID, "PASS")
