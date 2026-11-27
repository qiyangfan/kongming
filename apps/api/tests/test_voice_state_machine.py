"""通话状态机(voice-call「新提问取消上一轮」「结果回填与补交」「等待期固定应答」等)。

纯 asyncio 状态机,不碰 WebSocket:输入模型会话事件与问答终态,输出对模型、
浏览器、问答域的指令;用假管道断言指令序列。
"""

import json
from typing import Any, cast

from kongming.voice.models import CallSession
from kongming.voice.state_machine import (
    BrowserSend,
    CallStateMachine,
    Command,
    ModelSend,
    TurnCancel,
)

UID = "2d1a6d35-7d79-40a6-948c-642b0e3f8943"

SESSION_CONFIG_TOOL = "ask_kongming"
STOP_SESSION_TOOL = "stop_current_query"


def _session(**over: Any) -> CallSession:
    base: dict[str, Any] = {
        "call_id": "c1",
        "user_id": UID,
        "tenant_key": "default",
        "started_at": 0.0,
    }
    base.update(over)
    return CallSession(**base)


class _FakeBinding:
    """假关联字段签发口:取消由 TurnCancel 命令表达,不由 binding 执行。"""

    def __init__(self) -> None:
        self.tokens: list[str] = []

    def new_turn_token(self) -> str:
        token = f"vt{len(self.tokens)}"
        self.tokens.append(token)
        return token


def _cancel_ids(commands: list[Command]) -> list[str]:
    return [c.trace_id for c in commands if isinstance(c, TurnCancel)]


def _machine(binding: _FakeBinding, **kw: Any) -> CallStateMachine:
    return CallStateMachine(
        _session(),
        binding=binding,  # type: ignore[arg-type]
        tool_name=SESSION_CONFIG_TOOL,
        max_call_seconds=kw.pop("max_call_seconds", 600),
        **kw,
    )


def _tool_call(args: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "conversation.item.create",
        "item": {
            "type": "function_call",
            "name": SESSION_CONFIG_TOOL,
            "arguments": json.dumps(args, ensure_ascii=False),
        },
    }


def _stop_call(call_id: str = "stop-1") -> dict[str, Any]:
    return {
        "type": "conversation.item.create",
        "item": {
            "type": "function_call",
            "name": STOP_SESSION_TOOL,
            "call_id": call_id,
            "arguments": "{}",
        },
    }


def _types(commands: list[Command]) -> list[str]:
    return [c.kind for c in commands]


def test_tool_call_cancels_previous_and_dispatches_turn_request() -> None:
    """收到工具调用:取消在途轮 → 下发 turn.request(问题是听写原话)。"""
    binding = _FakeBinding()
    m = _machine(binding)
    m.attach_inflight(trace_id="t-old")
    commands = m.on_model_event(_tool_call({"question": "上个月销售额"}))
    assert _cancel_ids(commands) == ["t-old"]
    kinds = _types(commands)
    assert kinds[0] == TurnCancel.kind and kinds[1] == BrowserSend.kind
    send = cast("BrowserSend", commands[1])
    payload = send.payload
    assert payload["type"] == "turn.request"
    assert payload["question"] == "上个月销售额"  # 听写原话,不用模型改写
    assert payload["token"].startswith("vt")


def test_only_backchannel_does_nothing() -> None:
    """只有附和(被语义断句过滤)没有工具调用:在途轮不受影响。"""
    m = _machine(_FakeBinding())
    m.attach_inflight(trace_id="t1")
    assert (
        m.on_model_event(
            {"type": "conversation.item.input_transcription.completed", "transcript": "嗯"}
        )
        == []
    )
    assert m.on_model_event({"type": "input_audio_buffer.speech_started"}) == []


