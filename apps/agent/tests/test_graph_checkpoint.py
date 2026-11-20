# pyright: reportArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportUnknownArgumentType=false, reportPrivateUsage=false
"""图级检查点与图内挂起测试(agent 面)。

覆盖:控制面回调检查点适配器(往返/降级/写入行)、图内挂起端到端
(confirm_required 挂图 → interrupt 挂起 → Command(resume) 决策兑现 →
循环恢复,批准与拒绝双路径)、编排接线(挂起上账/恢复提示透传/恢复方式
如实入步骤账)。检查点后端为内存 Fake(协议同 /internal/task/graph-checkpoint),
真实 HTTP 链由 api 侧集成测试覆盖。
"""

# pyright: reportPrivateUsage=false

import base64
import json
from typing import Any, cast

import pytest
from langgraph.types import Command

from kongming_agent import deps
from kongming_agent.apiclient import ToolExecuteOutcome
from kongming_agent.graph.checkpoints import (
    InternalCheckpointSaver,
    reset_checkpoint_context,
    set_checkpoint_context,
    thread_id_for_task,
)
from kongming_agent.graph.react.build import get_react_graph, react_recursion_limit
from kongming_agent.graph.react.state import ReactState
from kongming_agent.llm import LlmToolCall, ToolChatReply

TOOLS: list[dict[str, object]] = [
    {
        "name": "create_system_announcement",
        "description": "发布系统公告",
        "parameters": {"type": "object", "properties": {"title": {"type": "string"}}},
        "write": True,
        "require_confirm": True,
    }
]


class FakeLlm:
    def __init__(self, replies: list[ToolChatReply]) -> None:
        self.replies = list(replies)
        self.calls: list[dict[str, object]] = []

    async def chat_with_tools(
        self,
        system: str,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]],
        *,
        temperature: float = 0.1,
        **_kw: object,
    ) -> ToolChatReply:
        self.calls.append({"messages": messages})
        if not self.replies:
            raise AssertionError("FakeLlm.replies 已空,LLM 不应再被调用")
        return self.replies.pop(0)


class FakeCheckpointApi:
    """内存检查点后端 + 工具/确认回调(协议与 /internal/task/graph-checkpoint 同形)。"""

    def __init__(
        self,
        *,
        outcomes: list[ToolExecuteOutcome] | None = None,
        resolve_results: list[dict[str, object] | None] | None = None,
        put_should_fail: bool = False,
    ) -> None:
        self.store: dict[tuple[str, str, str], list[tuple[str, str, bytes]]] = {}
        self.outcomes = list(outcomes or [])
        self.resolve_results = list(resolve_results or [])
        self.put_should_fail = put_should_fail
        self.tool_calls: list[dict[str, object]] = []
        self.resolve_calls: list[dict[str, object]] = []
        self.put_failures = 0
        self.degraded = False

    # ── 检查点协议 ──

    async def graph_checkpoint_put(self, **kw: object) -> bool:
        if self.put_should_fail:
            self.put_failures += 1
            return False
        key = (
            str(kw["thread_id"]),
            str(kw.get("checkpoint_ns") or ""),
            str(kw["kind"]),
        )
        self.store.setdefault(key, []).append(
            (
                str(kw.get("checkpoint_id") or ""),
                str(kw.get("payload_type") or ""),
                bytes(kw.get("payload") or b""),  # type: ignore[arg-type]
            )
        )
        return True

    async def graph_checkpoint_latest(self, **kw: object) -> dict[str, object] | None:
        rows = self.store.get(
            (str(kw["thread_id"]), str(kw.get("checkpoint_ns") or ""), "checkpoint")
        )
        if not rows:
            return None
        cid, ptype, payload = rows[-1]
        return {
            "checkpoint_id": cid,
            "payload_type": ptype,
            "payload_b64": base64.b64encode(payload).decode("ascii"),
        }

    async def graph_checkpoint_writes(self, **kw: object) -> list[dict[str, object]] | None:
        rows = self.store.get((str(kw["thread_id"]), str(kw.get("checkpoint_ns") or ""), "write"))
        return [
            {
                "write_task_id": "t1",
                "payload_type": ptype,
                "payload_b64": base64.b64encode(payload).decode("ascii"),
            }
            for cid, ptype, payload in (rows or [])
            if cid == str(kw.get("checkpoint_id"))
        ]

    async def graph_checkpoint_delete_thread(self, **kw: object) -> bool:
        for key in [k for k in self.store if k[0] == str(kw["thread_id"])]:
            self.store.pop(key, None)
        return True

    # ── 工具/确认回调 ──

    async def tool_execute(self, **kw: object) -> ToolExecuteOutcome:
        self.tool_calls.append(kw)
        if not self.outcomes:
            raise AssertionError("FakeCheckpointApi.outcomes 已空")
        return self.outcomes.pop(0)

    async def tool_resolve_confirm(self, **kw: object) -> dict[str, object] | None:
        self.resolve_calls.append(kw)
        if not self.resolve_results:
            raise AssertionError("FakeCheckpointApi.resolve_results 已空")
        return self.resolve_results.pop(0)


