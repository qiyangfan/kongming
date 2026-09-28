"""多协议翻译层测试(multi-protocol-llm):anthropic/responses 翻译纯函数
表驱动 + 官方 SDK 客户端 MockTransport 集成钉子 + llm.py 三通道集成组。"""

import asyncio
import json
from typing import cast

import httpx
import httpx2
import pytest
from openai import AsyncOpenAI

from kongming_agent.llm import LlmClient
from kongming_agent.llm_protocol import (
    DEFAULT_MAX_TOKENS,
    AnthropicClient,
    LlmError,
    ProtocolReply,
    ProtocolUsage,
    anthropic_parse,
    anthropic_request,
    responses_parse,
    responses_request,
)
from kongming_agent.settings import Settings


def _msgs_of(req: dict[str, object], key: str = "messages") -> list[dict[str, object]]:
    """请求 kwargs 中 messages/input 列表的收窄读取(测试断言用)。"""
    return cast("list[dict[str, object]]", req[key])


def _parts_of(item: dict[str, object]) -> list[dict[str, object]]:
    """消息项 content 块列表的收窄读取(测试断言用)。"""
    return cast("list[dict[str, object]]", item["content"])


# ── canonical 消息便捷构造(与 ReAct 内核产出形态逐字同形) ──
SYS = {"role": "system", "content": "你是 SQL 助手"}
USER = {"role": "user", "content": "有多少商品?"}
ASSISTANT_TC = {
    "role": "assistant",
    "content": "",
    "tool_calls": [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "execute_sql", "arguments": '{"sql": "SELECT 1"}'},
        }
    ],
}
TOOL_OBS = {"role": "tool", "tool_call_id": "call_1", "content": '{"rows": 4}'}


class _Block:
    """模拟 SDK 应答对象(attribute 形态;翻译层以 getattr 读取,不依赖具体类型)。"""

    def __init__(self, **kw: object) -> None:
        self.__dict__.update(kw)