def test_waiting_self_answer_cancelled_on_first_non_tool_output() -> None:
    """等待期模型自行回应:response.created 先挂起,首个非工具输出项才取消;原调用失效。"""
    m = _machine(_FakeBinding())
    m.attach_inflight(trace_id="t1")
    assert m.on_model_event({"type": "response.created", "response": {"id": "r1"}}) == []
    assert m.original_call_invalid is False
    commands = m.on_model_event(
        {
            "type": "response.output_item.added",
            "item": {"type": "message", "role": "assistant", "content": []},
        }
    )
    assert m.original_call_invalid is True  # 原调用已失效
    assert _types(commands) == [ModelSend.kind]
    assert cast("ModelSend", commands[0]).payload["type"] == "response.cancel"
    assert m.session.interrupts == 0


def test_tool_call_during_wait_is_not_cancelled() -> None:
    """等待期模型调用提问工具:首个输出项是 function_call,不取消,按新问处理。"""
    m = _machine(_FakeBinding())
    m.attach_inflight(trace_id="t-old")
    assert m.on_model_event({"type": "response.created", "response": {"id": "r-tool"}}) == []
    assert (
        m.on_model_event(
            {
                "type": "response.output_item.added",
                "item": {
                    "type": "function_call",
                    "name": SESSION_CONFIG_TOOL,
                    "call_id": "fc-2",
                },
            }
        )
        == []
    )
    commands = m.on_model_event(
        {
            "type": "response.function_call_arguments.done",
            "name": SESSION_CONFIG_TOOL,
            "call_id": "fc-2",
            "arguments": json.dumps({"question": "新问题"}, ensure_ascii=False),
        }
    )
    assert _cancel_ids(commands) == ["t-old"]
    assert _types(commands) == [TurnCancel.kind, BrowserSend.kind]
    assert cast("BrowserSend", commands[1]).payload["type"] == "turn.request"


def test_speech_started_during_model_audio_cancels_and_counts_barge_in() -> None:
    """模型出声时用户开口:取消该回应并计一次打断;取消后的残余音频不再触发取消。"""
    m = _machine(_FakeBinding())
    assert m.on_model_event({"type": "response.created", "response": {"id": "r1"}}) == []
    assert m.on_model_event({"type": "response.audio.delta", "response_id": "r1"}) == []
    commands = m.on_model_event({"type": "input_audio_buffer.speech_started"})
    assert _types(commands) == [ModelSend.kind]
    assert cast("ModelSend", commands[0]).payload["type"] == "response.cancel"
    assert m.session.interrupts == 1
    # 取消与收尾之间的残余音频不得重新打开可打断标记
    assert m.on_model_event({"type": "response.audio.delta", "response_id": "r1"}) == []
    assert m.on_model_event({"type": "input_audio_buffer.speech_started"}) == []
    assert m.session.interrupts == 1
    assert m.on_model_event({"type": "response.done", "response": {"id": "r1"}}) == []
    # 新一轮回应出声后再开口:重新计数
    assert m.on_model_event({"type": "response.created", "response": {"id": "r2"}}) == []
    assert m.on_model_event({"type": "response.audio.delta", "response_id": "r2"}) == []
    assert m.on_model_event({"type": "input_audio_buffer.speech_started"}) != []
    assert m.session.interrupts == 2


def test_speech_started_without_model_audio_does_not_cancel() -> None:
    """模型未出声(工具调用轮/静默)时开口不取消,避免掐断工具调用。"""
    m = _machine(_FakeBinding())
    m.on_model_event({"type": "response.created", "response": {"id": "r-tool"}})
    assert m.on_model_event({"type": "input_audio_buffer.speech_started"}) == []
    assert m.session.interrupts == 0


def test_idle_self_answer_cancelled_on_first_non_tool_output() -> None:
    """空闲期模型自答:首个非工具输出项到达即 response.cancel,不计用户打断。"""
    m = _machine(_FakeBinding())
    assert m.on_model_event({"type": "response.created", "response": {"id": "r-idle"}}) == []
    commands = m.on_model_event(
        {
            "type": "response.output_item.added",
            "item": {"type": "message", "role": "assistant", "content": []},
        }
    )
    assert _types(commands) == [ModelSend.kind]
    assert cast("ModelSend", commands[0]).payload["type"] == "response.cancel"
    assert m.original_call_invalid is False
    assert m.session.interrupts == 0


