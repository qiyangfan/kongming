"""通话状态机:纯 asyncio/同步逻辑,不碰 WebSocket(测试用假管道)。

输入模型会话事件与问答终态,输出指令序列(ModelSend/BrowserSend/TurnCancel);
协议由后端驱动,浏览器只搬运(design D3)。全部决策与记录都在后端。
"""

import json
import logging
from dataclasses import dataclass
from typing import Any, Protocol, cast

from kongming.voice.models import CallSession

log = logging.getLogger(__name__)

# 与实时模型音色一致:固定应答录音按该音色生成;改这里须重录录音
VOICE_NAME = "Serena"
# 语义断句的静默下限:取数问题常带句中停顿,官方默认 800ms 偏急;2s 兼顾
# 自然停顿与等待感(取值须在官方 [200, 6000] 内)
END_OF_TURN_SILENCE_MS = 2000
# 短促的“等一下/停”也应有较高概率进入 speech_started;0.5 对短句偏保守。
END_OF_TURN_THRESHOLD = 0.4

# 两个固定工具:提问交给问答链路;停止只取消在途轮,不发起新问题
ASK_TOOL_NAME = "ask_kongming"
STOP_TOOL_NAME = "stop_current_query"
STOP_OUTPUT_TEXT = "已停止"
# 除工具调用外的文本增量即自答信号;首个此类事件到达时取消未放行的回应
_IDLE_CANCEL_TEXT_EVENTS = {
    "response.output_text.delta",
    "response.audio_transcript.delta",
    "response.output_audio_transcript.delta",
}

_PLAYBACK_AUDIO_EVENTS = {
    "response.audio.delta",
    "response.output_audio.delta",
    "output_audio_buffer.started",
}
_PLAYBACK_END_EVENTS = {
    "response.done",
    "output_audio_buffer.stopped",
    "output_audio_buffer.cleared",
}
_TRANSCRIPT_EVENTS = {
    "conversation.item.input_audio_transcription.completed",
    "conversation.item.input_audio_transcription.delta",
}


def _response_id(event: dict[str, Any]) -> str | None:
    """取事件所属回应 id(response.created 嵌在 response.id,增量事件用 response_id)。"""
    response = event.get("response")
    if isinstance(response, dict):
        rid = cast("dict[str, Any]", response).get("id")
        if isinstance(rid, str) and rid:
            return rid
    rid = event.get("response_id")
    return rid if isinstance(rid, str) and rid else None


@dataclass
class ModelSend:
    """发给模型会话的指令(浏览器原样搬运)。"""

    payload: dict[str, Any]
    kind = "model_send"


@dataclass
class BrowserSend:
    """发给浏览器的指令(turn.request/call.end 等通道消息)。"""

    payload: dict[str, Any]
    kind = "browser_send"


@dataclass
class TurnCancel:
    """取消一轮在途问答的指令(经问答域端口执行)。"""

    trace_id: str
    kind = "turn_cancel"


Command = ModelSend | BrowserSend | TurnCancel


class TurnBinding(Protocol):
    """在途轮关联字段的签发口(取消经 TurnCancel 命令由适配层执行)。"""

    def new_turn_token(self) -> str: ...


