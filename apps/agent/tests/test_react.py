"""ReAct 内核测试(019 tool-calling):LLM 自主编排/终止/上限/畸形/确认观察/端点同构。

mock 形态与 test_nodes.py 同款:FakeLlm 脚本化 chat_with_tools 应答,
FakeApi 记录 tool_execute 回调;deps 单例 monkeypatch。
"""

# pyright: reportPrivateUsage=false
# 测试惯例:_observe 为被测模块私有实现,直引锁截断语义(仓库约定,不改名)

import json
from typing import Any, cast

import pytest
from httpx import ASGITransport, AsyncClient

from kongming_agent import deps
from kongming_agent.apiclient import ToolExecuteOutcome
from kongming_agent.graph.react import nodes
from kongming_agent.graph.react.build import get_react_graph, react_recursion_limit
from kongming_agent.graph.react.state import ReactState
from kongming_agent.llm import LlmToolCall, ToolChatReply
from kongming_agent.main import app

TOKEN = {"X-Internal-Token": "test-internal-token"}

TOOLS: list[dict[str, object]] = [
    {
        "name": "execute_sql",
        "description": "执行只读 SQL",
        "parameters": {"type": "object", "properties": {"sql": {"type": "string"}}},
        "write": False,
        "require_confirm": False,
    }
]


class FakeLlm:
    """按队列脚本应答 chat_with_tools;记录 messages 形态供断言。"""

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
        **_kw: object,  # 021:task/trace_id/tenant_key 吸收
    ) -> ToolChatReply:
        self.calls.append({"system": system, "messages": messages, "tools": tools})
        if not self.replies:
            raise AssertionError("FakeLlm.replies 已空,LLM 不应再被调用")
        return self.replies.pop(0)


class FakeApi:
    def __init__(self, outcomes: list[ToolExecuteOutcome] | None = None) -> None:
        self.outcomes = list(outcomes or [])
        self.tool_calls: list[dict[str, object]] = []

    async def tool_execute(self, **kw: object) -> ToolExecuteOutcome:
        self.tool_calls.append(kw)
        if not self.outcomes:
            raise AssertionError("FakeApi.outcomes 已空,tool_execute 不应再被调用")
        return self.outcomes.pop(0)


def _call(name: str, args: dict[str, object] | None, call_id: str = "c1") -> LlmToolCall:
    return LlmToolCall(
        call_id=call_id,
        name=name,
        args=args,
        raw_args=json.dumps(args, ensure_ascii=False) if args is not None else "{bad json",
    )


def _state(**kw: object) -> ReactState:
    base: ReactState = {
        "trace_id": "tr-t1",
        "tenant_key": "default",
        "datasource_key": "default",
        "user_id": "u-1",
        "question": "有多少部电影",
        "history": [],
        "tools": TOOLS,
        "rounds": 0,
    }
    base.update(kw)  # type: ignore[typeddict-item]
    return base