def test_allowed_answer_plays_and_next_idle_answer_is_cancelled() -> None:
    """回填后的回应按放行播报;结束后空闲自答重新进入取消判定。"""
    m = _machine(_FakeBinding())
    m.pending_token = "vt-pending"
    m.on_turn_terminal({"verdict": "PASS", "summary": "1000"}, original_call_invalid=False)
    assert m.on_model_event({"type": "response.created", "response": {"id": "r-own"}}) == []
    assert (
        m.on_model_event(
            {
                "type": "response.output_item.added",
                "item": {"type": "message", "role": "assistant", "content": []},
            }
        )
        == []
    )
    assert m.on_model_event({"type": "response.audio.delta", "response_id": "r-own"}) == []
    assert m.on_model_event({"type": "response.done", "response": {"id": "r-own"}}) == []
    assert m.on_model_event({"type": "response.created", "response": {"id": "r-idle"}}) == []
    commands = m.on_model_event(
        {
            "type": "response.output_item.added",
            "item": {"type": "message", "role": "assistant", "content": []},
        }
    )
    assert _types(commands) == [ModelSend.kind]
    assert cast("ModelSend", commands[0]).payload["type"] == "response.cancel"


def test_terminal_backfill_via_tool_result_then_create() -> None:
    """轮结束且原调用有效:发工具结果 + response.create。"""
    m = _machine(_FakeBinding())
    m.pending_token = "vt-pending"
    commands = m.on_turn_terminal(
        {"verdict": "PASS", "summary": "上个月销售额 100 万元", "columns": ["月"], "rows": [["x"]]},
        original_call_invalid=False,
    )
    kinds = _types(commands)
    assert kinds == [ModelSend.kind, BrowserSend.kind, ModelSend.kind]
    first = cast("ModelSend", commands[0])
    assert first.payload["type"] == "conversation.item.create"  # 工具结果回填
    text = cast("dict[str, Any]", first.payload["item"])["output"]
    assert "100 万元" in cast("str", text)
    allow = cast("BrowserSend", commands[1])
    assert allow.payload["type"] == "playback.allow"
    third = cast("ModelSend", commands[2])
    assert third.payload["type"] == "response.create"


def test_terminal_with_table_mentions_screen() -> None:
    """多行结果时,念给模型的文本末尾提示数据已在屏幕上。"""
    m = _machine(_FakeBinding())
    m.pending_token = "vt-pending"
    commands = m.on_turn_terminal(
        {"verdict": "PASS", "summary": "共 3 行", "columns": ["a"], "rows": [["1"], ["2"]]},
        original_call_invalid=False,
    )
    assert (
        "屏幕" in cast("dict[str, Any]", cast("ModelSend", commands[0]).payload["item"])["output"]
    )


def test_scalar_result_does_not_mention_screen() -> None:
    """单值计数不是名单/明细,不得对模型说详细数据在屏幕上。"""
    m = _machine(_FakeBinding())
    m.pending_token = "vt-pending"
    commands = m.on_turn_terminal(
        {"verdict": "PASS", "summary": "共 200 位。", "columns": ["演员数量"], "rows": [[200]]},
        original_call_invalid=False,
    )
    output = cast("dict[str, Any]", cast("ModelSend", commands[0]).payload["item"])["output"]
    assert "屏幕" not in cast("str", output)


def test_terminal_backfill_as_text_when_call_invalid() -> None:
    """原调用已失效:以文字消息补交结果,不丢弃。"""
    m = _machine(_FakeBinding())
    m.pending_token = "vt-pending"
    commands = m.on_turn_terminal(
        {"verdict": "PASS", "summary": "答案 X"},
        original_call_invalid=True,
    )
    item = cast("dict[str, Any]", cast("ModelSend", commands[0]).payload["item"])
    assert item["role"] == "user"  # 文字消息补交(实测编号作废后的补交形态)
    assert "答案 X" in item["content"][0]["text"]


