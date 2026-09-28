"""流式问答端点测试(017 query-streaming;chat spec「流式问答端点」)。

真实 PG/Redis;agent 面经 dependency_overrides 替身(scripted SSE 帧)。
覆盖:事件中继/终态一致性(result 与同步逐字段一致)/REJECTED 走 result/
传输失败 error 终态+FAILED 落库/persist=false/前段校验失败常规 JSON/
断线续存(直调 service:前端不消费,后台任务照常落库)/心跳分支/SSE 解码器。
"""
# pyright: reportPrivateUsage=false

import asyncio
import json
from collections.abc import Callable, Coroutine, Iterator
from typing import Any, cast

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.seed import seed_all
from kongming.chat import router as chat_router
from kongming.chat.agent_client import AgentReply, AgentUnavailable
from kongming.chat.repository import ChatRepository
from kongming.chat.router import get_agent_client
from kongming.chat.service import ChatService, StreamHandle
from kongming.chat.sse import SseDecoder
from kongming.config import get_settings
from kongming.datasource.repository import SqlDatasourceRepo
from kongming.db import get_sessionmaker
from kongming.main import app
from tests.conftest import MakeUser
from tests.test_chat_query import (
    _auth,
    _bind,
    _default_tenant_id,
    _login,
    _mk_role_with_perms,
    _pass_reply,
)


class StreamFakeAgent:
    """scripted 流式替身:按帧序列回调 on_frame;同步 query 供终态一致性对照。"""

    def __init__(
        self,
        frames: list[tuple[str, str]] | None = None,
        *,
        reply: AgentReply | None = None,
        boom: bool = False,
        crash: bool = False,
        crash_after_frames: bool = False,
    ) -> None:
        self.frames = frames or []
        self.reply = reply or _pass_reply()
        self.boom = boom
        self.crash = crash  # 非 AgentUnavailable 的意外异常(R1-S1)
        # 全部帧发完后读尾异常(R3-R1:result 已落库,后置传输错误不得叠加 FAILED)
        self.crash_after_frames = crash_after_frames
        self.stream_calls: list[dict[str, object]] = []
        self.title_reply: str | None = None

    async def query(self, **kw: object) -> AgentReply:
        return self.reply

    async def query_stream(
        self, *, on_frame: Callable[[str, str], Coroutine[Any, Any, None]], **kw: object
    ) -> None:
        self.stream_calls.append(kw)
        if self.boom:
            raise AgentUnavailable("connect failed")
        if self.crash:
            raise RuntimeError("unexpected mid-stream crash")
        for event, data in self.frames:
            await on_frame(event, data)
        if self.crash_after_frames:
            raise AgentUnavailable("connection reset after terminal frame")

    async def rewrite_title(self, *, question: str) -> str | None:
        return self.title_reply


def _frames_for(reply: AgentReply) -> list[tuple[str, str]]:
    """成功链路的 scripted 帧序列(stage 对+sql+summary+result)。"""
    frames: list[tuple[str, str]] = []
    for node in ("understand", "retrieve", "generate"):
        for st in ("running", "done"):
            frames.append(("stage", json.dumps({"node": node, "status": st, "retry_count": 0})))
    for st in ("running", "done"):
        frames.append(("stage", json.dumps({"node": "validate", "status": st, "retry_count": 0})))
    frames.append(("sql", json.dumps({"sql": reply.sql})))
    for st in ("running", "done"):
        frames.append(("stage", json.dumps({"node": "execute", "status": st, "retry_count": 0})))
    frames.append(("summary", json.dumps({"delta": reply.summary})))
    frames.append(("result", json.dumps(reply.__dict__)))
    return frames


@pytest.fixture
def fake_agent() -> Iterator[dict[str, StreamFakeAgent]]:
    holder: dict[str, StreamFakeAgent] = {}

    def _factory() -> StreamFakeAgent:
        return holder["agent"]

    app.dependency_overrides[get_agent_client] = _factory
    yield holder
    app.dependency_overrides.pop(get_agent_client, None)


def _install(holder: dict[str, StreamFakeAgent], agent: StreamFakeAgent) -> None:
    holder["agent"] = agent