class TestAnthropicRequest:
    """canonical → anthropic Messages 请求形态(翻译表:设计决策 4)。"""

    def test_assistant_without_content_rejected_locally(self) -> None:
        """空 content 且无 tool_calls 的 assistant 消息:本地即 LlmError,
        不产出注定被 Messages API 400 拒收的空 text 块。"""
        with pytest.raises(LlmError, match="无可回放内容"):
            anthropic_request(messages=[{"role": "assistant", "content": ""}], tools=[])

    def test_system_top_and_default_max_tokens(self) -> None:
        req = anthropic_request(messages=[SYS, USER], tools=[])
        assert req["system"] == "你是 SQL 助手"  # system 置顶,不进消息史
        assert req["messages"] == [{"role": "user", "content": "有多少商品?"}]
        assert req["max_tokens"] == DEFAULT_MAX_TOKENS  # 必填上限的文档化缺省
        assert "temperature" not in req  # SDK 1.8.0 类型面已移除,不发送
        assert "tools" not in req  # 空工具不产出 tools 键

    def test_explicit_max_tokens(self) -> None:
        req = anthropic_request(messages=[SYS, USER], tools=[], max_tokens=100)
        assert req["max_tokens"] == 100

    def test_tool_round_trip_blocks(self) -> None:
        """assistant.tool_calls → tool_use 块(args 解析为对象);
        role:tool → user 消息内 tool_result 块(tool_use_id 对齐)。"""
        req = anthropic_request(messages=[SYS, USER, ASSISTANT_TC, TOOL_OBS], tools=[])
        msgs = _msgs_of(req)
        assistant = msgs[1]
        assert assistant["role"] == "assistant"
        assert {
            "type": "tool_use",
            "id": "call_1",
            "name": "execute_sql",
            "input": {"sql": "SELECT 1"},
        } in _parts_of(assistant)
        tool_msg = msgs[2]
        assert tool_msg["role"] == "user"
        assert tool_msg["content"] == [
            {"type": "tool_result", "tool_use_id": "call_1", "content": '{"rows": 4}'}
        ]

    def test_malformed_args_replayed_as_empty_object(self) -> None:
        """畸形 arguments 以 {} 回放(参数畸形观察已由内核侧喂回自纠)。"""
        bad = {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "c2",
                    "type": "function",
                    "function": {"name": "x", "arguments": "{bad json"},
                }
            ],
        }
        req = anthropic_request(messages=[USER, bad], tools=[])
        assert _parts_of(_msgs_of(req)[1]) == [
            {"type": "tool_use", "id": "c2", "name": "x", "input": {}}
        ]

    def test_consecutive_tool_results_merge(self) -> None:
        """连续多条 tool 观察合并为一条 user 消息(anthropic 块语义)。"""
        second = {"role": "tool", "tool_call_id": "call_2", "content": '{"y": 2}'}
        req = anthropic_request(messages=[USER, ASSISTANT_TC, TOOL_OBS, second], tools=[])
        msgs = _msgs_of(req)
        assert len(msgs) == 3
        assert msgs[2]["role"] == "user"
        assert len(_parts_of(msgs[2])) == 2

    def test_tools_declaration_input_schema(self) -> None:
        """openai function schema → {name, description, input_schema}。"""
        tools: list[dict[str, object]] = [
            {
                "type": "function",
                "function": {
                    "name": "execute_sql",
                    "description": "跑 SQL",
                    "parameters": {
                        "type": "object",
                        "properties": {"sql": {"type": "string"}},
                        "required": ["sql"],
                    },
                },
            }
        ]
        req = anthropic_request(messages=[SYS, USER], tools=tools)
        assert req["tools"] == [
            {
                "name": "execute_sql",
                "description": "跑 SQL",
                "input_schema": {
                    "type": "object",
                    "properties": {"sql": {"type": "string"}},
                    "required": ["sql"],
                },
            }
        ]

    def test_vision_data_url_to_base64_source(self) -> None:
        """vision data-url → image.source(base64) 块。"""
        user_img = {
            "role": "user",
            "content": [
                {"type": "text", "text": "图里有什么"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,aGVsbG8="}},
            ],
        }
        req = anthropic_request(messages=[SYS, user_img], tools=[])
        content = _parts_of(_msgs_of(req)[0])
        assert content[0] == {"type": "text", "text": "图里有什么"}
        assert content[1] == {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": "aGVsbG8="},
        }


class TestAnthropicParse:
    """anthropic Message 应答 → ProtocolReply 回读。"""

    def test_text_and_tool_use_readback(self) -> None:
        raw = _Block(
            content=[
                _Block(type="text", text="查询结果 4 行"),
                _Block(type="tool_use", id="tu_1", name="execute_sql", input={"sql": "SELECT 1"}),
            ],
            usage=_Block(input_tokens=11, output_tokens=7),
        )
        reply = anthropic_parse(raw)
        assert isinstance(reply, ProtocolReply)
        assert reply.content == "查询结果 4 行"
        call = reply.tool_calls[0]
        assert (call.call_id, call.name, call.args) == ("tu_1", "execute_sql", {"sql": "SELECT 1"})
        assert call.raw_args == json.dumps({"sql": "SELECT 1"}, ensure_ascii=False)
        assert reply.usage == ProtocolUsage(prompt_tokens=11, completion_tokens=7, total_tokens=18)

    def test_non_dict_tool_input_kept_raw(self) -> None:
        """input 非 dict(畸形)→ args=None + raw_args 保原文(json 序列化回退)。"""
        raw = _Block(
            content=[_Block(type="tool_use", id="tu_2", name="x", input="not-a-dict")],
            usage=None,
        )
        reply = anthropic_parse(raw)
        assert reply.tool_calls[0].args is None
        assert reply.tool_calls[0].raw_args == '"not-a-dict"'

    def test_tool_only_reply_content_none(self) -> None:
        raw = _Block(
            content=[_Block(type="tool_use", id="tu_3", name="x", input={})],
            usage=_Block(input_tokens=1, output_tokens=0),
        )
        reply = anthropic_parse(raw)
        assert reply.content is None
        assert reply.usage is not None and reply.usage.total_tokens == 1

    def test_missing_tool_input_normalized_empty_args(self) -> None:
        """input 字段缺失 → args={}:与 openai 侧 arguments 缺失同义(空参执行),
        跨协议语义对称,不进自纠循环(区别于非 dict 畸形)。"""
        raw = _Block(
            content=[_Block(type="tool_use", id="tu_4", name="x", input=None)],
            usage=None,
        )
        reply = anthropic_parse(raw)
        call = reply.tool_calls[0]
        assert call.args == {}
        assert call.raw_args == ""


# ── responses(OpenAI Responses)翻译 ──


class TestResponsesRequest:
    """canonical → Responses 请求形态(instructions / input items / 扁平工具)。"""

    def test_instructions_and_flat_tools(self) -> None:
        tools: list[dict[str, object]] = [
            {
                "type": "function",
                "function": {
                    "name": "execute_sql",
                    "description": "跑 SQL",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]
        req = responses_request(messages=[SYS, USER], tools=tools, temperature=0.2)
        assert req["instructions"] == "你是 SQL 助手"  # system 置顶,不进 input
        assert req["input"] == [{"role": "user", "content": "有多少商品?"}]
        assert req["tools"] == [
            {
                "type": "function",
                "name": "execute_sql",
                "description": "跑 SQL",
                "parameters": {"type": "object", "properties": {}},
            }
        ]
        assert req["temperature"] == 0.2
        assert "max_output_tokens" not in req  # 未显式传不产出(供应商默认)

    def test_history_function_call_items(self) -> None:
        """assistant.tool_calls → function_call 项(arguments 原文回放);
        role:tool → function_call_output 项(output = 观察原文)。"""
        req = responses_request(messages=[USER, ASSISTANT_TC, TOOL_OBS], tools=[], temperature=0.1)
        items = _msgs_of(req, "input")
        assert items[1] == {
            "type": "function_call",
            "call_id": "call_1",
            "name": "execute_sql",
            "arguments": '{"sql": "SELECT 1"}',
        }
        assert items[2] == {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": '{"rows": 4}',
        }

    def test_assistant_text_item(self) -> None:
        msg = {"role": "assistant", "content": "上一轮结论"}
        req = responses_request(messages=[USER, msg], tools=[], temperature=0.1)
        assert _msgs_of(req, "input")[1] == {
            "role": "assistant",
            "content": [{"type": "output_text", "text": "上一轮结论"}],
        }

    def test_explicit_max_tokens_maps_max_output_tokens(self) -> None:
        req = responses_request(messages=[SYS, USER], tools=[], temperature=0.2, max_tokens=300)
        assert req["max_output_tokens"] == 300

    def test_vision_input_image(self) -> None:
        user_img = {
            "role": "user",
            "content": [
                {"type": "text", "text": "图里有什么"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,aGVsbG8="}},
            ],
        }
        req = responses_request(messages=[SYS, user_img], tools=[], temperature=0.2)
        parts = _parts_of(_msgs_of(req, "input")[0])
        assert parts[0] == {"type": "input_text", "text": "图里有什么"}
        assert parts[1] == {
            "type": "input_image",
            "image_url": "data:image/png;base64,aGVsbG8=",
        }

    def test_assistant_list_content_uses_output_text(self) -> None:
        """assistant 列表 content 部件须定型 output_text(user 保持 input_text,
        图片部件无 input/output 之分原样保留)——回放历史轮的可达路径。"""
        user_img = {
            "role": "user",
            "content": [
                {"type": "text", "text": "看图"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,aGVsbG8="}},
            ],
        }
        assistant_list = {"role": "assistant", "content": [{"type": "text", "text": "答案"}]}
        req = responses_request(messages=[assistant_list, user_img], tools=[], temperature=0.1)
        items = _msgs_of(req, "input")
        assert items[0] == {
            "role": "assistant",
            "content": [{"type": "output_text", "text": "答案"}],
        }
        assert items[1] == {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "看图"},
                {"type": "input_image", "image_url": "data:image/png;base64,aGVsbG8="},
            ],
        }


class TestResponsesParse:
    """Responses 应答 → ProtocolReply 回读。"""

    def test_message_and_function_call_readback(self) -> None:
        raw = _Block(
            output=[
                _Block(type="message", content=[_Block(type="output_text", text="答案 4")]),
                _Block(
                    type="function_call",
                    call_id="fc_1",
                    name="execute_sql",
                    arguments='{"sql": "SELECT 1"}',
                ),
            ],
            usage=_Block(input_tokens=11, output_tokens=7, total_tokens=18),
        )
        reply = responses_parse(raw)
        assert reply.content == "答案 4"
        call = reply.tool_calls[0]
        assert (call.call_id, call.name, call.args) == ("fc_1", "execute_sql", {"sql": "SELECT 1"})
        assert call.raw_args == '{"sql": "SELECT 1"}'
        assert reply.usage == ProtocolUsage(prompt_tokens=11, completion_tokens=7, total_tokens=18)

    def test_malformed_arguments_kept_raw(self) -> None:
        """arguments 畸形 → args=None + raw_args 保原文(喂回自纠口径)。"""
        raw = _Block(
            output=[_Block(type="function_call", call_id="fc_2", name="x", arguments="{oops")],
            usage=None,
        )
        reply = responses_parse(raw)
        assert reply.tool_calls[0].args is None
        assert reply.tool_calls[0].raw_args == "{oops"


# ── anthropic 官方 SDK 客户端封装(MockTransport 集成钉子) ──

_MSG_OK = {
    "id": "msg_1",
    "type": "message",
    "role": "assistant",
    "model": "test-model",
    "content": [
        {"type": "text", "text": "查询完成"},
        {"type": "tool_use", "id": "tu_1", "name": "execute_sql", "input": {"sql": "SELECT 1"}},
    ],
    "stop_reason": "tool_use",
    "usage": {"input_tokens": 11, "output_tokens": 7},
}


def _mk_anthropic_client(scenario: str, captured: list[httpx2.Request]) -> AnthropicClient:
    """MockTransport 假上游:scenario ∈ ok | server_error | bad_json。"""

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured.append(request)
        if scenario == "server_error":
            return httpx2.Response(500, text="upstream exploded")
        if scenario == "bad_json":
            return httpx2.Response(
                200, content=b"{not json", headers={"content-type": "application/json"}
            )
        return httpx2.Response(200, json=_MSG_OK)

    return AnthropicClient(
        base_url="http://test.local",
        api_key="sk-anthropic-test",
        timeout_sec=5.0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )


async def test_anthropic_client_dual_auth_and_url() -> None:
    """双头鉴权(x-api-key + Bearer)与 anthropic-version 头在场;
    URL 按 Claude Code 同构由 base_url 拼 /v1/messages;请求体为 Messages 形态。"""
    captured: list[httpx2.Request] = []
    client = _mk_anthropic_client("ok", captured)
    await client.create(
        model="test-model",
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
    )
    req = captured[0]
    assert req.url.path.endswith("/v1/messages")
    assert req.headers["x-api-key"] == "sk-anthropic-test"
    assert req.headers["authorization"] == "Bearer sk-anthropic-test"
    assert "anthropic-version" in req.headers
    body = json.loads(req.content)
    assert body["model"] == "test-model"
    assert body["max_tokens"] == DEFAULT_MAX_TOKENS  # 必填上限缺省 8192
    assert body["messages"] == [{"role": "user", "content": "hi"}]
    await client.close()


async def test_anthropic_client_200_readback() -> None:
    """200 正常回读:text/tool_use 块 → ProtocolReply,usage 归一求和。"""
    client = _mk_anthropic_client("ok", [])
    reply = await client.create(
        model="test-model",
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
    )
    assert reply.content == "查询完成"
    assert reply.tool_calls[0].args == {"sql": "SELECT 1"}
    assert reply.usage == ProtocolUsage(prompt_tokens=11, completion_tokens=7, total_tokens=18)
    await client.close()


async def test_anthropic_client_5xx_maps_llm_error() -> None:
    """非 2xx(APIStatusError 族)统一收敛 LlmError(错误映射外包给官方 SDK)。"""
    client = _mk_anthropic_client("server_error", [])
    with pytest.raises(LlmError):
        await client.create(
            model="test-model",
            messages=[{"role": "user", "content": "hi"}],
            tools=[],
        )
    await client.close()


async def test_anthropic_client_malformed_json_maps_llm_error() -> None:
    """200 但 body 畸形(JSON 解码失败)同样收敛 LlmError,不裸抛。"""
    client = _mk_anthropic_client("bad_json", [])
    with pytest.raises(LlmError):
        await client.create(
            model="test-model",
            messages=[{"role": "user", "content": "hi"}],
            tools=[],
        )
    await client.close()


# ── llm.py 三通道集成组(协议条目路由 + MockTransport 假上游) ──


def _env_settings(**env: str) -> Settings:
    """与 test_model_routing 同款:env 显式钉住,防本地 .env 渗入。"""
    import os

    old = {k: os.environ.get(f"KM_AGENT_{k.upper()}") for k in env}
    for k, v in env.items():
        os.environ[f"KM_AGENT_{k.upper()}"] = v
    try:
        return Settings()
    finally:
        for k, oldv in old.items():
            key = f"KM_AGENT_{k.upper()}"
            if oldv is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = oldv


class _Reporter:
    def __init__(self) -> None:
        self.records: list[dict[str, object]] = []

    async def __call__(self, records: list[dict[str, object]]) -> None:
        self.records.extend(records)


def _json_response(payload: dict[str, object]) -> "httpx2.Response":
    return httpx2.Response(200, json=payload)


_ANTHROPIC_TOOL_USE_MSG: dict[str, object] = {
    "id": "msg_t1",
    "type": "message",
    "role": "assistant",
    "model": "test-model",
    "content": [
        {
            "type": "tool_use",
            "id": "tu_1",
            "name": "execute_sql",
            "input": {"sql": "SELECT COUNT(*) FROM goods"},
        }
    ],
    "stop_reason": "tool_use",
    "usage": {"input_tokens": 12, "output_tokens": 8},
}
_ANTHROPIC_FINAL_MSG: dict[str, object] = {
    "id": "msg_t2",
    "type": "message",
    "role": "assistant",
    "model": "test-model",
    "content": [{"type": "text", "text": "共有 4 个商品"}],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 30, "output_tokens": 9},
}


async def test_anthropic_entry_react_round_trip() -> None:
    """anthropic 条目经 chat_with_tools 完成 ReAct 往返:首轮 tool_use 回读,
    二轮历史(assistant.tool_calls + tool 观察)翻译为 Messages 形态后收敛最终答案;
    两轮 usage 均按归一口径落 _report。"""
    captured: list[httpx2.Request] = []
    rounds = {"n": 0}

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured.append(request)
        rounds["n"] += 1
        return _json_response(_ANTHROPIC_TOOL_USE_MSG if rounds["n"] == 1 else _ANTHROPIC_FINAL_MSG)

    registry = json.dumps(
        [
            {
                "name": "glm-coding",
                "base_url": "http://test.local",
                "api_key": "sk-a",
                "model": "test-model",
                "protocol": "anthropic",
            }
        ]
    )
    settings = _env_settings(
        model_registry_json=registry,
        model_routes_json='{"REACT": "glm-coding"}',
        model_allowed_hosts="test.local",
        llm_api_key="sk-main",
        # 072:500/连接超时属瞬时故障,重试满预算;退避钉 1ms 防真等
        llm_retry_base_ms="1",
        llm_retry_max_wait_ms="1",
    )
    reporter = _Reporter()
    llm = LlmClient(settings, usage_reporter=reporter)
    llm._pool["glm-coding"] = (  # pyright: ignore[reportPrivateUsage]
        AnthropicClient(
            base_url="http://test.local",
            api_key="sk-a",
            timeout_sec=5.0,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
        ),
        "test-model",
        "anthropic",
    )
    tools: list[dict[str, object]] = [
        {
            "type": "function",
            "function": {
                "name": "execute_sql",
                "description": "跑 SQL",
                "parameters": {"type": "object", "properties": {"sql": {"type": "string"}}},
            },
        }
    ]
    # 轮 1:决策出工具调用
    reply1 = await llm.chat_with_tools(
        "你是 SQL 助手",
        [{"role": "user", "content": "有多少商品?"}],  # pyright: ignore[reportArgumentType]
        tools,
        task="REACT",
    )
    assert [c.name for c in reply1.tool_calls] == ["execute_sql"]
    assert reply1.tool_calls[0].args == {"sql": "SELECT COUNT(*) FROM goods"}
    # 轮 2:喂观察 → 收敛最终答案
    history: list[dict[str, object]] = [
        {"role": "user", "content": "有多少商品?"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "tu_1",
                    "type": "function",
                    "function": {
                        "name": "execute_sql",
                        "arguments": '{"sql": "SELECT COUNT(*) FROM goods"}',
                    },
                }
            ],
        },
        {"role": "tool", "tool_call_id": "tu_1", "content": '{"rows": 4}'},
    ]
    reply2 = await llm.chat_with_tools(
        "你是 SQL 助手",
        history,  # pyright: ignore[reportArgumentType]
        tools,
        task="REACT",
    )
    assert reply2.content == "共有 4 个商品"
    # 出站形态:两轮均为 Messages(system 置顶 / 工具声明 input_schema / 观察块)
    body1 = json.loads(captured[0].content)
    assert body1["system"] == "你是 SQL 助手"
    assert body1["tools"][0]["input_schema"]["properties"]["sql"]["type"] == "string"
    body2 = json.loads(captured[1].content)
    flat2 = json.dumps(body2, ensure_ascii=False)
    assert "tool_result" in flat2 and "tool_use" in flat2
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert len(reporter.records) == 2
    assert reporter.records[0]["prompt_tokens"] == 12  # 归一:input → prompt
    assert reporter.records[0]["total_tokens"] == 20
    await llm.aclose()