def test_failed_turn_backfills_not_found_text() -> None:
    """失败轮:回填「没有查到」类文案,让模型如实说明。"""
    m = _machine(_FakeBinding())
    m.pending_token = "vt-pending"
    commands = m.on_turn_terminal(
        {"verdict": "FAILED", "summary": "查询超时"}, original_call_invalid=False
    )
    assert (
        "没有查到"
        in cast("dict[str, Any]", cast("ModelSend", commands[0]).payload["item"])["output"]
    )


def test_cancelled_turn_not_backfilled() -> None:
    """被取消的轮:不回填。"""
    m = _machine(_FakeBinding())
    m.pending_token = "vt-pending"
    assert m.on_turn_terminal({"verdict": "CANCELLED"}, original_call_invalid=False) == []


def test_max_duration_ends_call() -> None:
    """时长到上限:下发结束通知、取消在途轮;审计由调用方按终态落。"""
    binding = _FakeBinding()
    m = _machine(binding, max_call_seconds=600)
    m.attach_inflight(trace_id="t9")
    m.tick(600.0)
    commands = m.on_expiration()
    assert _cancel_ids(commands) == ["t9"]
    assert _types(commands) == [BrowserSend.kind, TurnCancel.kind]
    assert cast("BrowserSend", commands[0]).payload["type"] == "call.end"


def test_channel_closed_ends_call() -> None:
    """通道断开:同结束语义(取消在途轮+结束通知)。"""
    binding = _FakeBinding()
    m = _machine(binding)
    m.attach_inflight(trace_id="t8")
    commands = m.on_channel_closed()
    assert _cancel_ids(commands) == ["t8"]
    assert _types(commands) == [BrowserSend.kind, TurnCancel.kind]


def test_usage_accumulates_and_audit_content() -> None:
    """用量累计(听入/念出)与审计内容:不含语音内容与密钥。"""
    m = _machine(_FakeBinding())
    m.on_model_event(
        {
            "type": "response.done",
            "response": {
                "usage": {
                    "input_token_details": {"audio": 100},
                    "output_token_details": {"audio": 40},
                }
            },
        }
    )
    assert m.session.audio_tokens_in == 100 and m.session.audio_tokens_out == 40
    assert m.session.rounds == 0 and m.session.interrupts == 0


def test_session_config_contains_behavior_and_semantic_vad() -> None:
    """下发给浏览器的会话配置:行为说明、固定音色、两个工具、按语义断句。"""
    config = CallStateMachine.build_session_config(model="qwen3.8-omni-flash-realtime")
    assert config["model"] == "qwen3.8-omni-flash-realtime"
    assert config["voice"] == "Serena"  # 固定应答录音按同一音色生成
    assert config["turn_detection"] == {
        "type": "semantic_vad",
        "threshold": 0.4,
        "silence_duration_ms": 2000,
    }
    tools = config["tools"]
    assert [tool["name"] for tool in tools] == [SESSION_CONFIG_TOOL, STOP_SESSION_TOOL]
    assert "交给" in config["instructions"]  # 行为说明:每句话都交给该工具
    assert "每一次语音输入" in config["instructions"]
    assert "绝不直接回答" in config["instructions"]
    assert "停止" in config["instructions"]
    assert "k-secret" not in json.dumps(config)  # 密钥不出后端,不在配置里下发


def test_malformed_tool_arguments_fall_back_to_transcript() -> None:
    """模型工具参数畸形时用听写原话补问,避免用户重复一遍。"""
    m = _machine(_FakeBinding())
    m.on_model_event(
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "transcript": "上个月销售额",
        }
    )
    commands = m.on_model_event(
        {
            "type": "response.function_call_arguments.done",
            "name": SESSION_CONFIG_TOOL,
            "call_id": "fc-bad",
            "arguments": "{bad-json",
        }
    )
    assert _types(commands) == [BrowserSend.kind]
    assert cast("BrowserSend", commands[0]).payload["question"] == "上个月销售额"