def _call(name: str, args: dict[str, object], call_id: str = "c1") -> LlmToolCall:
    return LlmToolCall(
        call_id=call_id,
        name=name,
        args=args,
        raw_args=json.dumps(args, ensure_ascii=False),
    )


def _state(**kw: object) -> ReactState:
    base: ReactState = {
        "trace_id": "tr-gc",
        "tenant_key": "default",
        "datasource_key": "default",
        "user_id": "u-1",
        "question": "发布修正公告",
        "history": [],
        "tools": TOOLS,
        "rounds": 0,
        "channel": "task",
    }
    base.update(kw)  # type: ignore[typeddict-item]
    return base


def _executed_result() -> dict[str, object]:
    return {
        "status": "executed",
        "result": {"verdict": "PASS", "announcement_id": "a-1", "created": True},
    }


# ============ 适配器 ============


@pytest.mark.asyncio
async def test_saver_roundtrip_and_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    api = FakeCheckpointApi()
    monkeypatch.setattr(deps, "get_api", lambda: api)
    saver = InternalCheckpointSaver()
    set_checkpoint_context(tenant_key="default", task_id="t-1", step_id="s-1", trace_id="tr")
    try:
        config = cast("Any", {"configurable": {"thread_id": thread_id_for_task("t-1", "s-1")}})
        checkpoint: dict[str, Any] = {
            "v": 1,
            "id": "ck-9",
            "ts": "t",
            "channel_values": {},
            "channel_versions": {},
            "versions_seen": {},
        }
        metadata: dict[str, Any] = {"source": "loop", "step": 2}
        out_config = await saver.aput(config, checkpoint, metadata, {})  # type: ignore[arg-type]
        out_cfg = cast("Any", out_config)
        assert out_cfg["configurable"]["checkpoint_id"] == "ck-9"
        tup = await saver.aget_tuple(config)
        assert tup is not None
        assert cast("Any", tup.checkpoint)["id"] == "ck-9"
        assert cast("Any", tup.metadata)["step"] == 2
        await saver.aput_writes(out_config, [("messages", {"x": 1})], "task-abc")
        tup2 = await saver.aget_tuple(config)
        assert tup2 is not None and tup2.pending_writes is not None
        assert tup2.pending_writes[0][1] == "messages"
        await saver.adelete_thread("task:t-1:step:s-1")
        assert await saver.aget_tuple(config) is None
    finally:
        reset_checkpoint_context()


@pytest.mark.asyncio
async def test_saver_write_failure_degrades_not_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """控制面拒绝 → 降级(返回配置不抛断);读取缺席 → None(整步回退)。"""
    api = FakeCheckpointApi(put_should_fail=True)
    monkeypatch.setattr(deps, "get_api", lambda: api)
    saver = InternalCheckpointSaver()
    set_checkpoint_context(tenant_key="default", task_id="t-1", step_id="s-1", trace_id="tr")
    try:
        config = cast("Any", {"configurable": {"thread_id": "task:t-1:step:s-1"}})
        checkpoint: dict[str, Any] = {
            "v": 1,
            "id": "ck-1",
            "ts": "t",
            "channel_values": {},
            "channel_versions": {},
            "versions_seen": {},
        }
        out = await saver.aput(config, checkpoint, {"source": "loop", "step": 1}, {})  # type: ignore[arg-type]
        assert cast("Any", out)["configurable"]["thread_id"] == "task:t-1:step:s-1"
        assert api.put_failures == 1
        assert await saver.aget_tuple(config) is None
    finally:
        reset_checkpoint_context()