async def _user_with_chat_perm(
    client: AsyncClient, s: AsyncSession, make_user: MakeUser, username: str = "qs"
) -> tuple[str, str]:
    await seed_all(s, get_settings())
    uid = await make_user(username)
    rid = (
        await s.execute(text("SELECT id FROM public.sys_role WHERE role_key='DEVELOPER'"))
    ).scalar_one()
    await _bind(s, "sys_user_role", uid, str(rid))
    await _bind(s, "sys_user_tenant", uid, await _default_tenant_id(s))
    return uid, await _login(client, username)


def _parse_sse(text: str) -> list[tuple[str, dict[str, object]]]:
    frames: list[tuple[str, dict[str, object]]] = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block or block.startswith(":"):
            continue
        event = ""
        data_lines: list[str] = []
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:") :].strip())
        if event:
            frames.append((event, json.loads("\n".join(data_lines))))
    return frames


# ============ SseDecoder 纯函数 ============


def test_sse_decoder_basic_frame() -> None:
    d = SseDecoder()
    assert d.feed("event: stage") is None
    assert d.feed('data: {"node":"a"}') is None
    assert d.feed("") == ("stage", '{"node":"a"}')
    assert d.feed("") is None  # 连续空行跳过


def test_sse_decoder_comment_and_multi_data() -> None:
    d = SseDecoder()
    assert d.feed(": ping") is None
    assert d.feed("event: x") is None
    assert d.feed("data: a") is None
    assert d.feed("data: b") is None
    assert d.feed("") == ("x", "a\nb")


def test_sse_decoder_data_space_stripping() -> None:
    d = SseDecoder()
    d.feed("data: 值")
    assert d.feed("") == ("", "值")
    d2 = SseDecoder()
    d2.feed("data:值2")  # 无空格也接受
    assert d2.feed("") == ("", "值2")


# ============ HTTP 面 ============


async def test_stream_happy_path_full_relay(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, StreamFakeAgent],
) -> None:
    """全链:stage/sql/summary 透传+result 富化(与同步逐字段一致)+双消息落库。"""
    reply = _pass_reply()
    first_agent = StreamFakeAgent(_frames_for(reply), reply=reply)
    _install(fake_agent, first_agent)
    _uid, token = await _user_with_chat_perm(client, session, make_user, "stream1")
    r = await client.post(
        "/chat/query/stream", json={"question": "有多少商品"}, headers=_auth(token)
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.headers["cache-control"] == "no-cache"
    frames = _parse_sse(r.text)
    events = [e for e, _ in frames]
    # 过程事件透传(sql/summary/stage)
    assert events[:1] == ["stage"]
    assert "sql" in events and "summary" in events
    assert events[-1] == "result" and events.count("result") == 1
    assert "error" not in events
    # result 富化:chart 推断+message_id+session_id(同步对照)
    sync = StreamFakeAgent(reply=reply)
    _install(fake_agent, sync)
    r2 = await client.post("/chat/query", json={"question": "有多少商品"}, headers=_auth(token))
    # session/message id 属请求实例产物(两次请求各自新建),剔除后逐字段一致
    streamed = dict(frames[-1][1])
    synced = dict(r2.json())
    for k in ("session_id", "message_id"):
        assert streamed.pop(k) is not None  # 两路都应携带落库 id
        synced.pop(k)
    assert streamed == synced
    # 落库:USER+ASSISTANT 两条
    sid = frames[-1][1]["session_id"]
    rows = await session.execute(
        text(
            "SELECT role, status FROM public.chat_message WHERE session_id=:s ORDER BY created_at"
        ),
        {"s": sid},
    )
    msgs = [(str(r[0]), str(r[1])) for r in rows]
    assert msgs == [("USER", "SUCCESS"), ("ASSISTANT", "SUCCESS")]
    # agent 收到完整回调参数(前段共用直证:history/tenant/trace)
    assert first_agent.stream_calls[0]["question"] == "有多少商品"


async def test_stream_rejected_walks_result_not_error(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, StreamFakeAgent],
) -> None:
    """业务拒绝(权限类拦截)终态走 result(success=false),不走 error。"""
    rejected = AgentReply(
        success=False, verdict="BLOCKED", sql="DELETE FROM goods", error="BLOCKED_ROOT: Delete"
    )
    frames = [
        ("stage", json.dumps({"node": "understand", "status": "done", "retry_count": 0})),
        ("result", json.dumps(rejected.__dict__)),
    ]
    _install(fake_agent, StreamFakeAgent(frames, reply=rejected))
    _, token = await _user_with_chat_perm(client, session, make_user, "stream2")
    r = await client.post("/chat/query/stream", json={"question": "删掉商品"}, headers=_auth(token))
    frames_out = _parse_sse(r.text)
    events = [e for e, _ in frames_out]
    assert events[-1] == "result" and "error" not in events
    assert frames_out[-1][1]["success"] is False
    assert "已被拒绝" in str(frames_out[-1][1]["error"])
    # 落库 REJECTED
    sid = frames_out[-1][1]["session_id"]
    status = await session.execute(
        text("SELECT status FROM public.chat_message WHERE session_id=:s AND role='ASSISTANT'"),
        {"s": sid},
    )
    assert str(status.scalar_one()) == "REJECTED"


