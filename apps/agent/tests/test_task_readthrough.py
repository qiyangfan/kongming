"""worker 续读入口测试:apiclient 降级契约/截断提示/续读工具分流/装配绑定。"""

# pyright: reportPrivateUsage=false

import json
from typing import Any, cast

import httpx
import pytest

from kongming_agent import deps
from kongming_agent.apiclient import ApiClient
from kongming_agent.graph.react.nodes import tool_dispatch
from kongming_agent.graph.react.state import ReactState
from kongming_agent.llm_protocol import LlmToolCall
from kongming_agent.roundtable_gen import participant_system_prompt
from kongming_agent.settings import Settings
from kongming_agent.task_context import TASK_CONTEXT_HINT, TASK_CONTEXT_TOOL_NAME

# ── apiclient.task_context 降级契约 ──


async def test_task_context_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    client = ApiClient(Settings())
    seen: list[dict[str, Any]] = []

    async def fake_get(self: ApiClient, path: str, params: dict[str, str]) -> httpx.Response:
        seen.append({"path": path, "params": dict(params)})
        return httpx.Response(200, json={"goal": "全文", "steps": [], "truncated": False})

    monkeypatch.setattr(ApiClient, "_get_once", fake_get)
    out = await client.task_context(task_id="t1", tenant_key="default")
    assert out is not None and out["goal"] == "全文"
    assert seen[0]["path"] == "/internal/task/context"
    assert seen[0]["params"] == {"task_id": "t1", "tenant_key": "default"}


async def test_task_context_degrades_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    client = ApiClient(Settings())

    async def down(self: ApiClient, path: str, params: dict[str, str]) -> httpx.Response:
        raise httpx.ConnectError("down")

    async def bad(self: ApiClient, path: str, params: dict[str, str]) -> httpx.Response:
        return httpx.Response(200, content=b"<html>")

    async def err(self: ApiClient, path: str, params: dict[str, str]) -> httpx.Response:
        return httpx.Response(503)

    for handler in (down, bad, err):
        monkeypatch.setattr(ApiClient, "_get_once", handler)
        assert await client.task_context(task_id="t1", tenant_key="default") is None


# ── 截断尾提示(未截断零变化) ──


def test_roundtable_prompt_hint_on_truncation_only() -> None:
    short_ctx = "短上下文"
    short_board = "第1轮 甲[PASS]:ok"
    plain = participant_system_prompt("", short_ctx, short_board, 1, 2)
    assert TASK_CONTEXT_HINT not in plain
    long_ctx = "长" * 900
    long_board = "唠" * 2100
    hinted = participant_system_prompt("", long_ctx, long_board, 1, 2)
    assert hinted.count(TASK_CONTEXT_HINT) == 2  # 上下文与黑板各附一次


async def test_collab_worker_context_hint_on_truncation() -> None:
    seen_ctx: list[str] = []

    async def runner(subgoal: str, context: str, slot: str | None = None) -> dict[str, object]:
        seen_ctx.append(context)
        return {"verdict": "PASS", "summary": "ok"}

    from kongming_agent.collab_gen import run_collab
    from tests.test_collab_gen import FakeLlm

    llm = FakeLlm([json.dumps([{"subgoal": "甲", "worker": ""}], ensure_ascii=False)])
    long_context = "详" * 2400
    await run_collab(
        payload={"subgoal": "甲", "context_summary": long_context},
        llm=llm,
        worker_runner=runner,
    )
    assert len(seen_ctx) == 1
    assert seen_ctx[0].startswith("详" * 100)  # 截断后仍保留前缀
    assert TASK_CONTEXT_HINT in seen_ctx[0]
    # 对照:未截断不带提示
    seen_ctx.clear()
    llm2 = FakeLlm([json.dumps([{"subgoal": "甲", "worker": ""}], ensure_ascii=False)])
    await run_collab(
        payload={"subgoal": "甲", "context_summary": "短"},
        llm=llm2,
        worker_runner=runner,
    )
    assert TASK_CONTEXT_HINT not in seen_ctx[0]


# ── 续读工具分流(不走通用工具执行面) ──


async def test_dispatch_routes_task_context(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Api:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def task_context(self, *, task_id: str, tenant_key: str) -> dict[str, object] | None:
            self.calls.append({"task_id": task_id, "tenant_key": tenant_key})
            return {"goal": "全文", "steps": [], "truncated": False}

        async def tool_execute(self, **kw: Any) -> Any:
            raise AssertionError("task_context 不得走通用工具执行面")

    api = _Api()
    monkeypatch.setattr(deps, "get_api", lambda: api)
    state: Any = {
        "trace_id": "tr",
        "tenant_key": "default",
        "task_id": "t-own",
        "channel": "task",
        "messages": [],
        "rounds": 0,
        "executed_sigs": [],
        "todos": [],
        "pending_tool_calls": [
            LlmToolCall(call_id="c1", name=TASK_CONTEXT_TOOL_NAME, args={}, raw_args="{}")
        ],
    }
    out = await tool_dispatch(cast("ReactState", state))
    assert api.calls == [{"task_id": "t-own", "tenant_key": "default"}]  # 绑定派发载荷,非 LLM 参数
    msgs = cast("list[dict[str, Any]]", out.get("messages") or [])
    tool_msgs = [m for m in msgs if m.get("role") == "tool"]
    assert tool_msgs and tool_msgs[0].get("tool_call_id") == "c1"
    assert "全文" in str(tool_msgs[0].get("content"))


# ── 装配绑定:worker 请求带 task_id 且工具含内置续读 ──


async def test_collab_wiring_worker_request_binding(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_collab_wiring import (
        FakeCollabApi,
        _make_react_recorder,
    )
    from tests.test_collab_wiring import (
        FakeLlm as WiringFakeLlm,
    )

    api = FakeCollabApi(open_ok=True)
    react, reqs = _make_react_recorder()
    from kongming_agent.collab_wiring import make_collab_runner

    runner = make_collab_runner(
        api=api,  # type: ignore[arg-type]
        llm=WiringFakeLlm([_plan_json(["甲"])]),
        settings=Settings(collab_max_workers=3, collab_max_rounds=3),
        react_runner=react,
        task_id="t-bind",
        tenant_key="default",
        datasource_key="default",
        user_id=None,
        trace_root="tr",
        tools=[{"name": "execute_sql", "description": "d", "parameters": {}}],
    )
    out = await runner("s1", "协作子目标", "", {"max_workers": 2})
    assert str(out.get("verdict")) == "PASS"
    assert reqs, "worker 请求未派发"
    req = cast("dict[str, Any]", reqs[0])
    assert req.get("task_id") == "t-bind"
    tools = [str(t.get("name")) for t in cast("list[dict[str, Any]]", req.get("tools") or [])]
    assert TASK_CONTEXT_TOOL_NAME in tools  # 内置续读在授权交集之外恒在场


def _plan_json(subgoals: list[str]) -> str:
    return json.dumps([{"subgoal": s} for s in subgoals], ensure_ascii=False)
