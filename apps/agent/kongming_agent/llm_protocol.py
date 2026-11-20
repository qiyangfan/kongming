"""在 OpenAI、Anthropic 和 Responses 协议间转换请求与响应，统一为内部消息格式。"""

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, cast

from anthropic import APIError, AsyncAnthropic
from openai import APIError as OpenAIAPIError
from openai import AsyncOpenAI

log = logging.getLogger(__name__)

# 协议常量(取值域单源在 settings.VALID_PROTOCOLS;此处语义化别名供分支判定)
PROTOCOL_OPENAI = "openai"
PROTOCOL_ANTHROPIC = "anthropic"
PROTOCOL_RESPONSES = "responses"

# anthropic Messages 必填输出上限的缺省值(max_tokens 未显式传时;SQL 生成 /
# ReAct 决策的输出量级充分,文档化行为——设计决策 3)
DEFAULT_MAX_TOKENS = 8192


def _attr_str(obj: object, name: str) -> str:
    """SDK 应答对象属性读取(str 归一;缺失/None 记空串)。
    getattr 的 Any 传播被封装在本层,下游保持具体型(pyright strict)。"""
    value = getattr(obj, name, None)
    return str(value) if value is not None else ""


def _attr(obj: object, name: str) -> object:
    """SDK 应答对象属性读取(原始 object;缺失记 None)。"""
    value = getattr(obj, name, None)
    if value is None:
        return None
    return value


def _usage_count(obj: object, name: str) -> int:
    """usage 计数字段读取:缺失/None/畸形一律记 0(既有用量采集语义)。"""
    value = getattr(obj, name, None)
    try:
        return int(value) if value is not None else 0
    except (TypeError, ValueError):
        return 0