async def test_stream_agent_unavailable_error_terminal(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, StreamFakeAgent],
) -> None:
    """传输层失败:error 终态事件+FAILED 消息落库(友好文案不暴露内部)。"""
    _install(fake_agent, StreamFakeAgent(boom=True))
    _, token = await _user_with_chat_perm(client, session, make_user, "stream3")
    r = await client.post(
        "/chat/query/stream", json={"question": "有多少商品"}, headers=_auth(token)
    )
    frames = _parse_sse(r.text)
    events = [e for e, _ in frames]
    assert events[-1] == "error" and "result" not in events
    assert frames[-1][1]["error"] == "问答服务暂不可用,请稍后重试"
    sid_rows = await session.execute(
        text(
            "SELECT s.id FROM public.chat_session s JOIN public.chat_message m ON m.session_id=s.id"
            " WHERE m.role='ASSISTANT' AND m.status='FAILED'"
        )
    )
    assert sid_rows.scalar_one() is not None


async def test_stream_unexpected_crash_still_terminates(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, StreamFakeAgent],
) -> None:
    """R1-S1 直证:非 AgentUnavailable 的意外异常(如中途 RuntimeError)也收敛
    error 终态+FAILED 落库——终止标记必达,前端不悬挂。"""
    _install(fake_agent, StreamFakeAgent(crash=True))
    _, token = await _user_with_chat_perm(client, session, make_user, "stream3b")
    r = await client.post("/chat/query/stream", json={"question": "q"}, headers=_auth(token))
    frames = _parse_sse(r.text)
    assert [e for e, _ in frames] == ["error"]
    failed = await session.execute(
        text(
            "SELECT count(*) FROM public.chat_message WHERE role='ASSISTANT' AND status='FAILED'"
            " AND summary='问答服务暂不可用,请稍后重试'"
        )
    )
    assert failed.scalar_one() == 1


async def test_stream_missing_terminal_frame_converges_error(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, StreamFakeAgent],
) -> None:
    """R3:agent 流正常结束但缺终态帧(契约破坏)→ error 终态+FAILED 落库。"""
    frames_in = [
        ("stage", json.dumps({"node": "understand", "status": "done", "retry_count": 0})),
    ]
    _install(fake_agent, StreamFakeAgent(frames_in))
    _, token = await _user_with_chat_perm(client, session, make_user, "stream3c")
    r = await client.post("/chat/query/stream", json={"question": "q"}, headers=_auth(token))
    frames = _parse_sse(r.text)
    events = [e for e, _ in frames]
    assert events == ["stage", "error"]  # 过程帧照转,终态收敛 error


async def test_stream_malformed_process_frame_skipped(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, StreamFakeAgent],
) -> None:
    """R1-S1 相邻面:过程帧 data 畸形 JSON → 跳帧不中断,流继续到终态。"""
    reply = _pass_reply()
    frames_in = [
        ("stage", "{not-json"),
        ("stage", json.dumps({"node": "understand", "status": "done", "retry_count": 0})),
        ("result", json.dumps(reply.__dict__)),
    ]
    _install(fake_agent, StreamFakeAgent(frames_in, reply=reply))
    _, token = await _user_with_chat_perm(client, session, make_user, "stream3d")
    r = await client.post("/chat/query/stream", json={"question": "q"}, headers=_auth(token))
    frames = _parse_sse(r.text)
    events = [e for e, _ in frames]
    assert events == ["stage", "result"]  # 畸形帧被跳过,合法帧与终态照常