async def test_responses_entry_chat_route_hit() -> None:
    """responses 条目 chat 通道路由命中:出站走 Responses 形态(instructions),
    应答文本与归一 usage 与 openai 条目同构回传。"""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={
                "id": "resp_1",
                "object": "response",
                "created_at": 1,
                "status": "completed",
                "model": "test-model",
                "output": [
                    {
                        "type": "message",
                        "id": "m_1",
                        "status": "completed",
                        "role": "assistant",
                        "content": [
                            {"type": "output_text", "text": "答案:共 4 个", "annotations": []}
                        ],
                    }
                ],
                "usage": {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18},
            },
        )

    registry = json.dumps(
        [
            {
                "name": "qwen-rsp",
                "base_url": "http://test.local",
                "api_key": "sk-r",
                "model": "test-model",
                "protocol": "responses",
            }
        ]
    )
    settings = _env_settings(
        model_registry_json=registry,
        model_routes_json='{"SUMMARIZE": "qwen-rsp"}',
        model_allowed_hosts="test.local",
        llm_api_key="sk-main",
    )
    reporter = _Reporter()
    llm = LlmClient(settings, usage_reporter=reporter)
    llm._pool["qwen-rsp"] = (  # pyright: ignore[reportPrivateUsage]
        AsyncOpenAI(
            api_key="sk-r",
            base_url="http://test.local",
            timeout=5.0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),  # pyright: ignore[reportArgumentType]
        ),
        "test-model",
        "responses",
    )
    out = await llm.chat("你是 SQL 助手", "有多少商品?", task="SUMMARIZE", trace_id="tr-r")
    assert out == "答案:共 4 个"
    req = captured[0]
    assert "/responses" in req.url.path
    body = json.loads(req.content)
    assert body["instructions"] == "你是 SQL 助手"
    assert body["model"] == "test-model"
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert len(reporter.records) == 1
    assert reporter.records[0]["total_tokens"] == 18
    assert reporter.records[0]["status"] == "SUCCESS"
    await llm.aclose()


