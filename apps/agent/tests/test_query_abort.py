# pyright: reportPrivateUsage=false
"""在途问答中止端点测试(nl2sql spec「在途问答中止(Agent 面)」):中止在途轮、
幂等、令牌闸、两类流式入口同构、同一标识多处在途全停,以及断开连接仍能停图。"""

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from kongming_agent import deps
from kongming_agent.main import QueryIn, _initial_state, _query_stream_frames, app

TOKEN = {"X-Internal-Token": "test-internal-token"}
ABORT = "/internal/abort-query"

TRACE = "t-abort"
REQ: dict[str, Any] = {
    "trace_id": TRACE,
    "tenant_key": "default",
    "question": "商品总库存是多少",
    "history": [],
}


class _SlowGraph:
    """慢图替身:出一帧后卡在模拟的模型调用上;被取消即记录,未被取消才进入下一节点。"""

    def __init__(self) -> None:
        self.entered = asyncio.Event()  # 模拟的模型调用已开始(流在途)
        self.cancelled = asyncio.Event()  # 模拟的模型调用被取消
        self.calls = 0  # 模拟的模型调用次数
        self.late_nodes = 0  # 模型调用结束后进入的节点数

    async def astream(
        self,
        state: dict[str, Any],
        stream_mode: list[str] | None = None,
        config: dict[str, Any] | None = None,
    ) -> AsyncIterator[tuple[str, object]]:
        yield (
            "custom",
            {
                "event": "stage",
                "data": {"node": "understand", "status": "running", "retry_count": 0},
            },
        )
        self.calls += 1
        self.entered.set()
        try:
            await asyncio.sleep(0.3)
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        self.late_nodes += 1
        yield (
            "custom",
            {"event": "stage", "data": {"node": "understand", "status": "done", "retry_count": 0}},
        )


def _client(monkeypatch: pytest.MonkeyPatch) -> AsyncClient:
    monkeypatch.setattr(deps, "get_llm", lambda: None)
    monkeypatch.setattr(deps, "get_api", lambda: None)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _stand_in(graph: _SlowGraph) -> Callable[..., _SlowGraph]:
    """get_react_graph 替身(忽略 checkpointed 等关键字参数)。"""

    def _get(**_kw: object) -> _SlowGraph:
        return graph

    return _get


def _events(text: str) -> list[str]:
    return [
        line[len("event:") :].strip() for line in text.splitlines() if line.startswith("event:")
    ]


async def _start_stream(c: AsyncClient, path: str) -> asyncio.Task[Any]:
    return asyncio.create_task(c.post(path, json=REQ, headers=TOKEN))


async def test_abort_stops_running_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    """中止在途轮:图执行被取消,其后不再进入节点、不再发起模型调用,流上无终态帧。"""
    graph = _SlowGraph()
    monkeypatch.setattr("kongming_agent.main.get_graph", lambda: graph)
    async with _client(monkeypatch) as c:
        stream = await _start_stream(c, "/query/stream")
        await asyncio.wait_for(graph.entered.wait(), 5)
        resp = await c.post(ABORT, json={"trace_id": TRACE}, headers=TOKEN)
        assert resp.status_code == 200
        assert resp.json() == {"aborted": True}
        body = await asyncio.wait_for(stream, 5)
    assert _events(body.text) == ["stage"]
    await asyncio.wait_for(graph.cancelled.wait(), 5)
    assert graph.calls == 1 and graph.late_nodes == 0


async def test_abort_covers_react_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    """工具调用内核的流式入口与常规问答图同构:同样可被中止。"""
    graph = _SlowGraph()
    monkeypatch.setattr("kongming_agent.main.get_react_graph", _stand_in(graph))
    async with _client(monkeypatch) as c:
        stream = await _start_stream(c, "/query_react/stream")
        await asyncio.wait_for(graph.entered.wait(), 5)
        resp = await c.post(ABORT, json={"trace_id": TRACE}, headers=TOKEN)
        assert resp.json() == {"aborted": True}
        body = await asyncio.wait_for(stream, 5)
    assert _events(body.text) == ["stage"]
    await asyncio.wait_for(graph.cancelled.wait(), 5)
    assert graph.late_nodes == 0


async def test_abort_unknown_or_finished_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """标识未知或该轮已结束:返回「未在执行」,不报错。"""
    graph = _SlowGraph()
    monkeypatch.setattr("kongming_agent.main.get_graph", lambda: graph)
    async with _client(monkeypatch) as c:
        unknown = await c.post(ABORT, json={"trace_id": "no-such-trace"}, headers=TOKEN)
        assert unknown.status_code == 200
        assert unknown.json() == {"aborted": False}

        body = await c.post("/query/stream", json=REQ, headers=TOKEN)
        assert _events(body.text)[-1] == "result"
        finished = await c.post(ABORT, json={"trace_id": TRACE}, headers=TOKEN)
        assert finished.status_code == 200
        assert finished.json() == {"aborted": False}
    assert not graph.cancelled.is_set() and graph.late_nodes == 1


async def test_abort_without_token_does_not_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    """无令牌或错令牌:403,且不中止任何在途轮。"""
    graph = _SlowGraph()
    monkeypatch.setattr("kongming_agent.main.get_graph", lambda: graph)
    async with _client(monkeypatch) as c:
        stream = await _start_stream(c, "/query/stream")
        await asyncio.wait_for(graph.entered.wait(), 5)
        missing = await c.post(ABORT, json={"trace_id": TRACE})
        assert missing.status_code == 403
        wrong = await c.post(ABORT, json={"trace_id": TRACE}, headers={"X-Internal-Token": "bad"})
        assert wrong.status_code == 403
        body = await asyncio.wait_for(stream, 5)
    assert _events(body.text)[-1] == "result"
    assert not graph.cancelled.is_set() and graph.late_nodes == 1


async def test_abort_stops_every_execution_of_same_trace(monkeypatch: pytest.MonkeyPatch) -> None:
    """同一标识下多处在途流式执行:一次中止全部停掉。"""
    graph = _SlowGraph()
    monkeypatch.setattr("kongming_agent.main.get_graph", lambda: graph)
    async with _client(monkeypatch) as c:
        first = await _start_stream(c, "/query/stream")
        second = await _start_stream(c, "/query/stream")
        await asyncio.wait_for(graph.entered.wait(), 5)
        resp = await c.post(ABORT, json={"trace_id": TRACE}, headers=TOKEN)
        assert resp.json() == {"aborted": True}
        await asyncio.wait_for(asyncio.gather(first, second), 5)
    await asyncio.wait_for(graph.cancelled.wait(), 5)
    assert graph.late_nodes == 0


async def test_closing_stream_stops_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    """断开连接的兜底:帧不再被消费时图执行同样被取消(不靠中止端点)。"""
    graph = _SlowGraph()
    monkeypatch.setattr("kongming_agent.main.get_graph", lambda: graph)
    req = QueryIn(trace_id=TRACE, tenant_key="default", question="商品总库存是多少")
    frames = _query_stream_frames(_initial_state(req), TRACE)

    async def _drain() -> None:
        async for _ in frames:
            pass

    # 消费者等同响应任务:取消它即等同连接断开时响应任务被取消
    consumer = asyncio.create_task(_drain())
    await asyncio.wait_for(graph.entered.wait(), 5)
    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer
    await asyncio.wait_for(graph.cancelled.wait(), 5)
    assert graph.late_nodes == 0
