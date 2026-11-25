# pyright: reportPrivateUsage=false
"""问答轮真取消测试(query-streaming / chat「消息状态第五态」/ nl2sql 中止的
控制面一侧):第五态 CHECK 约束、在途轮登记、取消编排、取消端点与归属校验。"""

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Coroutine, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.seed import seed_all
from kongming.chat import inflight
from kongming.chat.agent_client import AgentUnavailable
from kongming.chat.dependencies import get_agent_client
from kongming.chat.finalize import (
    TURN_CANCELLED_TEXT,
    FinalizePorts,
    ReplyFinalizer,
    persist_cancelled,
)
from kongming.chat.inflight import InflightTurns, TurnCanceller, TurnSettlement
from kongming.chat.models import MessageMeta
from kongming.chat.ports import StreamStore
from kongming.chat.results import ChatOutcome
from kongming.chat.stream_runner import (
    AgentStreamConsumer,
    PreparedQuery,
    StreamFailure,
    StreamHandle,
)
from kongming.config import get_settings
from kongming.main import app
from kongming.uuid7 import uuid7
from tests.conftest import MakeUser
from tests.test_chat_query import (  # 测试助手按名字取用
    _auth,
    _bind,
    _default_tenant_id,
    _login,
    _mk_role_with_perms,
    _pass_reply,
)
from tests.test_chat_stream import (  # 测试助手按名字取用
    StreamFakeAgent,
    _frames_for,
    _install,
    _parse_sse,
)
from tests.test_chat_stream import (
    _user_with_chat_perm as _stream_user,
)


@pytest.fixture
def fake_agent_stream() -> Iterator[dict[str, StreamFakeAgent]]:
    holder: dict[str, StreamFakeAgent] = {}

    def _factory() -> StreamFakeAgent:
        return holder["agent"]

    app.dependency_overrides[get_agent_client] = _factory
    yield holder
    app.dependency_overrides.pop(get_agent_client, None)


async def _mk_assistant(s: AsyncSession, uid: str, tid: str, *, status: str = "CANCELLED") -> str:
    """直插最小轮次链(绕过服务层,专测约束)。"""
    sid = str(uuid7())
    mid = str(uuid7())
    await s.execute(
        text("INSERT INTO public.chat_session (id, user_id, tenant_id) VALUES (:id, :u, :t)"),
        {"id": sid, "u": uid, "t": tid},
    )
    await s.execute(
        text(
            "INSERT INTO public.chat_message (id, session_id, role, status, summary)"
            " VALUES (:id, :sid, 'ASSISTANT', :status, '本轮已取消')"
        ),
        {"id": mid, "sid": sid, "status": status},
    )
    await s.commit()
    return mid


class TestCancelledStatusConstraint:
    async def test_cancelled_assistant_message_persists(
        self, session: AsyncSession, make_user: MakeUser
    ) -> None:
        await seed_all(session, get_settings())
        uid = await make_user("cancel-c1")
        tid = await _default_tenant_id(session)
        mid = await _mk_assistant(session, uid, tid)
        status = await session.execute(
            text("SELECT status FROM public.chat_message WHERE id = :id"), {"id": mid}
        )
        assert str(status.scalar_one()) == "CANCELLED"

    async def test_invalid_status_still_rejected_by_check(
        self, session: AsyncSession, make_user: MakeUser
    ) -> None:
        from sqlalchemy.exc import IntegrityError

        await seed_all(session, get_settings())
        uid = await make_user("cancel-c2")
        tid = await _default_tenant_id(session)
        with pytest.raises(IntegrityError):
            await _mk_assistant(session, uid, tid, status="ABORTED")
        await session.rollback()


# ============ 消费任务与取消编排(假 AgentClient + 假流存储) ============

TID = "turn-cancel-trace"