def test_new_tool_call_supersedes_old_turn() -> None:
    """新的工具调用取消旧轮;旧轮迟到结果不再回填。"""
    binding = _FakeBinding()
    m = _machine(binding)
    m.on_model_event(_tool_call({"question": "第一问"}))
    stale_token = cast("str", m.pending_token)
    m.on_model_event(_tool_call({"question": "第二问"}))
    assert m.pending_token != stale_token
    # 旧轮终态到达:不回填(以 token 判归属)
    assert (
        m.on_turn_terminal_for(
            stale_token, {"verdict": "PASS", "summary": "旧答案"}, original_call_invalid=False
        )
        == []
    )


def test_stop_tool_cancels_pending_without_turn_request() -> None:
    """停止工具:取消在途轮、回工具结果、下发固定确认;不发起新问答、不计轮数。"""
    binding = _FakeBinding()
    m = _machine(binding)
    m.attach_inflight(trace_id="t-stop")
    commands = m.on_model_event(_stop_call())
    assert _cancel_ids(commands) == ["t-stop"]
    assert _types(commands) == [TurnCancel.kind, ModelSend.kind, BrowserSend.kind]
    assert cast("ModelSend", commands[1]).payload["item"]["type"] == "function_call_output"
    assert cast("BrowserSend", commands[2]).payload["type"] == "turn.stopped"
    assert m.pending_token is None and m.pending_trace_id is None
    assert m.session.rounds == 0
    assert binding.tokens == []  # 停止不签发新关联字段


def test_stop_tool_without_pending_only_confirms() -> None:
    """无在途轮时停止:只回工具结果并播确认,不产生问答副作用。"""
    m = _machine(_FakeBinding())
    commands = m.on_model_event(_stop_call("stop-2"))
    assert _cancel_ids(commands) == []
    assert _types(commands) == [ModelSend.kind, BrowserSend.kind]
    assert cast("BrowserSend", commands[1]).payload["type"] == "turn.stopped"
    assert m.session.rounds == 0


def test_stop_tool_during_wait_not_cancelled_as_self_answer() -> None:
    """等待期停止工具:首个输出项是 function_call,不按自答取消,正常取消在途轮。"""
    m = _machine(_FakeBinding())
    m.attach_inflight(trace_id="t-old")
    assert m.on_model_event({"type": "response.created", "response": {"id": "r-stop"}}) == []
    assert (
        m.on_model_event(
            {
                "type": "response.output_item.added",
                "item": {
                    "type": "function_call",
                    "name": STOP_SESSION_TOOL,
                    "call_id": "fc-stop",
                },
            }
        )
        == []
    )
    commands = m.on_model_event(
        {
            "type": "response.function_call_arguments.done",
            "name": STOP_SESSION_TOOL,
            "call_id": "fc-stop",
            "arguments": "{}",
        }
    )
    assert _cancel_ids(commands) == ["t-old"]
    assert _types(commands) == [TurnCancel.kind, ModelSend.kind, BrowserSend.kind]
    assert [c.payload["type"] for c in commands if isinstance(c, ModelSend)] == [
        "conversation.item.create"
    ]
    assert cast("BrowserSend", commands[2]).payload["type"] == "turn.stopped"
    assert m.pending_token is None and m.pending_trace_id is None
    assert m.original_call_invalid is False


def test_on_channel_open_and_session_created_emit_session_update() -> None:
    """通道打开与 session.created 下发 session.update;收到 session.updated 后不再重复。"""
    m = CallStateMachine(
        _session(),
        binding=_FakeBinding(),  # type: ignore[arg-type]
        tool_name=SESSION_CONFIG_TOOL,
        max_call_seconds=600,
        model="qwen3.8-omni-flash-realtime",
    )
    opened = m.on_channel_open()
    assert len(opened) == 1 and isinstance(opened[0], ModelSend)
    assert opened[0].payload["type"] == "session.update"
    assert opened[0].payload["session"]["model"] == "qwen3.8-omni-flash-realtime"
    assert opened[0].payload["session"]["turn_detection"] == {
        "type": "semantic_vad",
        "threshold": 0.4,
        "silence_duration_ms": 2000,
    }
    assert opened[0].payload["session"]["voice"] == "Serena"

    # 未确认前收到 session.created 再补发一次;确认后再收到不再发送
    created_cmds = m.on_model_event({"type": "session.created"})
    assert len(created_cmds) == 1 and isinstance(created_cmds[0], ModelSend)
    assert created_cmds[0].payload["type"] == "session.update"
    assert m.on_model_event({"type": "session.updated"}) == []
    assert m.on_model_event({"type": "session.created"}) == []