# ============ 图内挂起端到端 ============


def _graph_api() -> FakeCheckpointApi:
    return FakeCheckpointApi(
        outcomes=[
            ToolExecuteOutcome(status="confirm_required", confirm_id="cf-1", reason="需用户确认")
        ],
        resolve_results=[_executed_result()],
    )


@pytest.mark.asyncio
async def test_task_channel_suspends_on_confirm(monkeypatch: pytest.MonkeyPatch) -> None:
    """任务通道 confirm_required → 图挂起(挂起态含确认载荷),工具未重复调用。"""
    llm = FakeLlm(
        [ToolChatReply(tool_calls=[_call("create_system_announcement", {"title": "修正"})])]
    )
    api = _graph_api()
    monkeypatch.setattr(deps, "get_llm", lambda: llm)
    monkeypatch.setattr(deps, "get_api", lambda: api)
    graph = get_react_graph(checkpointed=True)
    set_checkpoint_context(tenant_key="default", task_id="t-9", step_id="s-9", trace_id="tr")
    try:
        config = {
            "recursion_limit": react_recursion_limit(),
            "configurable": {"thread_id": thread_id_for_task("t-9", "s-9")},
        }
        final = cast("dict[str, Any]", await graph.ainvoke(_state(), config=config))
    finally:
        reset_checkpoint_context()
    assert "__interrupt__" in final
    intr = final["__interrupt__"][0]
    assert intr.value["kind"] == "tool_confirm"
    assert intr.value["confirm_id"] == "cf-1"
    assert len(api.tool_calls) == 1  # 挂起前动作未执行(PENDING_CONFIRM 非执行)


@pytest.mark.asyncio
async def test_resume_approved_executes_and_continues(monkeypatch: pytest.MonkeyPatch) -> None:
    """批准恢复:决策经 resolve 兑换为执行,观察喂回 LLM 循环收口。"""
    llm = FakeLlm(
        [
            ToolChatReply(tool_calls=[_call("create_system_announcement", {"title": "修正"})]),
            ToolChatReply(content="公告已发布。"),
        ]
    )
    api = _graph_api()
    monkeypatch.setattr(deps, "get_llm", lambda: llm)
    monkeypatch.setattr(deps, "get_api", lambda: api)
    graph = get_react_graph(checkpointed=True)
    set_checkpoint_context(tenant_key="default", task_id="t-8", step_id="s-8", trace_id="tr")
    try:
        config = {
            "recursion_limit": react_recursion_limit(),
            "configurable": {"thread_id": thread_id_for_task("t-8", "s-8")},
        }
        await graph.ainvoke(_state(), config=config)
        final = cast(
            "dict[str, Any]",
            await graph.ainvoke(
                Command(resume={"decision": "approved", "confirm_id": "cf-1"}), config=config
            ),
        )
    finally:
        reset_checkpoint_context()
    assert "__interrupt__" not in final
    assert final.get("summary") == "公告已发布。"
    # 兑换回调:批准决策到达控制面(全量复校在控制面)
    assert len(api.resolve_calls) == 1
    assert api.resolve_calls[0]["decision"] == "approve"
    assert api.resolve_calls[0]["confirm_id"] == "cf-1"
    # 工具只被回调一次(PENDING_CONFIRM);执行发生在 resolve 兑换
    assert len(api.tool_calls) == 1
    # LLM 第二轮收到兑现观察(原 call_id 回填)
    msgs = cast("list[dict[str, object]]", llm.calls[1]["messages"])
    tool_msgs = [m for m in msgs if m.get("role") == "tool"]
    assert len(tool_msgs) == 1 and "announcement_id" in str(tool_msgs[0].get("content"))