class CallStateMachine:
    """一通通话的协议状态机:见 D3 表(工具调用/擅自回应/回填与补交/结束)。"""

    def __init__(
        self,
        session: CallSession,
        *,
        binding: TurnBinding,
        tool_name: str,
        max_call_seconds: int,
        model: str = "",
    ) -> None:
        self.session = session
        self._binding = binding
        self._tool_name = tool_name
        self._max_seconds = max_call_seconds
        self._model = model
        self.pending_token: str | None = None  # 在途问答轮的关联字段
        self.pending_trace_id: str | None = None
        self.original_call_invalid = False  # 模型擅自回应导致原查询调用编号作废
        self._waiting_own_response = False  # 我方 response.create 后的回应归属标记
        self._session_configured = False
        self._pending_call_id = ""
        self._last_transcript = ""  # 模型听写文本:工具参数畸形时作为问题兜底
        self._tool_call_names: dict[str, str] = {}  # call_id → 工具名(参数事件可能不带 name)
        self._seen_call_ids: set[str] = set()
        self._current_response_id: str | None = None  # 正在进行的回应(插话取消定位)
        self._cancelled_response_ids: set[str] = set()  # 已发取消的回应(忽略其后续音频)
        self._response_has_audio = False  # 当前回应是否已出声(插话取消依据)
        self._response_watched = False  # 当前回应尚未判定工具/自答
        self._watching_response_id: str | None = None
        self._playback_allowed = False  # 我方回应已放行播报(直到该回应结束)
        self._allowed_response_id: str | None = None
        # 轮数/打断/用量直接累加在 session 上(唯一计数承载处,审计据此取值)

    # ---- 生命周期 ----

    def on_channel_open(self) -> list[Command]:
        """通道建立后下发模型会话配置(行为说明/两个工具/语义断句)。"""
        if not self._model:
            return []
        return [
            ModelSend(
                {
                    "type": "session.update",
                    "session": self.build_session_config(model=self._model),
                }
            )
        ]

    def attach_inflight(self, *, trace_id: str) -> None:
        """登记在途轮标识(供取消与过期收口)。"""
        self.pending_trace_id = trace_id

    def tick(self, now_monotonic: float) -> bool:
        """墙钟检查:到时长上限返回 True(调用方触发 on_expiration)。"""
        return now_monotonic - self.session.started_at >= self._max_seconds

    def on_expiration(self) -> list[Command]:
        """时长到上限:取消在途轮 + 下发结束通知。"""
        return self._end("max_duration")

    def on_channel_closed(self) -> list[Command]:
        """通道断开:取消在途轮 + 下发结束通知(尽力送达)。"""
        return self._end("channel_closed")

    def _end(self, reason: str) -> list[Command]:
        commands: list[Command] = [BrowserSend({"type": "call.end", "reason": reason})]
        if self.pending_trace_id:
            commands.append(TurnCancel(self.pending_trace_id))
        log.info(
            "通话结束 call_id=%s reason=%s rounds=%s",
            self.session.call_id,
            reason,
            self.session.rounds,
        )
        return commands

    # ---- 模型事件 ----

    def on_model_event(self, event: dict[str, Any]) -> list[Command]:
        """模型会话事件原样到达;输出指令序列(协议由后端驱动)。"""
        etype = event.get("type")
        if etype == "session.created":
            commands = (
                self.on_channel_open() if self._model and not self._session_configured else []
            )
        elif etype == "session.updated":
            self._session_configured = True
            commands = []
        elif etype == "input_audio_buffer.speech_started":
            commands = self._on_speech_started()
        elif etype == "response.created":
            self._current_response_id = _response_id(event)
            self._response_has_audio = False
            commands = self._on_response_created()
        elif etype == "response.done":
            self._finish_current_response(_response_id(event))
            self._accumulate_usage(event)
            commands = []
        elif etype in _PLAYBACK_END_EVENTS:
            self._finish_current_response(_response_id(event))
            commands = []
        elif etype == "response.output_item.added":
            commands = self._on_output_item_added(event)
        elif etype in _IDLE_CANCEL_TEXT_EVENTS:
            commands = self._cancel_watched_response()
        elif etype in _PLAYBACK_AUDIO_EVENTS:
            self._note_audio(_response_id(event))
            commands = []
        else:
            commands = self._maybe_tool_call(event, etype=etype)
        return commands

    def _on_speech_started(self) -> list[Command]:
        """用户开口即打断:模型正在出声时取消该回应;未出声不动作(附和/噪声不误伤工具调用)。"""
        self._last_transcript = ""
        if not self._response_has_audio:
            return []
        self._response_has_audio = False
        self._response_watched = False
        self._watching_response_id = None
        self._playback_allowed = False
        self._allowed_response_id = None
        if self._current_response_id is not None:
            self._cancelled_response_ids.add(self._current_response_id)
        self.session.interrupts += 1
        return [ModelSend({"type": "response.cancel"})]

    def _note_audio(self, response_id: str | None) -> None:
        """记下回应已出声;已被插话取消的回应,后续音频增量不再视为可打断。"""
        if response_id is not None and response_id in self._cancelled_response_ids:
            return
        self._response_has_audio = True

    def _finish_current_response(self, response_id: str | None) -> None:
        """回应收尾:清出声/观察/放行标记;按 response_id 清当前回应与已取消标记。"""
        self._response_has_audio = False
        if response_id is None or response_id == self._current_response_id:
            self._current_response_id = None
        if response_id is None or response_id == self._watching_response_id:
            self._response_watched = False
            self._watching_response_id = None
        if response_id is None or response_id == self._allowed_response_id:
            self._playback_allowed = False
            self._allowed_response_id = None
        if response_id is None:
            self._cancelled_response_ids.clear()
        else:
            self._cancelled_response_ids.discard(response_id)

    def _on_response_created(self) -> list[Command]:
        """回应开始先挂起判定:我方回应放行,工具调用/自答在首个输出项再分流。"""
        self._response_watched = True
        self._watching_response_id = self._current_response_id
        if self._waiting_own_response:
            self._waiting_own_response = False
            self._response_watched = False
            if self._playback_allowed and self._current_response_id is not None:
                self._allowed_response_id = self._current_response_id
        return []

    def _on_output_item_added(self, event: dict[str, Any]) -> list[Command]:
        """首个输出项判定:已知工具调用放行;其余输出(自答)取消。"""
        item = cast("dict[str, Any]", event.get("item") or {})
        name = str(item.get("name") or "")
        if item.get("type") == "function_call" and name in {
            self._tool_name,
            STOP_TOOL_NAME,
        }:
            call_id = str(item.get("call_id") or "")
            if call_id:
                self._tool_call_names[call_id] = name
            self._response_watched = False
            self._watching_response_id = None
            return []
        return self._cancel_watched_response()

    def _cancel_watched_response(self) -> list[Command]:
        """取消尚未判定为工具调用的回应;有在途轮时标记原调用失效(结果改文字补交)。"""
        if not self._response_watched:
            return []
        self._response_watched = False
        self._response_has_audio = False
        rid = self._watching_response_id or self._current_response_id
        self._watching_response_id = None
        if rid is not None:
            self._cancelled_response_ids.add(rid)
        if self.pending_token is not None or self.pending_trace_id is not None:
            self.original_call_invalid = True
        return [ModelSend({"type": "response.cancel"})]

    def _accumulate_usage(self, event: dict[str, Any]) -> None:
        """累计模型报告的音频用量(兼容 audio 与 audio_tokens 字段)。"""
        response = cast("dict[str, Any]", event.get("response") or {})
        usage = cast("dict[str, Any]", response.get("usage") or {})
        details_in = cast("dict[str, Any]", usage.get("input_token_details") or {})
        details_out = cast("dict[str, Any]", usage.get("output_token_details") or {})
        self.session.audio_tokens_in += int(
            details_in.get("audio") or details_in.get("audio_tokens") or 0
        )
        self.session.audio_tokens_out += int(
            details_out.get("audio") or details_out.get("audio_tokens") or 0
        )

    def _remember_transcript(self, event: dict[str, Any], *, delta: bool) -> None:
        """缓存模型听写文本;工具参数缺失/畸形时用原话补问。"""
        value = event.get("delta") if delta else event.get("transcript")
        text = str(value or "")
        if not text:
            return
        self._last_transcript = (self._last_transcript + text if delta else text).strip()

    def _maybe_tool_call(self, event: dict[str, Any], *, etype: object) -> list[Command]:
        """识别听写、ask/stop 工具调用(兼容 item.create/output_item.done/arguments.done)。"""
        if etype in _TRANSCRIPT_EVENTS:
            self._remember_transcript(event, delta=str(etype).endswith(".delta"))
            return []
        if etype in ("conversation.item.create", "response.output_item.done"):
            item = cast("dict[str, Any]", event.get("item") or {})
            name = str(item.get("name") or "")
            if item.get("type") == "function_call" and name in {
                self._tool_name,
                STOP_TOOL_NAME,
            }:
                return self._dispatch_tool_call(
                    tool_name=name,
                    raw_args=item.get("arguments"),
                    call_id=str(item.get("call_id") or ""),
                )
            return []
        if etype == "response.function_call_arguments.done":
            call_id = str(event.get("call_id") or "")
            name = str(event.get("name") or self._tool_call_names.get(call_id) or "")
            if name in {self._tool_name, STOP_TOOL_NAME}:
                return self._dispatch_tool_call(
                    tool_name=name,
                    raw_args=event.get("arguments"),
                    call_id=call_id,
                )
        return []

    def _dispatch_tool_call(
        self, *, tool_name: str, raw_args: object, call_id: str
    ) -> list[Command]:
        """按 call_id 去重后分发;停止工具不解析参数,提问工具要求 question 原文。"""
        if call_id and call_id in self._seen_call_ids:
            return []
        self._response_watched = False
        self._watching_response_id = None
        if tool_name == STOP_TOOL_NAME:
            if call_id:
                self._seen_call_ids.add(call_id)
            return self._on_stop_call(call_id=call_id)
        if isinstance(raw_args, dict):
            arguments = cast("dict[str, Any]", raw_args)
        elif isinstance(raw_args, str):
            try:
                parsed = json.loads(raw_args)
            except ValueError:
                return self._fallback_transcript_question(call_id)
            if not isinstance(parsed, dict):
                return self._fallback_transcript_question(call_id)
            arguments = cast("dict[str, Any]", parsed)
        else:
            return self._fallback_transcript_question(call_id)
        if not str(arguments.get("question") or "").strip():
            return self._fallback_transcript_question(call_id)
        if call_id:
            self._seen_call_ids.add(call_id)
        return self._on_ask_call(arguments, call_id=call_id)

    def _fallback_transcript_question(self, call_id: str) -> list[Command]:
        """工具参数不可用时用模型听写原话补问;无听写则保持原拒绝行为。"""
        question = self._last_transcript.strip()
        if not question:
            log.warning("语音工具参数解析失败且无听写兜底 call_id=%s", call_id)
            return []
        if call_id:
            self._seen_call_ids.add(call_id)
        log.info("语音工具参数不可用,回退听写原话 call_id=%s", call_id)
        return self._on_ask_call({"question": question}, call_id=call_id)

    def _on_ask_call(self, arguments: dict[str, Any], *, call_id: str = "") -> list[Command]:
        """新提问:取消旧轮 → 签关联字段 → 下发 turn.request(问题=听写原话)。"""
        self._last_transcript = ""
        commands: list[Command] = []
        if self.pending_trace_id:
            commands.append(TurnCancel(self.pending_trace_id))
        token = self._binding.new_turn_token()
        self.pending_token = token
        self.pending_trace_id = None  # 新轮尚未发起;发起后由问答侧绑定回填
        self.original_call_invalid = False
        self._pending_call_id = call_id
        self._waiting_own_response = False
        self._playback_allowed = False
        self._allowed_response_id = None
        self.session.rounds += 1
        commands.append(
            BrowserSend(
                {
                    "type": "turn.request",
                    "token": token,
                    "question": str(arguments.get("question") or ""),  # 听写原话
                }
            )
        )
        return commands

    def _on_stop_call(self, *, call_id: str = "") -> list[Command]:
        """停止意图:取消在途轮 → 回工具结果(不触发回应) → 通知浏览器播固定确认。"""
        commands: list[Command] = []
        if self.pending_trace_id:
            commands.append(TurnCancel(self.pending_trace_id))
        if call_id:
            commands.append(
                ModelSend(
                    {
                        "type": "conversation.item.create",
                        "item": {
                            "type": "function_call_output",
                            "call_id": call_id,
                            "output": json.dumps({"text": STOP_OUTPUT_TEXT}, ensure_ascii=False),
                        },
                    }
                )
            )
        commands.append(BrowserSend({"type": "turn.stopped"}))
        self.pending_token = None
        self.pending_trace_id = None
        self._pending_call_id = ""
        self._waiting_own_response = False
        self._playback_allowed = False
        self._allowed_response_id = None
        self.original_call_invalid = False
        log.info("停止当前查询 call_id=%s", self.session.call_id)
        return commands

    # ---- 问答终态 ----

    def on_turn_terminal(
        self, outcome: dict[str, Any], *, original_call_invalid: bool
    ) -> list[Command]:
        """终态回填:按原调用是否有效选工具结果或文字补交;被取消轮不回填。"""
        if self.pending_token is None:
            return []
        token = self.pending_token
        return self.on_turn_terminal_for(
            token, outcome, original_call_invalid=original_call_invalid
        )

    def on_turn_terminal_for(
        self, token: str, outcome: dict[str, Any], *, original_call_invalid: bool
    ) -> list[Command]:
        """按关联字段判归属的终态回填(旧轮迟到结果不回填)。"""
        if token != self.pending_token:
            return []  # 已被新一轮顶替:旧轮结果不交给模型
        verdict = outcome.get("verdict")
        if verdict == "CANCELLED":
            self.pending_token = None
            self.pending_trace_id = None
            self._waiting_own_response = False
            self._playback_allowed = False
            self._allowed_response_id = None
            return []
        text = self._compose_text(outcome)
        if original_call_invalid:
            commands: list[Command] = [
                ModelSend(
                    {
                        "type": "conversation.item.create",
                        "item": {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": text}],
                        },
                    }
                )
            ]
        else:
            commands = [
                ModelSend(
                    {
                        "type": "conversation.item.create",
                        "item": {
                            "type": "function_call_output",
                            "call_id": self._pending_call_id,
                            "output": json.dumps({"text": text}, ensure_ascii=False),
                        },
                    }
                )
            ]
        # 先放行再触发回应:浏览器以放行消息为唯一解除静音判据(残余音频仍按回应 id 压住)
        commands.append(BrowserSend({"type": "playback.allow"}))
        commands.append(ModelSend({"type": "response.create"}))
        self._waiting_own_response = True
        self._playback_allowed = True
        self._allowed_response_id = None
        self._response_watched = False
        self._watching_response_id = None
        self.pending_token = None
        self.pending_trace_id = None
        return commands

    def _compose_text(self, outcome: dict[str, Any]) -> str:
        """交给模型念的文本:失败如实说明;有结果表时提示数据已在屏幕上。"""
        if outcome.get("verdict") == "FAILED":
            return "这次查询没有查到结果,请如实告诉用户查询失败了,不要编造数字。"
        summary = str(outcome.get("summary") or "")
        rows = outcome.get("rows")
        # 单行结果(尤其单值计数)不是“名单/明细”;只在多行结果时提示页面有数据。
        if isinstance(rows, list) and len(cast("list[object]", rows)) > 1:
            summary += " 详细数据已显示在屏幕上。"
        return summary

    # ---- 会话配置 ----

    @staticmethod
    def build_session_config(*, model: str) -> dict[str, Any]:
        """下发给浏览器的模型会话配置:行为说明、两个工具、按语义断句(D4)。

        牵线交换由后端完成,模型服务密钥不出后端;本配置是浏览器经通道转发
        给模型会话的 session.update 载荷。
        """
        return {
            "model": model,
            "voice": VOICE_NAME,
            "instructions": (
                "你是语音助手:用户每一次语音输入都要立即调用 ask_kongming 工具,把用户的话"
                "原样交给助手,自己绝不直接回答任何内容;用户明确要求停止当前查询时,调用"
                " stop_current_query;调用工具时不出声;只有系统给出的查询结果或失败说明才能"
                "念出,数字与原文一致,不要编造。"
            ),
            "turn_detection": {
                "type": "semantic_vad",
                "threshold": END_OF_TURN_THRESHOLD,
                "silence_duration_ms": END_OF_TURN_SILENCE_MS,
            },
            "tools": [
                {
                    "type": "function",
                    "name": ASK_TOOL_NAME,
                    "description": "把用户的问题原样交给助手查询",
                    "parameters": {
                        "type": "object",
                        "properties": {"question": {"type": "string"}},
                        "required": ["question"],
                    },
                },
                {
                    "type": "function",
                    "name": STOP_TOOL_NAME,
                    "description": "用户明确要求停止当前查询时调用",
                    "parameters": {"type": "object", "properties": {}},
                },
            ],
        }