def test_own_response_not_cancelled_when_intermediate_event_arrives() -> None:
    """我方 response.create 后即便先到其他事件,后续 response.created 也不应被误掐断。"""
    m = _machine(_FakeBinding())
    m.pending_token = "vt-pending"
    m.on_turn_terminal({"verdict": "PASS", "summary": "共 5 条"}, original_call_invalid=False)
    assert m.on_model_event({"type": "conversation.item.created"}) == []
    assert m.on_model_event({"type": "response.created", "response": {"id": "r-own"}}) == []
    assert m.original_call_invalid is False
    assert m.session.interrupts == 0


def test_response_created_without_inflight_turn_not_cancelled() -> None:
    """无在途轮时模型发起 response.created(准备调 ask_kongming),不得误取消。"""
    m = _machine(_FakeBinding())
    assert m.on_model_event({"type": "response.created", "response": {"id": "r-tool"}}) == []
    assert m.session.interrupts == 0


def test_realtime_output_item_done_and_arguments_done_dedup_and_call_id() -> None:
    """Realtime 工具事件(arguments.done/output_item.done)去重、传 call_id 并重置失效标记。"""
    m = _machine(_FakeBinding())
    m.original_call_invalid = True  # 上一轮遗留的失效标记须在新轮开始时重置
    assert (
        m.on_model_event(
            {
                "type": "response.output_item.added",
                "item": {
                    "type": "function_call",
                    "name": SESSION_CONFIG_TOOL,
                    "call_id": "call_99",
                },
            }
        )
        == []
    )
    cmds1 = m.on_model_event(
        {
            "type": "response.function_call_arguments.done",
            "call_id": "call_99",
            "arguments": json.dumps({"question": "本月收入"}, ensure_ascii=False),
        }
    )
    assert len(cmds1) == 1 and isinstance(cmds1[0], BrowserSend)
    assert cmds1[0].payload["question"] == "本月收入"
    assert m.original_call_invalid is False
    # 紧随其后的 output_item.done 带同一 call_id 须去重
    assert (
        m.on_model_event(
            {
                "type": "response.output_item.done",
                "item": {
                    "type": "function_call",
                    "name": SESSION_CONFIG_TOOL,
                    "call_id": "call_99",
                    "arguments": json.dumps({"question": "本月收入"}, ensure_ascii=False),
                },
            }
        )
        == []
    )
    # 终态回填须带回原 call_id
    backfill = m.on_turn_terminal(
        {"verdict": "PASS", "summary": "100 万"}, original_call_invalid=False
    )
    item = cast("dict[str, Any]", cast("ModelSend", backfill[0]).payload["item"])
    assert item["call_id"] == "call_99"


def test_pending_trace_id_cleared_after_turn_terminal() -> None:
    """终态回填后须清理 pending_trace_id,避免后续回应被误判在途或收口误取消。"""
    m = _machine(_FakeBinding())
    m.on_model_event(_tool_call({"question": "第一问"}))
    m.attach_inflight(trace_id="t-finished")
    m.on_turn_terminal({"verdict": "PASS", "summary": "已查到"}, original_call_invalid=False)
    # 模拟我方回应正常开始后,下一轮模型自行启动回应(无在途轮)
    assert m.on_model_event({"type": "response.created"}) == []
    assert m.on_model_event({"type": "response.created"}) == []
    assert _cancel_ids(m.on_channel_closed()) == []
