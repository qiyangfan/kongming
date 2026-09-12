"""backlog-settlement agent 侧测试:收集器经图传播的端到端直证(A-B9)+
LLM 注册表池缓存(021①)。"""

# pyright: reportPrivateUsage=false
# 测试惯例:允许引用兄弟测试模块与被测模块的私有 helper(仓库测试约定,不改名)

import json
from typing import Any, cast

import pytest
from httpx import AsyncClient

import kongming_agent.deps as deps
import kongming_agent.llm as llm_mod
from kongming_agent.apiclient import ToolExecuteOutcome
from kongming_agent.graph.react.build import get_react_graph
from kongming_agent.llm import LlmClient, reset_usage_collector, set_usage_collector
from kongming_agent.main import _build_query_out
from kongming_agent.multimodal import AttachmentError
from tests.test_model_routing import _Recorder, _settings
from tests.test_react import FakeApi, _state, react_recursion_limit


def _tool_completion(*, content: str | None, tool_name: str | None, call_id: str) -> object:
    """构造带 usage 的真 SDK 形态应答(isinstance 解析路径直证)。"""
    from openai.types.chat import (
        ChatCompletion,
        ChatCompletionMessage,
        ChatCompletionMessageFunctionToolCall,
    )
    from openai.types.chat.chat_completion import Choice
    from openai.types.chat.chat_completion_message_function_tool_call import Function
    from openai.types.completion_usage import CompletionUsage

    tool_calls = None
    if tool_name:
        tool_calls = [
            ChatCompletionMessageFunctionToolCall(
                id=call_id,
                type="function",
                function=Function(name=tool_name, arguments=json.dumps({"sql": "SELECT 1"})),
            )
        ]
    msg = ChatCompletionMessage(role="assistant", content=content, tool_calls=tool_calls)  # pyright: ignore[reportArgumentType]  # SDK 存根 list 不变型,Function 子类实参运行时安全
    choice = Choice(index=0, message=msg, finish_reason="tool_calls" if tool_name else "stop")
    return ChatCompletion(
        id="cmpl-x",
        choices=[choice],
        created=1,
        model="fake",
        object="chat.completion",
        usage=CompletionUsage(prompt_tokens=11, completion_tokens=7, total_tokens=18),
    )


class _ScriptCompletions:
    """两拍脚本:第 1 拍回 execute_sql 工具调用,第 2 拍回最终答案。"""

    def __init__(self) -> None:
        self.n = 0

    async def create(self, **kw: object) -> object:  # type: ignore[no-untyped-def]
        self.n += 1
        if self.n == 1:
            return _tool_completion(content=None, tool_name="execute_sql", call_id="c1")
        return _tool_completion(content="共 1 行。", tool_name=None, call_id="c2")


class _ScriptClient:
    def __init__(self) -> None:
        self.chat = type("Chat", (), {"completions": _ScriptCompletions()})()