async def test_anthropic_entry_failure_reported_as_error() -> None:
    """失败语义(038 口径):anthropic 条目非 2xx → LlmError 且 _report 落
    status=ERROR、tokens=0、latency_ms 在场;超时同口径。"""
    scenario = {"mode": "server_error"}

    def handler(request: httpx2.Request) -> httpx2.Response:
        if scenario["mode"] == "timeout":
            raise httpx2.ConnectTimeout("timed out")
        return httpx2.Response(500, text="upstream exploded")

    registry = json.dumps(
        [
            {
                "name": "glm-coding",
                "base_url": "http://test.local",
                "api_key": "sk-a",
                "model": "test-model",
                "protocol": "anthropic",
            }
        ]
    )
    settings = _env_settings(
        model_registry_json=registry,
        model_routes_json='{"REACT": "glm-coding"}',
        model_allowed_hosts="test.local",
        llm_api_key="sk-main",
        # 072:500/连接超时属瞬时故障,重试满预算;退避钉 1ms 防真等
        llm_retry_base_ms="1",
        llm_retry_max_wait_ms="1",
    )
    reporter = _Reporter()
    llm = LlmClient(settings, usage_reporter=reporter)
    llm._pool["glm-coding"] = (  # pyright: ignore[reportPrivateUsage]
        AnthropicClient(
            base_url="http://test.local",
            api_key="sk-a",
            timeout_sec=5.0,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
        ),
        "test-model",
        "anthropic",
    )
    with pytest.raises(LlmError):
        await llm.chat("s", "u", task="REACT", trace_id="tr-fail")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    # 072:瞬时故障(500)重试满预算,每尝试一条 ERROR 记录
    assert len(reporter.records) == 3
    assert all(r["status"] == "ERROR" for r in reporter.records)
    rec = reporter.records[0]
    assert rec["status"] == "ERROR"
    assert rec["prompt_tokens"] == 0 and rec["total_tokens"] == 0
    assert isinstance(rec["latency_ms"], int) and rec["latency_ms"] >= 0
    assert rec["trace_id"] == "tr-fail"
    # 超时场景同口径
    scenario["mode"] = "timeout"
    reporter.records.clear()
    with pytest.raises(LlmError):
        await llm.chat("s", "u", task="REACT")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert reporter.records[0]["status"] == "ERROR"
    await llm.aclose()