class _HangAgent:
    """流式替身:出一帧 stage 后挂起;放行后发出尾帧并再次挂起,等同 Agent 面还在跑、
    中止请求到来才收尾。abort 决定中止请求的应答方式。"""

    def __init__(
        self,
        *,
        abort: str = "ok",
        tail: list[tuple[str, str]] | None = None,
        fail: bool = False,
    ) -> None:
        self.abort = abort  # ok=停止执行 / fail=请求出错 / hang=不应答
        self.tail = tail or []
        self.fail = fail  # 放行后以传输失败收尾(不出终态帧)
        self.release = asyncio.Event()  # 第一段挂起的放行信号
        self.finish = asyncio.Event()  # 尾段挂起的放行信号(等同 Agent 面停止执行)
        self.started = asyncio.Event()  # 首帧已出(流在途)
        self.cancelled = False  # 流在挂起中被硬取消
        self.abort_calls: list[str] = []

    async def query_stream(
        self, *, on_frame: Callable[[str, str], Coroutine[Any, Any, None]], **kw: object
    ) -> None:
        await on_frame(
            "stage", json.dumps({"node": "understand", "status": "running", "retry_count": 0})
        )
        self.started.set()
        try:
            await self.release.wait()
            for event, data in self.tail:
                await on_frame(event, data)
            if self.fail:
                raise AgentUnavailable("stream broken")
            await self.finish.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise

    async def abort_query(self, *, trace_id: str) -> bool:
        self.abort_calls.append(trace_id)
        if self.abort == "fail":
            raise RuntimeError("abort crashed")
        if self.abort == "hang":
            await asyncio.sleep(30)
            return False
        self.release.set()
        self.finish.set()
        return True


class _FakeRepo:
    """内存消息仓储:记录落库行并按 trace_id 查重;可在首次提交后触发一次回调。"""

    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []
        self.after_commit: Callable[[], None] | None = None
        self.inserted = asyncio.Event()  # 首条 assistant 行已写入

    async def insert_message(
        self,
        *,
        session_id: str,
        role: str,
        question: str | None = None,
        sql_text: str | None = None,
        result_json: str | None = None,
        summary: str | None = None,
        status: str,
        attachments: list[dict[str, object]] | None = None,
        meta: MessageMeta | None = None,
        created_at: datetime | None = None,
    ) -> str:
        mid = f"m{len(self.messages) + 1}"
        if role == "ASSISTANT":
            self.inserted.set()
        self.messages.append(
            {
                "id": mid,
                "session_id": session_id,
                "role": role,
                "status": status,
                "summary": summary,
                "result_json": result_json,
                "trace_id": meta.trace_id if meta is not None else None,
                "stage_trace": meta.stage_trace if meta is not None else None,
                "created_at": created_at,
            }
        )
        return mid

    async def message_created_at(self, *, message_id: str) -> datetime | None:
        for m in self.messages:
            if m["id"] == message_id:
                return cast("datetime | None", m.get("created_at"))
        return None

    async def touch_session(self, session_id: str) -> None:
        return None

    async def commit(self) -> None:
        if self.after_commit is not None:
            hook, self.after_commit = self.after_commit, None
            hook()

    async def rollback(self) -> None:
        return None

    async def assistant_message_by_trace(
        self, *, trace_id: str, user_id: str | None = None
    ) -> dict[str, object] | None:
        for m in reversed(self.messages):
            if m["role"] == "ASSISTANT" and m["trace_id"] == trace_id:
                return {"id": m["id"], "status": m["status"], "summary": m["summary"]}
        return None


class _FailingReadRepo(_FakeRepo):
    """查重读取必失败(等同取消收敛时数据库连接中断)。"""

    async def assistant_message_by_trace(
        self, *, trace_id: str, user_id: str | None = None
    ) -> dict[str, object] | None:
        raise RuntimeError("connection reset")


class _SlowWriter:
    """回写替身:让出一次事件循环(等同真实回写在提交之后挂起等待)。"""

    async def writeback(self, **kw: object) -> None:
        await asyncio.sleep(0)


def _prepared(*, sid: str | None = "s1", trace_id: str = TID) -> PreparedQuery:
    return PreparedQuery(
        tenant_id="tenant-1",
        resolved_key="default",
        sid=sid,
        effective_history=[],
        trace_id=trace_id,
        user_id="u1",
    )