async def test_collect_usage_e2e_through_react_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A-B9:真 LlmClient(fake OpenAI,带 usage)+ReAct 图两轮——contextvar
    经 LangGraph 节点传播,collect_usage 收集 seq 递增,reporter 零调用。"""
    s = _settings(llm_api_key="sk-main")
    recorder = _Recorder()
    client = LlmClient(s, usage_reporter=recorder)
    client._client = _ScriptClient()  # type: ignore[assignment]
    api = FakeApi(
        [
            ToolExecuteOutcome(
                status="executed",
                result={"verdict": "PASS", "columns": ["v"], "rows": [[1]], "row_count": 1},
            )
        ]
    )
    monkeypatch.setattr(deps, "get_llm", lambda: client)
    monkeypatch.setattr(deps, "get_api", lambda: api)

    collector: list[dict[str, object]] = []
    token = set_usage_collector(collector)
    try:
        # ainvoke 泛型成员 strict 下部分未知(LangGraph),测试边界 cast 收窄为终态 dict
        final = cast(
            "dict[str, Any]",
            await cast("Any", get_react_graph()).ainvoke(
                _state(trace_id="tr-e2e", tenant_key="sakila"),  # type: ignore[call-arg]
                config={"recursion_limit": react_recursion_limit()},
            ),
        )
    finally:
        reset_usage_collector(token)

    assert "error" not in final or final.get("error") is None
    assert len(collector) == 2  # 两轮 LLM 调用各一条
    assert collector[0]["seq"] == 0 and collector[1]["seq"] == 1
    assert collector[0]["total_tokens"] == 18
    assert collector[0]["trace_id"] == "tr-e2e"
    assert collector[0]["tenant_key"] == "sakila"
    assert collector[0]["status"] == "SUCCESS"
    assert recorder.records == []  # 收集器在场不上报

    # 终态构造携带(端点同款路径)
    out = _build_query_out(final)
    assert out.success is True


async def test_attachment_error_carries_usage_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """035⑧:附件预处理失败早返携带已收集记录(vision 第 1 件成功的用量
    不随 AttachmentError 丢弃)。"""
    import kongming_agent.main as main_mod
    from kongming_agent.main import app
    from tests.test_react import TOKEN, _react_payload

    async def _boom(req: object) -> str:
        raise AttachmentError("第 2 张图片预处理失败")

    monkeypatch.setattr(main_mod, "_combine_with_attachments", _boom)
    orig_set = llm_mod.set_usage_collector

    def _spy_set(collector: list[dict[str, object]]) -> object:
        collector.append({"trace_id": "tr-x", "model": "m", "task": "VISION", "seq": 0})
        return orig_set(collector)

    monkeypatch.setattr(llm_mod, "set_usage_collector", _spy_set)
    from httpx import ASGITransport

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post("/query_react", json=_react_payload(collect_usage=True), headers=TOKEN)
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False and body["error"]
    assert len(body["usage_records"]) == 1  # 已收集记录随早返携带
    assert body["usage_records"][0]["task"] == "VISION"


async def test_stream_attachment_error_carries_usage_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R1-F2:流式端点附件预处理在 gen 内(收集器挂载之后)——早返 result 帧
    携带已收集记录(挂载缺位则恒空,本用例直证修复)。"""
    import kongming_agent.main as main_mod
    from kongming_agent.main import app
    from tests.test_react import TOKEN, _react_payload

    async def _boom(req: object) -> str:
        raise AttachmentError("第 2 张图片预处理失败")

    monkeypatch.setattr(main_mod, "_combine_with_attachments", _boom)
    orig_set = llm_mod.set_usage_collector
    seen_collectors: list[list[dict[str, object]]] = []

    def _spy_set(collector: list[dict[str, object]]) -> object:
        seen_collectors.append(collector)
        collector.append({"trace_id": "tr-s", "model": "m", "task": "VISION", "seq": 0})
        return orig_set(collector)

    monkeypatch.setattr(llm_mod, "set_usage_collector", _spy_set)
    from httpx import ASGITransport

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/query_react/stream",
            json=_react_payload(collect_usage=True),
            headers=TOKEN,
        )
    assert resp.status_code == 200
    text = resp.text
    assert "event: result" in text
    import json as _json

    result_line = next(
        ln.removeprefix("data: ") for ln in text.splitlines() if ln.startswith("data:")
    )
    body = _json.loads(result_line)
    assert body["success"] is False and body["error"]
    assert body["verdict"] == "ERROR"  # R2-B2:与同步端点早返 verdict 同构
    assert len(body["usage_records"]) == 1  # combine 段收集的记录随早返携带
    assert body["usage_records"][0]["task"] == "VISION"


# ── 021①:LLM 注册表池缓存 ──


async def test_llm_pool_cached_by_fingerprint(monkeypatch: pytest.MonkeyPatch) -> None:
    """同指纹+同 loop:两次 get_llm 同实例(池不再逐请求重建)。"""
    import kongming_agent.deps as d

    d.reset_llm_cache()
    monkeypatch.setenv("KM_AGENT_LLM_API_KEY", "sk-cache")
    try:
        c1 = d.get_llm()
        c2 = d.get_llm()
        assert c1 is c2
    finally:
        d.reset_llm_cache()
        monkeypatch.delenv("KM_AGENT_LLM_API_KEY", raising=False)


async def test_llm_pool_rebuilt_on_fingerprint_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """指纹变更:重建新实例,旧实例客户端已 aclose(is_closed 直证)。"""
    import kongming_agent.deps as d

    d.reset_llm_cache()
    monkeypatch.setenv("KM_AGENT_LLM_API_KEY", "sk-cache-a")
    c1 = d.get_llm()
    assert c1._client is not None
    monkeypatch.setenv("KM_AGENT_LLM_API_KEY", "sk-cache-b")
    try:
        c2 = d.get_llm()
        assert c2 is not c1
        assert c2._client is not None
        import asyncio

        for _ in range(5):
            await asyncio.sleep(0)  # 让 fire-and-forget aclose task 跑完
        assert c1._client.is_closed()  # 旧实例已关
    finally:
        d.reset_llm_cache()
        monkeypatch.delenv("KM_AGENT_LLM_API_KEY", raising=False)