@pytest.mark.asyncio
async def test_resume_denied_feeds_rejection_observation(monkeypatch: pytest.MonkeyPatch) -> None:
    """拒绝恢复:拒绝观察喂回(动作跳过),LLM 如实收口,不虚报执行。"""
    llm = FakeLlm(
        [
            ToolChatReply(tool_calls=[_call("create_system_announcement", {"title": "修正"})]),
            ToolChatReply(content="用户拒绝,未执行发布。"),
        ]
    )
    api = FakeCheckpointApi(
        outcomes=[
            ToolExecuteOutcome(status="confirm_required", confirm_id="cf-2", reason="需用户确认")
        ],
        resolve_results=[{"status": "rejected", "reason": "用户拒绝该动作,已跳过"}],
    )
    monkeypatch.setattr(deps, "get_llm", lambda: llm)
    monkeypatch.setattr(deps, "get_api", lambda: api)
    graph = get_react_graph(checkpointed=True)
    set_checkpoint_context(tenant_key="default", task_id="t-7", step_id="s-7", trace_id="tr")
    try:
        config = {
            "recursion_limit": react_recursion_limit(),
            "configurable": {"thread_id": thread_id_for_task("t-7", "s-7")},
        }
        await graph.ainvoke(_state(), config=config)
        final = cast(
            "dict[str, Any]",
            await graph.ainvoke(
                Command(resume={"decision": "denied", "confirm_id": "cf-2"}), config=config
            ),
        )
    finally:
        reset_checkpoint_context()
    assert final.get("summary") == "用户拒绝,未执行发布。"
    assert api.resolve_calls[0]["decision"] == "reject"
    assert len(api.tool_calls) == 1  # 拒绝路径零执行


@pytest.mark.asyncio
async def test_interactive_channel_unaffected_by_confirm(monkeypatch: pytest.MonkeyPatch) -> None:
    """交互通道 confirm_required 维持既有形态(引导终止循环,零挂图零检查点)。"""
    from kongming_agent.apiclient import ToolExecuteOutcome as TEO

    llm = FakeLlm(
        [
            ToolChatReply(tool_calls=[_call("create_system_announcement", {"title": "修正"})]),
            ToolChatReply(content="该操作待确认。"),
        ]
    )
    api = FakeCheckpointApi(
        outcomes=[TEO(status="confirm_required", confirm_id="cf-3", reason="需用户确认")],
    )
    monkeypatch.setattr(deps, "get_llm", lambda: llm)
    monkeypatch.setattr(deps, "get_api", lambda: api)
    graph = get_react_graph(checkpointed=False)
    final = cast(
        "dict[str, Any]",
        await graph.ainvoke(
            _state(channel="chat"),  # type: ignore[arg-type]
            config={"recursion_limit": react_recursion_limit()},
        ),
    )
    assert "__interrupt__" not in final
    assert final.get("summary") == "该操作待确认。"
    assert not api.resolve_calls  # 图内没有待确认操作，无需兑换确认。


@pytest.mark.asyncio
async def test_resume_hint_mismatch_resuspends_without_executing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """恢复提示复合校验(安全红线):提示 confirm_id 与快照在途确认失配 →
    不兑现不执行,confirm_gate 原地重挂同一确认(幂等复用)——线程内先后
    挂起过多个确认时,旧决策不得套到新在途确认上(未决动作被错兑=越权)。"""
    from kongming_agent.main import QueryReactIn, _run_react_task

    llm = FakeLlm(
        [ToolChatReply(tool_calls=[_call("create_system_announcement", {"title": "修正"})])]
    )
    api = _graph_api()
    monkeypatch.setattr(deps, "get_llm", lambda: llm)
    monkeypatch.setattr(deps, "get_api", lambda: api)
    req = QueryReactIn(
        trace_id="tr",
        tenant_key="default",
        datasource_key="default",
        user_id="u-1",
        question="发布修正公告",
        history=[],
        tools=TOOLS,
        channel="task",
    )
    first = await _run_react_task(req, task_id="t-6", step_id="s-6", resume_confirm=None)
    assert isinstance(first.get("suspended"), dict)
    # 漂移提示:决策携带别的确认标识 → 不得兑现,原地重挂等待该确认自身处置
    second = await _run_react_task(
        req,
        task_id="t-6",
        step_id="s-6",
        resume_confirm={"confirm_id": "cf-other", "decision": "approved"},
    )
    suspended = cast("dict[str, Any]", second.get("suspended"))
    assert suspended["confirm_id"] == "cf-1"
    assert api.resolve_calls == []  # 失配提示未兑换
    assert len(api.tool_calls) == 1  # 动作仍未执行(重挂非重放)
    assert len(llm.calls) == 1  # 重挂不进入新执行轮(快照回放,零 LLM 消耗)