def _consumer(
    agent: _HangAgent,
    repo: _FakeRepo,
    turns: InflightTurns,
    *,
    writer: object | None = None,
    title_calls: list[str] | None = None,
    store: object | None = None,
) -> AgentStreamConsumer:
    titles: list[str] = title_calls if title_calls is not None else []

    async def _record_title(sid: str) -> None:
        titles.append(sid)

    @asynccontextmanager  # pyright: ignore[reportDeprecated]  # async 生成器误选 typeshed 弃用重载
    async def _store() -> AsyncIterator[StreamStore]:
        yield StreamStore(
            repo=cast("Any", repo),
            history_writer=cast("Any", writer),
            trigger_title_rewrite=_record_title,
        )

    return AgentStreamConsumer(
        agent=cast("Any", agent),
        stream_store_factory=_store if store is None else cast("Any", store),
        finalizer=ReplyFinalizer(FinalizePorts()),
        repo=cast("Any", repo),
        turns=turns,
    )


def _canceller(agent: _HangAgent, turns: InflightTurns, repo: _FakeRepo) -> TurnCanceller:
    return TurnCanceller(agent=cast("Any", agent), turns=turns, repo=cast("Any", repo))


def _gated_store(repo: _FakeRepo, gate: asyncio.Event, closing: asyncio.Event) -> object:
    """流存储工厂替身:首个会话在退出前挂起(等同失败行已提交、收尾尚未做完的窗口)。"""
    exits = 0

    @asynccontextmanager  # pyright: ignore[reportDeprecated]  # async 生成器误选 typeshed 弃用重载
    async def _open() -> AsyncIterator[StreamStore]:
        nonlocal exits
        yield StreamStore(repo=cast("Any", repo))
        exits += 1
        if exits == 1:
            closing.set()
            await gate.wait()

    return _open


async def _drain(handle: StreamHandle) -> list[object]:
    """读空句柄队列直到终止标记(公开流元素为事件对象或 ChatOutcome,不再用元组)。"""
    out: list[object] = []
    while True:
        item = await asyncio.wait_for(handle.queue.get(), 5)
        out.append(item)
        if item is None:
            return out


async def _terminal(handle: StreamHandle) -> object:
    """读空句柄队列直到终止标记,返回其中唯一的终态元素。"""
    frames = [f for f in await _drain(handle) if f is not None]
    assert len(frames) == 1  # 恰一个终态元素
    return frames[0]


def _assistant_rows(repo: _FakeRepo) -> list[dict[str, object]]:
    return [m for m in repo.messages if m["role"] == "ASSISTANT"]


async def test_turn_registered_while_running_and_unregistered_after() -> None:
    """登记随后台任务生命周期:在途时可按「用户 + trace_id」查到,他人查不到,结束即注销。"""
    agent, repo, turns = _HangAgent(), _FakeRepo(), InflightTurns()
    handle = _consumer(agent, repo, turns).begin(
        _prepared(),
        user_id="u1",
        question="q",
        orig_question="q",
        persist=True,
        datasource_key="default",
    )
    assert (await asyncio.wait_for(handle.queue.get(), 5)) is not None  # stage 帧已出
    assert [t.trace_id for t in turns.find_all(user_id="u1", trace_id=TID)] == [TID]
    assert turns.find_all(user_id="u2", trace_id=TID) == []
    agent.release.set()
    agent.finish.set()
    await asyncio.wait_for(handle.task, 5)
    assert turns.find_all(user_id="u1", trace_id=TID) == []


async def test_cancel_converges_to_cancelled_and_persists_stage_trace() -> None:
    """取消→通知 Agent 面→收敛为已取消:落裸终态行(带取消前轨迹),恰一个终态帧,不触发标题改写。"""
    agent, repo, turns = _HangAgent(), _FakeRepo(), InflightTurns()
    titles: list[str] = []
    handle = _consumer(agent, repo, turns, title_calls=titles).begin(
        _prepared(),
        user_id="u1",
        question="q",
        orig_question="q",
        persist=True,
        datasource_key="default",
    )
    await handle.queue.get()  # stage 帧(取消前轨迹的来源)
    settled = await asyncio.wait_for(
        _canceller(agent, turns, repo).cancel(user_id="u1", trace_id=TID), 5
    )
    assert settled == TurnSettlement("CANCELLED", "m1")
    assert agent.abort_calls == [TID]
    rows = _assistant_rows(repo)
    assert [(r["status"], r["summary"], r["trace_id"]) for r in rows] == [
        ("CANCELLED", TURN_CANCELLED_TEXT, TID)
    ]
    assert json.loads(str(rows[0]["result_json"])) == {"artifacts": []}
    assert rows[0]["stage_trace"] is not None  # 取消前已收集的节点轨迹如实落库
    terminal_item = await _terminal(handle)
    assert isinstance(terminal_item, ChatOutcome)
    assert terminal_item.verdict == "CANCELLED" and terminal_item.message_id == "m1"
    assert titles == []