async def test_stream_tail_error_after_result_no_double_write(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, StreamFakeAgent],
) -> None:
    """R3-R1 直证:result 终态已落库后流尾传输异常,按成功下发——
    不叠加第二条 FAILED 消息,不下发 error 帧。"""
    reply = _pass_reply()
    frames_in = [
        ("result", json.dumps(reply.__dict__)),
    ]
    _install(fake_agent, StreamFakeAgent(frames_in, reply=reply, crash_after_frames=True))
    _, token = await _user_with_chat_perm(client, session, make_user, "stream3e")
    r = await client.post("/chat/query/stream", json={"question": "q"}, headers=_auth(token))
    frames = _parse_sse(r.text)
    events = [e for e, _ in frames]
    assert events == ["result"]  # 结果成功下发
    assert frames[-1][1]["success"] is True
    # 库内该会话 assistant 恰一条 SUCCESS,无 FAILED 叠加
    sid = frames[-1][1]["session_id"]
    rows = await session.execute(
        text("SELECT status FROM public.chat_message WHERE session_id=:s AND role='ASSISTANT'"),
        {"s": sid},
    )
    assert [str(x[0]) for x in rows] == ["SUCCESS"]


async def test_stream_duplicate_result_frame_idempotent(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, StreamFakeAgent],
) -> None:
    """R4 直证:agent 违约发双 result 帧——首个为权威,幂等守卫不二次落库。"""
    reply = _pass_reply()
    frames_in = [
        ("result", json.dumps(reply.__dict__)),
        ("result", json.dumps(reply.__dict__)),
    ]
    _install(fake_agent, StreamFakeAgent(frames_in, reply=reply))
    _, token = await _user_with_chat_perm(client, session, make_user, "stream3f")
    r = await client.post("/chat/query/stream", json={"question": "q"}, headers=_auth(token))
    frames = _parse_sse(r.text)
    assert [e for e, _ in frames].count("result") == 1
    sid = frames[-1][1]["session_id"]
    rows = await session.execute(
        text("SELECT status FROM public.chat_message WHERE session_id=:s AND role='ASSISTANT'"),
        {"s": sid},
    )
    assert [str(x[0]) for x in rows] == ["SUCCESS"]  # 恰一条


async def test_stream_persist_false_nothing_written(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, StreamFakeAgent],
) -> None:
    reply = _pass_reply()
    _install(fake_agent, StreamFakeAgent(_frames_for(reply), reply=reply))
    _, token = await _user_with_chat_perm(client, session, make_user, "stream4")
    before = (await session.execute(text("SELECT count(*) FROM public.chat_session"))).scalar_one()
    r = await client.post(
        "/chat/query/stream",
        json={"question": "有多少商品", "persist": False},
        headers=_auth(token),
    )
    frames = _parse_sse(r.text)
    assert frames[-1][1]["message_id"] is None
    assert frames[-1][1]["session_id"] is None
    after = (await session.execute(text("SELECT count(*) FROM public.chat_session"))).scalar_one()
    assert after == before  # 零会话零消息


async def test_stream_preflight_failures_regular_json(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, StreamFakeAgent],
) -> None:
    """前段校验失败在流开始前以常规 JSON 错误响应(404 会话/400 数据源)。"""
    reply = _pass_reply()
    _install(fake_agent, StreamFakeAgent(_frames_for(reply), reply=reply))
    _, token = await _user_with_chat_perm(client, session, make_user, "stream5")
    r = await client.post(
        "/chat/query/stream",
        json={"question": "q", "session_id": "00000000-0000-0000-0000-000000000000"},
        headers=_auth(token),
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "SESSION_NOT_FOUND"
    assert not r.headers.get("content-type", "").startswith("text/event-stream")
    r2 = await client.post(
        "/chat/query/stream",
        json={"question": "q", "datasource_key": "ghost"},
        headers=_auth(token),
    )
    assert r2.status_code == 400
    assert r2.json()["detail"] == "DATASOURCE_INVALID"


async def test_stream_no_perm_403(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, StreamFakeAgent],
) -> None:
    _install(fake_agent, StreamFakeAgent())
    await seed_all(session, get_settings())
    uid = await make_user("stream_noperm")
    rid = await _mk_role_with_perms(session, "NO_CHAT2", [])
    await _bind(session, "sys_user_role", uid, rid)
    await _bind(session, "sys_user_tenant", uid, await _default_tenant_id(session))
    token = await _login(client, "stream_noperm")
    r = await client.post("/chat/query/stream", json={"question": "q"}, headers=_auth(token))
    assert r.status_code == 403