@dataclass(frozen=True)
class ProtocolUsage:
    """归一用量:字段名与既有 _report 的 duck-type 口径(prompt/completion/total)对齐。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class LlmError(Exception):
    """LLM 调用失败(key 缺失/网络/超时/协议异常)的统一出口。
    协议层错误映射(anthropic/responses)在本层收敛该型,llm.py 显式
    re-export 保持既有导入面不变。"""


@dataclass(frozen=True)
class LlmToolCall:
    """LLM 产出的单次工具调用(canonical;args 解析失败时 args=None + raw_args 原文,
    由 ReAct 节点收敛为「参数畸形」观察喂回自纠)。归属协议层,
    避免 llm.py ↔ llm_protocol 循环导入。"""

    call_id: str
    name: str
    args: dict[str, object] | None
    raw_args: str = ""


@dataclass(frozen=True)
class ProtocolReply:
    """协议应答归一形态(anthropic / responses 解析产物):三通道共用;
    content 与 tool_calls 至多一个有语义(与 openai 通道 ToolChatReply 同判据)。"""

    content: str | None = None
    tool_calls: list[LlmToolCall] = field(default_factory=list[LlmToolCall])
    usage: ProtocolUsage | None = None


def _json_object_or_none(raw: str) -> dict[str, object] | None:
    """arguments JSON 文本 → dict;空串视空参 {};标量/数组/畸形 → None(喂回自纠)。"""
    try:
        parsed: object = json.loads(raw) if raw.strip() else {}
        if not isinstance(parsed, dict):
            return None
        return cast("dict[str, object]", parsed)
    except json.JSONDecodeError:
        return None


# ── anthropic Messages 翻译(canonical → 请求 kwargs / 应答 → ProtocolReply) ──


class AnthropicClient:
    """封装 Anthropic SDK 调用:请求鉴权与错误转换,APIError 及响应校验错误
    统一转换为 LlmError。"""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout_sec: float,
        http_client: Any = None,  # 测试注入 httpx.MockTransport 用(生产恒 None)
    ) -> None:
        self._sdk = AsyncAnthropic(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout_sec,
            default_headers={"Authorization": f"Bearer {api_key}"},
            http_client=http_client,
        )

    async def create(
        self,
        *,
        model: str,
        messages: Sequence[Mapping[str, object]],
        tools: list[dict[str, object]],
        max_tokens: int | None = None,
    ) -> ProtocolReply:
        """翻译 → Messages 调用 → 应答解析;失败语义统一 LlmError。
        不发送 temperature:官方 SDK 1.8.0 起类型面已移除该参数(协议演进为
        output_config.effort);canonical 采样语义仅对 openai/responses 生效。"""
        kwargs = anthropic_request(messages=messages, tools=tools, max_tokens=max_tokens)
        kwargs["model"] = model
        try:
            # SDK 重载无法推断动态 kwargs 的返回类型,在调用边界显式 cast。
            raw = cast(
                "object",
                await self._sdk.messages.create(**cast("dict[str, Any]", kwargs)),
            )
        except APIError as e:
            raise LlmError(f"anthropic 调用失败: {e}") from e
        except ValueError as e:
            # 200 但 body 非 JSON 等裸解码失败(SDK 未包成 APIError 的边角)
            raise LlmError(f"anthropic 应答畸形: {e}") from e
        try:
            return anthropic_parse(raw)
        except Exception as e:
            raise LlmError(f"anthropic 应答解析失败: {e}") from e

    async def close(self) -> None:
        await self._sdk.close()


def _anthropic_image_source(url: object) -> dict[str, object]:
    """vision data-url(`data:<mime>;base64,<b64>`)→ base64 source 块。
    非 data-url 形态属编程错误(chat_vision 只产 data-url),fail-closed 上抛。"""
    text = str(url or "")
    prefix = "data:"
    marker = ";base64,"
    if not text.startswith(prefix) or marker not in text:
        raise LlmError(f"vision 图片引用非 data-url 形态,无法按 anthropic 出站: {text[:60]}")
    body = text[len(prefix) :]
    mime, _, b64 = body.partition(marker)
    return {"type": "base64", "media_type": mime, "data": b64}


def _anthropic_user_content(content: object) -> object:
    """user content:str 原样;content-parts 列表 → text / image(base64 source) 块。"""
    if not isinstance(content, list):
        return content
    blocks: list[dict[str, object]] = []
    for part in cast("list[object]", content):
        if not isinstance(part, dict):
            blocks.append({"type": "text", "text": str(part)})
            continue
        typed = cast("dict[str, object]", part)
        if typed.get("type") == "image_url":
            image_url = typed.get("image_url")
            url: object = None
            if isinstance(image_url, dict):
                url = cast("dict[str, object]", image_url).get("url")
            blocks.append({"type": "image", "source": _anthropic_image_source(url)})
        else:
            blocks.append({"type": "text", "text": str(typed.get("text") or "")})
    return blocks


def _anthropic_assistant(msg: Mapping[str, object]) -> dict[str, object]:
    """assistant → content 块序列:text(非空时)+ tool_use(args 解析,畸形以 {}
    回放——参数畸形观察已由内核侧喂回自纠,回放保真以原文为准)。"""
    blocks: list[dict[str, object]] = []
    content = msg.get("content")
    if isinstance(content, str) and content:
        blocks.append({"type": "text", "text": content})
    raw_calls = msg.get("tool_calls")
    for tc in cast("list[object]", raw_calls if isinstance(raw_calls, list) else []):
        if not isinstance(tc, dict):
            continue
        typed = cast("dict[str, object]", tc)
        fn = typed.get("function")
        fn_map = cast("dict[str, object]", fn if isinstance(fn, dict) else {})
        args = _json_object_or_none(str(fn_map.get("arguments") or ""))
        blocks.append(
            {
                "type": "tool_use",
                "id": str(typed.get("id") or ""),
                "name": str(fn_map.get("name") or ""),
                "input": args if args is not None else {},
            }
        )
    if not blocks:
        # 空 text 块会被 Messages API 服务端 400 拒收——本地直给可诊断错误,
        # 不产出注定无效的请求
        raise LlmError("assistant 消息无可回放内容(空 content 且无 tool_calls)")
    return {"role": "assistant", "content": blocks}


def _anthropic_tool(t: Mapping[str, object]) -> dict[str, object]:
    """openai function schema → {name, description, input_schema}(input_schema
    必填:缺省给空 object schema,防御声明缺失)。"""
    fn = t.get("function")
    fn_map = cast("dict[str, object]", fn if isinstance(fn, dict) else {})
    return {
        "name": str(fn_map.get("name") or ""),
        "description": str(fn_map.get("description") or ""),
        "input_schema": fn_map.get("parameters") or {"type": "object", "properties": {}},
    }


def anthropic_request(
    *,
    messages: Sequence[Mapping[str, object]],
    tools: list[dict[str, object]],
    max_tokens: int | None = None,
) -> dict[str, object]:
    """canonical 消息史 → anthropic Messages 请求 kwargs(messages.create 形态)。
    system 提取置顶层;tool 观察合并进 user 消息;max_tokens 必填缺省 8192。
    不含 temperature:官方 SDK 1.8.0 类型面已移除(协议演进为 effort 档位)。"""
    system_parts: list[str] = []
    out_msgs: list[dict[str, object]] = []
    pending_results: list[dict[str, object]] = []

    def _flush_results() -> None:
        if pending_results:
            out_msgs.append({"role": "user", "content": list(pending_results)})
            pending_results.clear()

    for msg in messages:
        role = str(msg.get("role") or "")
        if role == "system":
            system_parts.append(str(msg.get("content") or ""))
        elif role == "user":
            _flush_results()
            out_msgs.append(
                {"role": "user", "content": _anthropic_user_content(msg.get("content"))}
            )
        elif role == "assistant":
            _flush_results()
            out_msgs.append(_anthropic_assistant(msg))
        elif role == "tool":
            pending_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": str(msg.get("tool_call_id") or ""),
                    "content": str(msg.get("content") or ""),
                }
            )
    _flush_results()

    req: dict[str, object] = {
        "max_tokens": max_tokens if max_tokens is not None else DEFAULT_MAX_TOKENS,
        "system": "\n\n".join(system_parts),
        "messages": out_msgs,
    }
    if tools:
        req["tools"] = [_anthropic_tool(t) for t in tools]
    return req


def anthropic_parse(raw: object) -> ProtocolReply:
    """anthropic Message 应答(SDK 对象,attribute 形态)→ ProtocolReply。
    text/tool_use 块回读;usage input/output → prompt/completion(total 求和)。"""
    texts: list[str] = []
    calls: list[LlmToolCall] = []
    content_obj = _attr(raw, "content")
    for block in cast("list[object]", content_obj if isinstance(content_obj, list) else []):
        btype = _attr_str(block, "type")
        if btype == "text":
            texts.append(_attr_str(block, "text"))
        elif btype == "tool_use":
            args_obj = _attr(block, "input")
            call_id = _attr_str(block, "id")
            name = _attr_str(block, "name")
            if isinstance(args_obj, dict):
                args = cast("dict[str, object]", args_obj)
                calls.append(
                    LlmToolCall(
                        call_id=call_id,
                        name=name,
                        args=args,
                        raw_args=json.dumps(args, ensure_ascii=False),
                    )
                )
            elif args_obj is None:
                # input 字段缺失与 openai 侧 arguments 缺失同义:归一空参执行,
                # 不进自纠循环(跨协议语义对称)
                calls.append(LlmToolCall(call_id=call_id, name=name, args={}, raw_args=""))
            else:
                # 非 dict 畸形:args=None 喂回自纠;raw_args 序列化保原文
                raw_txt = (
                    json.dumps(args_obj, ensure_ascii=False, default=str)
                    if args_obj is not None
                    else ""
                )
                calls.append(LlmToolCall(call_id=call_id, name=name, args=None, raw_args=raw_txt))
    usage_obj = _attr(raw, "usage")
    input_tokens = _usage_count(usage_obj, "input_tokens")
    output_tokens = _usage_count(usage_obj, "output_tokens")
    content = "".join(texts).strip() or None
    return ProtocolReply(
        content=content,
        tool_calls=calls,
        usage=ProtocolUsage(
            prompt_tokens=input_tokens,
            completion_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
        ),
    )


# ── openai Responses 翻译(canonical → 请求 kwargs / 应答 → ProtocolReply) ──


def _responses_tool(t: Mapping[str, object]) -> dict[str, object]:
    """openai function schema → Responses 扁平声明。"""
    fn = t.get("function")
    fn_map = cast("dict[str, object]", fn if isinstance(fn, dict) else {})
    return {
        "type": "function",
        "name": str(fn_map.get("name") or ""),
        "description": str(fn_map.get("description") or ""),
        "parameters": fn_map.get("parameters") or {"type": "object", "properties": {}},
    }


def _tool_output_item(msg: Mapping[str, object]) -> dict[str, object]:
    """tool 消息 → function_call_output 项。"""
    return {
        "type": "function_call_output",
        "call_id": str(msg.get("tool_call_id") or ""),
        "output": str(msg.get("content") or ""),
    }


def _content_part(part: object) -> dict[str, object]:
    """单个 canonical 内容部件 → Responses 部件(图片或文本)。"""
    if not isinstance(part, dict):
        return {"type": "input_text", "text": str(part)}
    typed = cast("dict[str, object]", part)
    if typed.get("type") == "image_url":
        image_url = typed.get("image_url")
        url = (
            cast("dict[str, object]", image_url).get("url") if isinstance(image_url, dict) else None
        )
        return {"type": "input_image", "image_url": str(url or "")}
    return {"type": "input_text", "text": str(typed.get("text") or "")}


def _content_items(content: list[object], role: str) -> list[dict[str, object]]:
    """列表内容 → 定型后的 Responses content 数组。"""
    parts = [_content_part(part) for part in content]
    # text 部件按 role 定型(assistant→output_text);不可用 **p 直接展开
    # ——构造值自带 input_text 会盖掉定型。图片部件无 input/output 之分,原样保留
    part_type = "output_text" if role == "assistant" else "input_text"
    return [
        p if p.get("type") == "input_image" else {"type": part_type, "text": p["text"]}
        for p in parts
    ]


def _message_item(msg: Mapping[str, object], role: str) -> dict[str, object] | None:
    """role 消息 → Responses message 项;无可用内容返回 None。"""
    content = msg.get("content")
    if isinstance(content, list):
        # type 显式补 message(role 消息省略 type 虽为官方缺省语义,
        # 但木牛入站只接受协议明确规定的形态)
        content_items = _content_items(cast("list[object]", content), role)
        return {"type": "message", "role": role, "content": content_items}
    if isinstance(content, str) and content:
        # 纯文本统一发 part 数组形态(与字符串快捷形态在 Responses 规范内等价),
        # 对下游保持兼容;木牛入站只接受数组形态。
        text_part_type = "output_text" if role == "assistant" else "input_text"
        return {
            "type": "message",
            "role": role,
            "content": [{"type": text_part_type, "text": content}],
        }
    return None


def _function_call_items(msg: Mapping[str, object]) -> list[dict[str, object]]:
    """assistant 工具调用轮 → function_call 项(原文 arguments 按序回放)。"""
    raw = msg.get("tool_calls")
    if not isinstance(raw, list):
        return []
    items: list[dict[str, object]] = []
    for tc in cast("list[object]", raw):
        if not isinstance(tc, dict):
            continue
        typed = cast("dict[str, object]", tc)
        fn = typed.get("function")
        fn_map = cast("dict[str, object]", fn if isinstance(fn, dict) else {})
        items.append(
            {
                "type": "function_call",
                "call_id": str(typed.get("id") or ""),
                "name": str(fn_map.get("name") or ""),
                "arguments": str(fn_map.get("arguments") or ""),
            }
        )
    return items


def _responses_input_items(messages: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    """canonical 消息史 → Responses input 项序列:role 消息(text/output_text)、
    function_call(arguments 原文回放——Responses 以字符串承载,设计决策 4)、
    function_call_output。system 项由调用方提取为 instructions。"""
    items: list[dict[str, object]] = []
    for msg in messages:
        role = str(msg.get("role") or "")
        if role == "system":
            continue  # instructions 在 responses_request 顶层统一提取
        if role == "tool":
            items.append(_tool_output_item(msg))
            continue
        item = _message_item(msg, role)
        if item is not None:
            items.append(item)
        if role == "assistant":
            items.extend(_function_call_items(msg))
    return items


def responses_request(
    *,
    messages: Sequence[Mapping[str, object]],
    tools: list[dict[str, object]],
    temperature: float,
    max_tokens: int | None = None,
) -> dict[str, object]:
    """canonical 消息史 → Responses 请求 kwargs(responses.create 形态)。
    system 提取为 instructions;工具声明扁平化;max_tokens → max_output_tokens。"""
    system_parts: list[str] = []
    system_parts.extend(
        str(msg.get("content") or "") for msg in messages if str(msg.get("role") or "") == "system"
    )
    req: dict[str, object] = {
        "temperature": temperature,
        "instructions": "\n\n".join(system_parts),
        "input": _responses_input_items(messages),
    }
    if max_tokens is not None:
        req["max_output_tokens"] = max_tokens
    if tools:
        req["tools"] = [_responses_tool(t) for t in tools]
    return req


def responses_parse(raw: object) -> ProtocolReply:
    """Responses 应答(SDK 对象)→ ProtocolReply:message 输出文本回读、
    function_call 回读(arguments 畸形 args=None + raw_args 原文)、usage
    input/output/total → prompt/completion/total。"""
    texts: list[str] = []
    calls: list[LlmToolCall] = []
    output_obj = _attr(raw, "output")
    for item in cast("list[object]", output_obj if isinstance(output_obj, list) else []):
        itype = _attr_str(item, "type")
        if itype == "message":
            content_obj = _attr(item, "content")
            texts.extend(
                _attr_str(part, "text")
                for part in cast(
                    "list[object]", content_obj if isinstance(content_obj, list) else []
                )
                if _attr_str(part, "type") == "output_text"
            )
        elif itype == "function_call":
            raw_args = _attr_str(item, "arguments")
            calls.append(
                LlmToolCall(
                    call_id=_attr_str(item, "call_id"),
                    name=_attr_str(item, "name"),
                    args=_json_object_or_none(raw_args),
                    raw_args=raw_args,
                )
            )
    usage_obj = _attr(raw, "usage")
    input_tokens = _usage_count(usage_obj, "input_tokens")
    output_tokens = _usage_count(usage_obj, "output_tokens")
    total = _usage_count(usage_obj, "total_tokens") or (input_tokens + output_tokens)
    content = "".join(texts).strip() or None
    return ProtocolReply(
        content=content,
        tool_calls=calls,
        usage=ProtocolUsage(
            prompt_tokens=input_tokens,
            completion_tokens=output_tokens,
            total_tokens=total,
        ),
    )


async def responses_create(
    client: AsyncOpenAI,
    *,
    model: str,
    messages: Sequence[Mapping[str, object]],
    tools: list[dict[str, object]],
    temperature: float,
    max_tokens: int | None = None,
) -> ProtocolReply:
    """Responses 协议出站(复用既有 AsyncOpenAI 客户端):翻译 → 调用 → 解析;
    错误映射与 anthropic 同构(openai APIError 族 + 200 裸解码失败 → LlmError)。"""
    kwargs = responses_request(
        messages=messages, tools=tools, temperature=temperature, max_tokens=max_tokens
    )
    kwargs["model"] = model
    try:
        # SDK 重载无法推断动态 kwargs 的返回类型,在调用边界显式 cast。
        raw = cast(
            "object",
            await client.responses.create(**cast("dict[str, Any]", kwargs)),
        )
    except OpenAIAPIError as e:
        raise LlmError(f"responses 调用失败: {e}") from e
    except ValueError as e:
        raise LlmError(f"responses 应答畸形: {e}") from e
    try:
        return responses_parse(raw)
    except Exception as e:
        raise LlmError(f"responses 应答解析失败: {e}") from e