async def test_cancel_without_persist_writes_nothing() -> None:
    """persist=false 的轮:执行停止,不写任何消息,落库消息标识为空。"""
    agent, repo, turns = _HangAgent(), _FakeRepo(), InflightTurns()
    handle = _consumer(agent, repo, turns).begin(
        _prepared(sid=None),
        user_id="u1",
        question="q",
        orig_question="q",
        persist=False,
        datasource_key="default",
    )
    await handle.queue.get()
    settled = await asyncio.wait_for(
        _canceller(agent, turns, repo).cancel(user_id="u1", trace_id=TID), 5
    )
    assert settled == TurnSettlement("CANCELLED", None)
    assert repo.messages == []
    terminal_item = await _terminal(handle)
    assert isinstance(terminal_item, ChatOutcome) and terminal_item.verdict == "CANCELLED"


async def test_cancel_after_normal_result_returns_completed() -> None:
    """已收到正常终态后到达的取消:不硬取消任务,按已完成返回,落库仍是正常结果。"""
    reply = _pass_reply()
    agent = _HangAgent(tail=[("result", json.dumps(reply.__dict__))])
    repo, turns = _FakeRepo(), InflightTurns()
    handle = _consumer(agent, repo, turns).begin(
        _prepared(),
        user_id="u1",
        question="q",
        orig_question="q",
        persist=True,
        datasource_key="default",
    )
    await handle.queue.get()
    agent.release.set()  # 正常应答帧到达并落库,流尚未结束
    await asyncio.wait_for(repo.inserted.wait(), 5)
    settled = await asyncio.wait_for(
        _canceller(agent, turns, repo).cancel(user_id="u1", trace_id=TID), 5
    )
    assert settled == TurnSettlement("COMPLETED", "m1")
    assert agent.cancelled is False
    assert [r["status"] for r in _assistant_rows(repo)] == ["SUCCESS"]
    terminal_item = await _terminal(handle)
    assert isinstance(terminal_item, ChatOutcome) and terminal_item.success is True


async def test_cancel_racing_with_persisted_result_writes_no_second_row() -> None:
    """取消与正常终态落库几乎同时(行已提交、终态尚未记下时被硬取消):按 trace_id 查重,
    不写第二条 assistant 行,按已正常结束返回。"""
    reply = _pass_reply()
    agent = _HangAgent(tail=[("result", json.dumps(reply.__dict__))])
    repo, turns = _FakeRepo(), InflightTurns()

    def _cancel_caller() -> None:  # 正常应答行提交后立刻硬取消消费任务
        task = asyncio.current_task()
        assert task is not None
        task.cancel()

    repo.after_commit = _cancel_caller
    handle = _consumer(agent, repo, turns, writer=_SlowWriter()).begin(
        _prepared(),
        user_id="u1",
        question="q",
        orig_question="q",
        persist=True,
        datasource_key="default",
    )
    await handle.queue.get()
    settled = await asyncio.wait_for(
        _canceller(agent, turns, repo).cancel(user_id="u1", trace_id=TID), 5
    )
    assert settled == TurnSettlement("COMPLETED", "m1")
    assert [r["status"] for r in _assistant_rows(repo)] == ["SUCCESS"]
    terminal_item = await _terminal(handle)
    assert isinstance(terminal_item, ChatOutcome)


@pytest.mark.parametrize(
    ("status", "success", "verdict"),
    [
        ("SUCCESS", True, "PASS"),
        ("REJECTED", False, "BLOCKED"),
        ("NO_DATA", False, "NO_DATA"),
        ("FAILED", False, "ERROR"),
        ("CANCELLED", False, "CANCELLED"),
    ],
)
async def test_persist_cancelled_adopts_existing_row_with_response_verdict(
    status: str, success: bool, verdict: str
) -> None:
    """取消收敛遇到本轮已有的落库行:采用该行、不再写,结论词还原为响应口径
    (与 map_status 互逆——落库的 REJECTED 即响应的 BLOCKED)。"""
    repo = _FakeRepo()
    repo.messages.append(
        {
            "id": "m9",
            "session_id": "s1",
            "role": "ASSISTANT",
            "status": status,
            "summary": "已有结果",
            "result_json": None,
            "trace_id": TID,
            "stage_trace": None,
        }
    )
    out = await persist_cancelled(cast("Any", repo), sid="s1", trace_id=TID, user_id="u1")
    assert (out.success, out.verdict, out.message_id) == (success, verdict, "m9")
    assert len(_assistant_rows(repo)) == 1