@pytest.fixture
def _patch(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """每用例装配 deps;返回 (llm, api) 由用例自行决定脚本。"""

    def _install(llm: FakeLlm, api: FakeApi) -> None:
        monkeypatch.setattr(deps, "get_llm", lambda: llm)
        monkeypatch.setattr(deps, "get_api", lambda: api)

    return _install


async def _ainvoke(state: ReactState) -> dict[str, Any]:
    """get_react_graph().ainvoke 成员型 strict 下部分未知(LangGraph 泛型),
    测试边界一次收窄为终态 state dict(自构造 state,键型已知)。"""
    graph = cast("Any", get_react_graph())
    return cast(
        "dict[str, Any]",
        await graph.ainvoke(state, config={"recursion_limit": react_recursion_limit()}),
    )


def _tool_contents(messages: object) -> list[str]:
    """FakeLlm 记录的 messages 中 role=tool 的 content 列表(边界 cast 收窄)。"""
    typed = cast("list[dict[str, object]]", messages)
    return [cast("str", m.get("content", "")) for m in typed if m.get("role") == "tool"]


# ============ 图级:自主编排全链 ============


async def test_react_full_loop(_patch) -> None:  # type: ignore[no-untyped-def]
    """LLM 第一轮调 execute_sql → 观察 → 第二轮产出最终答案。"""
    llm = FakeLlm(
        [
            ToolChatReply(tool_calls=[_call("execute_sql", {"sql": "SELECT count(*) FROM film"})]),
            ToolChatReply(content="共有 1000 部电影。"),
        ]
    )
    api = FakeApi(
        [
            ToolExecuteOutcome(
                status="executed",
                result={
                    "verdict": "PASS",
                    "columns": ["count"],
                    "rows": [[1000]],
                    "row_count": 1,
                    "duration_ms": 12,
                },
            )
        ]
    )
    _patch(llm, api)
    final = await _ainvoke(_state())
    # 终态同构:execution/summary/generated_sql/retry_count
    assert final["summary"] == "共有 1000 部电影。"
    assert final["execution"].verdict == "PASS"
    assert final["execution"].rows == [[1000]]
    assert final["generated_sql"] == "SELECT count(*) FROM film"
    assert final["retry_count"] == 1
    assert final["stats"]["rows"] == 1
    # 回调参数:trace/租户/用户/数据源贯穿,零凭证字段
    call = api.tool_calls[0]
    assert call["trace_id"] == "tr-t1"
    assert call["tenant_key"] == "default"
    assert call["user_id"] == "u-1"
    assert set(call.keys()) <= {
        "trace_id", "tenant_key", "datasource_key", "user_id", "tool_name", "args",
        "channel",  # 025
    }
    # LLM 第二轮收到的 messages 含 tool 观察(行数据在观察内)
    tool_msgs = _tool_contents(llm.calls[1]["messages"])
    assert len(tool_msgs) == 1
    assert "1000" in tool_msgs[0]


async def test_react_terminate_without_tools(_patch) -> None:  # type: ignore[no-untyped-def]
    """LLM 首轮即产出最终文本:循环终止,零工具回调,纯文本成功终态。"""
    llm = FakeLlm([ToolChatReply(content="你好,我是孔明。")])
    api = FakeApi()
    _patch(llm, api)
    final = await _ainvoke(_state())
    assert final["summary"] == "你好,我是孔明。"
    assert final["execution"].verdict == "PASS"  # 无工具执行:成功空结果
    assert final["execution"].rows == []
    assert "generated_sql" not in final
    assert api.tool_calls == []


async def test_react_round_limit_forces_stop(_patch) -> None:  # type: ignore[no-untyped-def]
    """LLM 连续要调工具不收敛:达上限强制终止,结构化错误终态。"""
    from kongming_agent.settings import Settings

    limit = Settings().max_tool_rounds
    # 每轮不同 SQL(避开重复熔断):纯测轮次上限语义
    llm = FakeLlm(
        [
            ToolChatReply(tool_calls=[_call("execute_sql", {"sql": f"SELECT {i}"}, f"c{i}")])
            for i in range(limit + 1)
        ]
    )
    api = FakeApi(
        [
            ToolExecuteOutcome(
                status="executed",
                result={"verdict": "PASS", "columns": ["?column?"], "rows": [[1]], "row_count": 1},
            )
        ]
        * limit
    )
    _patch(llm, api)
    final = await _ainvoke(_state())
    assert "上限" in str(final.get("error") or "")
    assert final["retry_count"] == limit
    assert len(api.tool_calls) == limit  # 超限后不再执行
    # A-B6:触顶不丢已产出部分结果——早前成功轮的 execution/rows 随终态携带
    exec_obj = final.get("execution")
    assert exec_obj is not None and exec_obj.verdict == "PASS"
    assert exec_obj.rows == [[1]]
    assert final.get("stats")


async def test_react_malformed_args_fed_back(_patch) -> None:  # type: ignore[no-untyped-def]
    """工具参数 JSON 畸形:不回调控制面,畸形观察喂回 LLM 自纠。"""
    llm = FakeLlm(
        [
            ToolChatReply(tool_calls=[_call("execute_sql", None)]),
            ToolChatReply(content="参数有误,我重新组织。"),
        ]
    )
    api = FakeApi()
    _patch(llm, api)
    final = await _ainvoke(_state())
    assert api.tool_calls == []  # 畸形参数未到达控制面
    tool_msgs = _tool_contents(llm.calls[1]["messages"])
    assert "畸形" in tool_msgs[0]
    assert final["summary"] == "参数有误,我重新组织。"


async def test_react_confirm_required_guides_stop(_patch) -> None:  # type: ignore[no-untyped-def]
    """写工具待确认:观察含停止指引,LLM 据此终止并说明。"""
    llm = FakeLlm(
        [
            ToolChatReply(
                tool_calls=[
                    _call(
                        "create_system_announcement",
                        {"title": "明日维护", "content": "停机"},
                    )
                ]
            ),
            ToolChatReply(content="发布公告需您确认,已在卡片中列出。"),
        ]
    )
    api = FakeApi(
        [ToolExecuteOutcome(status="confirm_required", confirm_id="cf-1", reason="需用户确认")]
    )
    _patch(llm, api)
    final = await _ainvoke(_state())
    assert "确认" in final["summary"]
    tool_msgs = _tool_contents(llm.calls[1]["messages"])
    assert "停止" in tool_msgs[0]


async def test_react_empty_tools_degenerates(_patch) -> None:  # type: ignore[no-untyped-def]
    """空工具集(无权限注入):LLM 收到空 tools,退化纯文本问答。"""
    llm = FakeLlm([ToolChatReply(content="我无法访问数据工具。")])
    api = FakeApi()
    _patch(llm, api)
    final = await _ainvoke(_state(tools=[]))
    assert llm.calls[0]["tools"] == []
    assert final["summary"] == "我无法访问数据工具。"
    assert final["execution"].verdict == "PASS"


async def test_react_rejected_observation_fed_back(_patch) -> None:  # type: ignore[no-untyped-def]
    """工具被防御层拒绝:rejected 观察喂回,LLM 换思路产出答案。"""
    llm = FakeLlm(
        [
            ToolChatReply(tool_calls=[_call("ghost_tool", {})]),
            ToolChatReply(content="该工具不存在,我改用现有能力回答。"),
        ]
    )
    api = FakeApi([ToolExecuteOutcome(status="rejected", reason="TOOL_NOT_REGISTERED")])
    _patch(llm, api)
    final = await _ainvoke(_state())
    assert api.tool_calls[0]["tool_name"] == "ghost_tool"
    assert "该工具不存在" in final["summary"]


# ============ 端点级:同构与故障收敛 ============


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    llm = FakeLlm(
        [
            ToolChatReply(tool_calls=[_call("execute_sql", {"sql": "SELECT 1"})]),
            ToolChatReply(content="结果是 1。"),
        ]
    )
    api = FakeApi(
        [
            ToolExecuteOutcome(
                status="executed",
                result={
                    "verdict": "PASS",
                    "columns": ["?column?"],
                    "rows": [[1]],
                    "row_count": 1,
                    "duration_ms": 3,
                },
            )
        ]
    )
    monkeypatch.setattr(deps, "get_llm", lambda: llm)
    monkeypatch.setattr(deps, "get_api", lambda: api)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _react_payload(**kw: object) -> dict[str, object]:
    base: dict[str, object] = {
        "trace_id": "tr-ep1",
        "tenant_key": "default",
        "question": "测试",
        "tools": TOOLS,
    }
    base.update(kw)
    return base


async def test_query_react_requires_token() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        assert (await c.post("/query_react", json={})).status_code == 403


def _collector_spy(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """set_usage_collector 调用计数(035 R1-S2:端点挂载闸直证)。"""
    import kongming_agent.llm as llm_mod

    calls: list[int] = []
    orig = llm_mod.set_usage_collector

    def _spy(collector: list[dict[str, object]]):  # type: ignore[no-untyped-def]
        calls.append(1)
        return orig(collector)

    monkeypatch.setattr(llm_mod, "set_usage_collector", _spy)
    return calls


async def test_collect_usage_default_off(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch  # type: ignore[valid-type]
) -> None:
    """035 R1-S2:collect_usage 缺省 False → 端点不挂收集器(chat 同步
    react/workflow 走 fire-and-forget 旧路,用量不整丢)。"""
    calls = _collector_spy(monkeypatch)
    async with client as c:
        resp = await c.post("/query_react", json=_react_payload(), headers=TOKEN)
    assert resp.status_code == 200
    assert resp.json()["usage_records"] == []
    assert calls == []


async def test_collect_usage_on_collects(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch  # type: ignore[valid-type]
) -> None:
    """035 R1-S2:collect_usage=True(bot 链路)→ 端点挂收集器,响应携带明细。"""
    calls = _collector_spy(monkeypatch)
    async with client as c:
        resp = await c.post(
            "/query_react", json=_react_payload(collect_usage=True), headers=TOKEN
        )
    assert resp.status_code == 200
    assert len(calls) == 1


async def test_query_react_happy_path(client: AsyncClient) -> None:  # type: ignore[valid-type]
    async with client as c:
        resp = await c.post("/query_react", json=_react_payload(), headers=TOKEN)
    assert resp.status_code == 200
    body = resp.json()
    # 与 /query 逐字段同构;035 纯增 usage_records(bot 链路同步采集明细,
    # 本端点挂收集器——替身 LLM 无 usage 属性时为空列表)
    assert set(body.keys()) == {
        "success", "verdict", "summary", "insights", "stats",
        "columns", "rows", "sql", "retry_count", "error", "usage_records",
    }
    assert body["success"] is True and body["verdict"] == "PASS"
    assert body["summary"] == "结果是 1。"
    assert body["columns"] == ["?column?"] and body["rows"] == [[1]]
    assert body["sql"] == "SELECT 1"
    assert body["retry_count"] == 1


async def test_query_react_llm_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    from kongming_agent.llm import LlmError

    class _BoomLlm:
        async def chat_with_tools(self, *a: object, **kw: object) -> ToolChatReply:
            raise LlmError("LLM 调用失败: 模拟故障")

    monkeypatch.setattr(deps, "get_llm", lambda: _BoomLlm())
    monkeypatch.setattr(deps, "get_api", lambda: FakeApi())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        resp = await c.post("/query_react", json=_react_payload(), headers=TOKEN)
    body = resp.json()
    assert resp.status_code == 200
    assert body["success"] is False
    assert "不可用" in (body["error"] or "")


async def test_query_react_stream_events(client: AsyncClient) -> None:  # type: ignore[valid-type]
    """流式:stage/tool_call/summary/result 事件序列;result 终态与同步同构。"""
    async with client as c:
        resp = await c.post("/query_react/stream", json=_react_payload(), headers=TOKEN)
    assert resp.status_code == 200
    text = resp.text
    events = [ln[7:] for ln in text.splitlines() if ln.startswith("event: ")]
    # tool_call running→done 成对;stage 成对;summary 与 result 终态
    assert "tool_call" in events
    tool_frames = [
        ln[6:] for ln in text.splitlines()
        if ln.startswith("data: ") and '"tool_name"' in ln
    ]
    statuses = [json.loads(f)["status"] for f in tool_frames]
    assert statuses == ["running", "done"]
    assert events[-1] == "result"
    # result 终态字段同构
    result_data = [
        ln[6:] for ln in text.splitlines() if ln.startswith("data: ")
    ][-1]
    final = json.loads(result_data)
    assert final["success"] is True and final["summary"] == "结果是 1。"


async def test_react_duplicate_call_circuit_breaker(_patch) -> None:  # type: ignore[no-untyped-def]
    """重复熔断(UI 冒烟实证):同工具同参数已成功过的调用不再回调控制面,
    观察引导 LLM 基于已有信息作答。"""
    llm = FakeLlm(
        [
            ToolChatReply(tool_calls=[_call("execute_sql", {"sql": "SELECT 1"})]),
            ToolChatReply(tool_calls=[_call("execute_sql", {"sql": "SELECT 1"}, "c2")]),
            ToolChatReply(content="已有结果,答案是 1。"),
        ]
    )
    api = FakeApi(
        [
            ToolExecuteOutcome(
                status="executed",
                result={"verdict": "PASS", "columns": ["?column?"], "rows": [[1]], "row_count": 1},
            )
        ]
    )
    _patch(llm, api)
    final = await _ainvoke(_state())
    assert len(api.tool_calls) == 1  # 第二次重复调用被熔断,未到达控制面
    # 第三轮 LLM 收到的 messages 含熔断观察
    tool_msgs = _tool_contents(llm.calls[2]["messages"])
    assert "已成功执行过" in tool_msgs[-1]
    assert final["summary"] == "已有结果,答案是 1。"


async def test_react_blocked_sql_not_deduped(_patch) -> None:  # type: ignore[no-untyped-def]
    """审查循环 S1:被 BLOCKED 的 execute_sql 不入熔断集——工具通道 status 恒为
    executed 而 SQL verdict 在 result 内,若入集,LLM 重发同条 SQL 会收到
    「已成功执行过」假观察并据此编造数据。"""
    blocked = ToolExecuteOutcome(
        status="executed",
        result={"verdict": "BLOCKED", "reason": "BLOCKED_ROOT: 删除语句"},
    )
    llm = FakeLlm(
        [
            ToolChatReply(tool_calls=[_call("execute_sql", {"sql": "DELETE FROM t"})]),
            ToolChatReply(tool_calls=[_call("execute_sql", {"sql": "DELETE FROM t"}, "c2")]),
            ToolChatReply(content="该操作被拒绝。"),
        ]
    )
    api = FakeApi([blocked, blocked])
    _patch(llm, api)
    final = await _ainvoke(_state())
    assert len(api.tool_calls) == 2  # 第二次同参调用未被熔断,真回调
    tool_msgs = _tool_contents(llm.calls[2]["messages"])
    assert "已成功执行过" not in tool_msgs[-1]  # 无假成功观察
    assert final["last_execution"].verdict == "BLOCKED"


# ============ 纯函数 ============

def test_build_react_system_bot_injection() -> None:
    """025:Bot 身份段替换默认身份,内核纪律后缀完整保留;空=逐字零回归。"""
    from kongming_agent.graph.react.prompts import (
        REACT_SYSTEM,
        build_react_system,
    )

    assert build_react_system("", has_tools=True) == REACT_SYSTEM
    out = build_react_system("你是售后客服助手", has_tools=True)
    assert out.startswith("你是售后客服助手")
    assert "孔明数据助手" not in out  # 默认身份被替换
    for marker in ("工具使用纪律", "效率纪律", "终止纪律", "create_system_announcement"):
        assert marker in out  # 纪律后缀完整保留


def test_build_react_system_no_tools_discipline() -> None:
    """能力-契约一致:注入工具集为空时纪律段替换为无工具模式——
    禁伪调用/禁伪代码,数据类问题如实说明;工具教学不得残留(治本闸①)。"""
    from kongming_agent.graph.react.prompts import (
        REACT_DISCIPLINE,
        REACT_IDENTITY_DEFAULT,
        REACT_NO_TOOLS_DISCIPLINE,
        build_react_system,
    )

    # Bot 注入路径:身份段替换 + 无工具纪律段
    out = build_react_system("你是售后客服助手", has_tools=False)
    assert out.startswith("你是售后客服助手")
    assert "没有任何可用工具" in out
    assert "不要尝试调用工具" in out
    assert "不得编造" in out  # 数据类问题如实说明,不编造
    for banned in (
        "rag_retrieve",
        "execute_sql",
        "validate_sql",
        "工具使用纪律",
        "效率纪律",
        "终止纪律",
    ):
        assert banned not in out  # 工具教学零残留
    # 空身份路径:默认身份 + 无工具纪律段(与 REACT_SYSTEM 组装同构)
    empty = build_react_system("", has_tools=False)
    assert empty == REACT_IDENTITY_DEFAULT + REACT_NO_TOOLS_DISCIPLINE
    assert REACT_DISCIPLINE not in empty


async def test_react_no_tools_uses_no_tools_discipline(_patch) -> None:  # type: ignore[no-untyped-def]
    """图级:空工具集进图时 LLM 收到无工具纪律 system(非工具教学)。"""
    llm = FakeLlm([ToolChatReply(content="当前助手未配置数据工具,无法执行该查询。")])
    api = FakeApi()
    _patch(llm, api)
    final = await _ainvoke(_state(tools=[]))
    system = str(llm.calls[0]["system"])
    assert "没有任何可用工具" in system
    assert "rag_retrieve" not in system and "execute_sql" not in system
    # 既有退化契约保持:纯文本应答 → PASS 空结果集终态
    assert final["summary"] == "当前助手未配置数据工具,无法执行该查询。"
    assert final["execution"].verdict == "PASS"


def test_openai_tools_shape() -> None:
    # 返回声明为 dict[str, object],嵌套键按本测试构造的实型收窄后断言
    out = cast("list[dict[str, Any]]", nodes.openai_tools(TOOLS))
    assert out[0]["type"] == "function"
    assert out[0]["function"]["name"] == "execute_sql"
    assert out[0]["function"]["parameters"]["type"] == "object"


def test_observe_truncates_rows() -> None:
    rows = [[i] for i in range(50)]
    obs = nodes._observe(
        ToolExecuteOutcome(
            status="executed",
            result={"verdict": "PASS", "columns": ["x"], "rows": rows, "row_count": 50},
        )
    )
    result = obs["result"]
    assert isinstance(result, dict)
    # isinstance 收窄产物 dict[Unknown, Unknown] 仍未知,按 _observe 构造实型收窄
    result = cast("dict[str, object]", result)
    assert len(cast("list[object]", result["rows"])) == nodes.MAX_OBSERVE_ROWS
    assert result["rows_truncated"] is True