# ============ 断线续存(直调 service,不经 HTTP) ============


async def test_disconnect_continues_to_terminal(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, StreamFakeAgent],
) -> None:
    """断线续存:前端断开(不消费 queue)后后台任务照常消费至终态并落库。"""
    reply = _pass_reply()
    _install(fake_agent, StreamFakeAgent(_frames_for(reply), reply=reply))
    uid, _token = await _user_with_chat_perm(client, session, make_user, "stream6")
    # 手工组装 service(不经 HTTP;stream_store_factory 与组合根同构)
    agent = fake_agent["agent"]
    from kongming.chat.router import _stream_store_factory  # 组合根工厂复用

    async with get_sessionmaker()() as s:
        svc = ChatService(
            ChatRepository(s),
            cast("Any", agent),
            SqlDatasourceRepo(s),
            None,
            None,
            None,
            _stream_store_factory(cast("Any", agent)),
        )
        handle = await svc.start_stream(
            user_id=uid,
            tenant_key="default",
            question="断线后仍应落库",
            session_id=None,
            persist=True,
            history=[],
            datasource_key="default",
        )
        # 模拟前端立即断开:从不消费 queue(生成器被取消的等价面)
        await handle.task
    # 后台任务已跑完:assistant 消息照常落库
    msgs = await session.execute(
        text(
            "SELECT m.role, m.status, m.summary FROM public.chat_message m"
            " JOIN public.chat_session s ON s.id=m.session_id"
            " WHERE s.title LIKE '断线后仍应落库%' ORDER BY m.created_at"
        )
    )
    rows = [(str(r[0]), str(r[1]), r[2]) for r in msgs]
    assert rows == [
        ("USER", "SUCCESS", None),
        ("ASSISTANT", "SUCCESS", reply.summary),
    ]


# ============ 心跳分支(直调生成器) ============


async def test_relay_gen_heartbeat_on_idle(monkeypatch: pytest.MonkeyPatch) -> None:
    """空闲超时发心跳注释行;帧到达即转发;None 收束。"""
    monkeypatch.setattr(chat_router, "STREAM_HEARTBEAT_SEC", 0.05)
    q: asyncio.Queue[tuple[str, object] | None] = asyncio.Queue()
    # task 允许None 后接,cast 对齐标注(运行时不变)
    handle = StreamHandle(queue=q, task=cast("asyncio.Task[None]", None))
    await q.put(("stage", {"node": "understand", "status": "running", "retry_count": 0}))

    async def _late_terminate() -> None:
        await asyncio.sleep(0.12)  # 生成器消费首帧后经历两个心跳间隔的真空窗
        await q.put(None)

    terminator = asyncio.create_task(_late_terminate())
    out: list[str] = []
    async for chunk in chat_router._relay_gen(handle):
        out.append(chunk)
    await terminator
    assert out[0].startswith("event: stage\n")
    assert out.count(": ping\n\n") >= 2  # 空闲窗口跨过多个心跳间隔


async def test_stream_trace_event_relayed(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, StreamFakeAgent],
) -> None:
    """trace 诊断帧(080 预算/063 预筛/064 闸门/068 净化共用通道)白名单透传,
    不侵入 result 终态与用户响应契约(内部诊断信号,前端按未知事件忽略)。"""
    reply = _pass_reply()
    frames = _frames_for(reply)
    # 在 summary 之后、result 之前插入 trace 帧
    idx = next(i for i, (e, _) in enumerate(frames) if e == "result")
    frames.insert(
        idx,
        (
            "trace",
            json.dumps(
                {
                    "node": "generate",
                    "fields": {"kind": "prompt_budget", "cut": {"schema_hint": 3}},
                }
            ),
        ),
    )
    _install(fake_agent, StreamFakeAgent(frames, reply=reply))
    _uid, token = await _user_with_chat_perm(client, session, make_user, "stream-tr")
    r = await client.post(
        "/chat/query/stream", json={"question": "有多少商品"}, headers=_auth(token)
    )
    assert r.status_code == 200
    parsed = _parse_sse(r.text)
    events = [e for e, _ in parsed]
    assert "trace" in events
    assert events[-1] == "result" and events.count("result") == 1
    trace_data = next(d for e, d in parsed if e == "trace")
    assert trace_data["node"] == "generate"