async def test_persist_cancelled_keeps_original_turn_order() -> None:
    """已取消行沿用原轮用户消息的落库时刻:下一问已先落库时,历史仍把取消说明排在原问之后。"""
    repo = _FakeRepo()
    user_time = datetime(2026, 11, 25, 15, 21, 48, tzinfo=UTC)
    await repo.insert_message(
        session_id="s1",
        role="USER",
        question="列出每位客户在各门店的累计租赁次数和总消费金额",
        status="SUCCESS",
        created_at=user_time,
    )
    await repo.insert_message(
        session_id="s1",
        role="USER",
        question="参演电影最多的前三位演员是谁",
        status="SUCCESS",
        created_at=user_time.replace(second=58),
    )
    out = await persist_cancelled(
        cast("Any", repo),
        sid="s1",
        trace_id=TID,
        user_id="u1",
        user_message_id="m1",
    )
    rows = [m for m in repo.messages if m["role"] == "ASSISTANT"]
    assert len(rows) == 1 and rows[0]["status"] == "CANCELLED"
    assert rows[0]["created_at"] == user_time  # 不是取消收敛时刻
    assert out.message_id == rows[0]["id"]


async def test_persist_cancelled_dedup_read_failure_returns_bare_cancelled() -> None:
    """查重读取失败(连接中断一类):收敛不炸,按裸已取消终态返回且不写行——
    读失败后的会话再插必败,强行续写只会把异常抛给取消发起方。"""
    repo = _FailingReadRepo()
    out = await persist_cancelled(cast("Any", repo), sid="s1", trace_id=TID, user_id="u1")
    assert (out.success, out.verdict, out.message_id) == (False, "CANCELLED", None)
    assert repo.messages == []


async def test_cancel_survives_dedup_read_failure_and_still_terminates() -> None:
    """取消收敛的查重读取失败:本轮仍收敛为已取消(无落库标识)、恰一个终态帧照常下发——
    流不以无终态帧收场,取消结果如实为已取消(不是异常)。"""
    repo = _FailingReadRepo()
    agent, turns = _HangAgent(), InflightTurns()
    handle = _consumer(agent, repo, turns).begin(
        _prepared(),
        user_id="u1",
        question="q",
        orig_question="q",
        persist=True,
        datasource_key="default",
    )
    await handle.queue.get()
    settled = await asyncio.wait_for(
        _canceller(agent, turns, repo).cancel(user_id="u1", trace_id=TID), 5
    )
    assert settled == TurnSettlement("CANCELLED", None)
    assert repo.messages == []
    terminal_item = await _terminal(handle)
    assert isinstance(terminal_item, ChatOutcome) and terminal_item.verdict == "CANCELLED"


async def test_cancel_raised_inside_failed_wrap_up_reports_completed() -> None:
    """取消标记在失败收尾开始后才置位(失败行提交时):本轮照常按失败收尾,取消结果如实为
    已结束并携带失败行标识——不谎称已取消。"""
    agent = _HangAgent(fail=True)
    repo, turns = _FakeRepo(), InflightTurns()
    committed = asyncio.Event()

    def _raise_flag() -> None:  # 收尾的取消判断已过,此时才请求取消
        turns.find_all(user_id="u1", trace_id=TID)[0].cancel_requested = True
        committed.set()

    repo.after_commit = _raise_flag
    handle = _consumer(agent, repo, turns).begin(
        _prepared(),
        user_id="u1",
        question="q",
        orig_question="q",
        persist=True,
        datasource_key="default",
    )
    await handle.queue.get()
    agent.release.set()
    await asyncio.wait_for(committed.wait(), 5)
    settled = await asyncio.wait_for(
        _canceller(agent, turns, repo).cancel(user_id="u1", trace_id=TID), 5
    )
    assert settled == TurnSettlement("COMPLETED", "m1")
    assert [(r["status"], r["trace_id"]) for r in _assistant_rows(repo)] == [("FAILED", TID)]
    terminal_item = await _terminal(handle)
    assert isinstance(terminal_item, StreamFailure)


async def test_hard_cancel_inside_failed_wrap_up_keeps_single_failed_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """失败收尾进行中(失败行已提交、收尾未完)被请求取消并硬取消:收尾照常做完,只留一条
    FAILED 行、恰一个 error 终态帧,按已结束返回——不再多写一条已取消行。"""
    monkeypatch.setattr(inflight, "ABORT_TIMEOUT_SEC", 0.05)
    monkeypatch.setattr(inflight, "CANCEL_SETTLE_TIMEOUT_SEC", 0.05)
    agent = _HangAgent(fail=True)
    repo, turns = _FakeRepo(), InflightTurns()
    gate, closing = asyncio.Event(), asyncio.Event()
    handle = _consumer(agent, repo, turns, store=_gated_store(repo, gate, closing)).begin(
        _prepared(),
        user_id="u1",
        question="q",
        orig_question="q",
        persist=True,
        datasource_key="default",
    )
    await handle.queue.get()
    agent.release.set()
    await asyncio.wait_for(closing.wait(), 5)  # 失败行已提交,收尾停在会话关闭前
    cancel = asyncio.create_task(_canceller(agent, turns, repo).cancel(user_id="u1", trace_id=TID))
    for _ in range(500):  # 等硬取消发出
        if handle.task.cancelling() > 0:
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("硬取消未发出")
    gate.set()
    settled = await asyncio.wait_for(cancel, 5)
    assert settled == TurnSettlement("COMPLETED", "m1")
    assert [(r["status"], r["trace_id"]) for r in _assistant_rows(repo)] == [("FAILED", TID)]
    terminal_item = await _terminal(handle)
    assert isinstance(terminal_item, StreamFailure)


@pytest.mark.parametrize("abort", ["fail", "hang"])
async def test_cancel_hard_cancels_when_agent_notification_fails(
    monkeypatch: pytest.MonkeyPatch, abort: str
) -> None:
    """通知 Agent 面失败或超时:硬取消本地任务,仍落已取消行。"""
    monkeypatch.setattr(inflight, "ABORT_TIMEOUT_SEC", 0.05)
    monkeypatch.setattr(inflight, "CANCEL_SETTLE_TIMEOUT_SEC", 0.05)
    agent, repo, turns = _HangAgent(abort=abort), _FakeRepo(), InflightTurns()
    handle = _consumer(agent, repo, turns).begin(
        _prepared(),
        user_id="u1",
        question="q",
        orig_question="q",
        persist=True,
        datasource_key="default",
    )
    await handle.queue.get()
    settled = await asyncio.wait_for(
        _canceller(agent, turns, repo).cancel(user_id="u1", trace_id=TID), 5
    )
    assert settled == TurnSettlement("CANCELLED", "m1")
    assert agent.cancelled is True
    assert [r["status"] for r in _assistant_rows(repo)] == ["CANCELLED"]
    terminal_item = await _terminal(handle)
    assert isinstance(terminal_item, ChatOutcome) and terminal_item.verdict == "CANCELLED"


async def test_external_cancel_without_request_is_reraised() -> None:
    """取消标记未置时被外部取消(如进程关停):原样上抛,不伪造终态,终止标记仍必达。"""
    agent, repo, turns = _HangAgent(), _FakeRepo(), InflightTurns()
    handle = _consumer(agent, repo, turns).begin(
        _prepared(),
        user_id="u1",
        question="q",
        orig_question="q",
        persist=True,
        datasource_key="default",
    )
    await handle.queue.get()
    handle.task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await handle.task
    assert repo.messages == []
    assert await asyncio.wait_for(handle.queue.get(), 5) is None
    assert turns.find_all(user_id="u1", trace_id=TID) == []


# ============ 取消端点(HTTP;真实 PG) ============


@pytest.fixture
def hang_agent() -> Iterator[dict[str, _HangAgent]]:
    holder: dict[str, _HangAgent] = {}

    def _factory() -> _HangAgent:
        return holder["agent"]

    app.dependency_overrides[get_agent_client] = _factory
    yield holder
    app.dependency_overrides.pop(get_agent_client, None)


def _stream_headers(token: str, trace_id: str | None = TID) -> dict[str, str]:
    headers = _auth(token)
    if trace_id is not None:
        headers["X-Trace-Id"] = trace_id
    return headers


async def _user_rows(session: AsyncSession, uid: str) -> list[tuple[object, ...]]:
    rows = await session.execute(
        text(
            "SELECT m.role, m.status, m.trace_id, m.summary, m.sql_text, m.result_json"
            " FROM public.chat_message m"
            " JOIN public.chat_session s ON s.id = m.session_id"
            " WHERE s.user_id = :u ORDER BY m.created_at, m.id"
        ),
        {"u": uid},
    )
    return [tuple(r) for r in rows.all()]


async def test_cancel_endpoint_own_inflight_turn(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    hang_agent: dict[str, _HangAgent],
) -> None:
    """本人取消在途轮:CANCELLED + 落库消息标识;历史留一条已取消行;重复取消结果一致。"""
    uid, token = await _stream_user(session, make_user, "cancel-a")
    agent = _HangAgent()
    hang_agent["agent"] = agent
    stream = asyncio.create_task(
        client.post(
            "/chat/query/stream", json={"question": "慢查询"}, headers=_stream_headers(token)
        )
    )
    await asyncio.wait_for(agent.started.wait(), 5)
    resp = await client.post(f"/chat/query/stream/{TID}/cancel", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["outcome"] == "CANCELLED" and body["message_id"]
    assert agent.abort_calls == [TID]
    frames = _parse_sse((await asyncio.wait_for(stream, 5)).text)
    assert [e for e, _ in frames] == ["stage", "result"]
    assert (
        frames[-1][1]["verdict"] == "CANCELLED"
        and frames[-1][1]["message_id"] == body["message_id"]
    )
    rows = await _user_rows(session, uid)
    assert [(r[0], r[1], r[2]) for r in rows] == [
        ("USER", "SUCCESS", None),
        ("ASSISTANT", "CANCELLED", TID),
    ]
    assert rows[1][3] == TURN_CANCELLED_TEXT and rows[1][4] is None
    assert json.loads(str(rows[1][5])) == {"artifacts": []}
    again = await client.post(f"/chat/query/stream/{TID}/cancel", headers=_auth(token))
    assert again.status_code == 200 and again.json() == body


async def test_cancel_endpoint_other_user_gets_same_404_and_turn_unaffected(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    hang_agent: dict[str, _HangAgent],
) -> None:
    """他人轮次:404 且与不存在同形;该轮不受影响,照常完成。"""
    uid_a, token_a = await _stream_user(session, make_user, "cancel-b-a")
    _uid_b, token_b = await _stream_user(session, make_user, "cancel-b-b")
    agent = _HangAgent(tail=[("result", json.dumps(_pass_reply().__dict__))])
    hang_agent["agent"] = agent
    stream = asyncio.create_task(
        client.post(
            "/chat/query/stream", json={"question": "慢查询"}, headers=_stream_headers(token_a)
        )
    )
    await asyncio.wait_for(agent.started.wait(), 5)
    ghost = await client.post("/chat/query/stream/no-such-trace/cancel", headers=_auth(token_b))
    other = await client.post(f"/chat/query/stream/{TID}/cancel", headers=_auth(token_b))
    assert other.status_code == 404 and ghost.status_code == 404
    assert other.json() == ghost.json()
    assert agent.abort_calls == []
    agent.release.set()
    agent.finish.set()
    frames = _parse_sse((await asyncio.wait_for(stream, 5)).text)
    assert frames[-1][1]["success"] is True and agent.cancelled is False
    assert [(r[0], r[1]) for r in await _user_rows(session, uid_a)] == [
        ("USER", "SUCCESS"),
        ("ASSISTANT", "SUCCESS"),
    ]


async def test_cancel_endpoint_requires_chat_execute(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    hang_agent: dict[str, _HangAgent],
) -> None:
    """无 chat:execute:403,且不中止任何轮。"""
    await seed_all(session, get_settings())
    uid = await make_user("cancel-noperm")
    rid = await _mk_role_with_perms(session, "NO_CANCEL", [])
    await _bind(session, "sys_user_role", uid, rid)
    await _bind(session, "sys_user_tenant", uid, await _default_tenant_id(session))
    token = _login(uid)
    resp = await client.post(f"/chat/query/stream/{TID}/cancel", headers=_auth(token))
    assert resp.status_code == 403


async def test_cancel_endpoint_completed_turn_returns_completed_with_message_id(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent_stream: dict[str, StreamFakeAgent],
) -> None:
    """已完成且落库的轮:COMPLETED 并携带该轮消息标识,历史不出现已取消行。"""
    uid, token = await _stream_user(session, make_user, "cancel-done")
    reply = _pass_reply()
    _install(fake_agent_stream, StreamFakeAgent(_frames_for(reply), reply=reply))
    frames = _parse_sse(
        (
            await client.post(
                "/chat/query/stream", json={"question": "q"}, headers=_stream_headers(token)
            )
        ).text
    )
    mid = frames[-1][1]["message_id"]
    resp = await client.post(f"/chat/query/stream/{TID}/cancel", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json() == {"outcome": "COMPLETED", "message_id": mid}
    assert [(r[0], r[1], r[2]) for r in await _user_rows(session, uid)] == [
        ("USER", "SUCCESS", None),
        ("ASSISTANT", "SUCCESS", TID),
    ]


async def test_cancel_endpoint_finished_failed_turn_returns_completed(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent_stream: dict[str, StreamFakeAgent],
) -> None:
    """以失败收尾且已落库的轮:失败行带本轮标识,取消按已结束返回并携带该行标识
    (已结束且有落库结果的轮不走 404),不写已取消行。"""
    uid, token = await _stream_user(session, make_user, "cancel-failed")
    _install(fake_agent_stream, StreamFakeAgent(boom=True))
    frames = _parse_sse(
        (
            await client.post(
                "/chat/query/stream", json={"question": "q"}, headers=_stream_headers(token)
            )
        ).text
    )
    assert [e for e, _ in frames] == ["error"]
    mid = (
        await session.execute(
            text("SELECT id FROM public.chat_message WHERE role = 'ASSISTANT' AND trace_id = :t"),
            {"t": TID},
        )
    ).scalar_one()
    resp = await client.post(f"/chat/query/stream/{TID}/cancel", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json() == {"outcome": "COMPLETED", "message_id": str(mid)}
    assert [(r[0], r[1], r[2]) for r in await _user_rows(session, uid)] == [
        ("USER", "SUCCESS", None),
        ("ASSISTANT", "FAILED", TID),
    ]


async def test_cancel_endpoint_finished_unpersisted_turn_404(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent_stream: dict[str, StreamFakeAgent],
) -> None:
    """已结束且无落库结果(persist=false):404。"""
    uid, token = await _stream_user(session, make_user, "cancel-nopersist")
    reply = _pass_reply()
    _install(fake_agent_stream, StreamFakeAgent(_frames_for(reply), reply=reply))
    await client.post(
        "/chat/query/stream",
        json={"question": "q", "persist": False},
        headers=_stream_headers(token),
    )
    resp = await client.post(f"/chat/query/stream/{TID}/cancel", headers=_auth(token))
    assert resp.status_code == 404 and resp.json()["detail"] == "TURN_NOT_FOUND"
    assert await _user_rows(session, uid) == []  # persist=false:整轮不落库


async def test_stream_response_trace_header_matches_turn_trace(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent_stream: dict[str, StreamFakeAgent],
) -> None:
    """流式响应头 X-Trace-Id 即本轮标识:入站指定时原样回,未指定时生成的值与落库 trace_id 一致。"""
    uid, token = await _stream_user(session, make_user, "cancel-hdr")
    reply = _pass_reply()
    _install(fake_agent_stream, StreamFakeAgent(_frames_for(reply), reply=reply))
    given = await client.post(
        "/chat/query/stream", json={"question": "q"}, headers=_stream_headers(token)
    )
    assert given.headers["x-trace-id"] == TID
    auto = await client.post(
        "/chat/query/stream", json={"question": "q"}, headers=_stream_headers(token, None)
    )
    traces = [str(r[2]) for r in await _user_rows(session, uid) if r[0] == "ASSISTANT"]
    assert auto.headers["x-trace-id"] in traces and TID in traces
