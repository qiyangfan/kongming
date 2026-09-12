"""LLM 客户端(openai 兼容协议,通义/DeepSeek 改环境变量即切换,nl2sql spec)。

ADR-002 注记:LLM key 是 agent 面唯一持有的外部凭证(非数据凭证)。
纪律:同步 request/response,无自写重试——selfheal 是链路唯一重试机制。
019 新增 chat_with_tools(ReAct 内核):tools 参数原生透传,无自写协议层。
"""

import contextvars
import json
import logging
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any, Literal, cast

from openai import AsyncOpenAI
from openai.types.chat import (
    ChatCompletionFunctionToolParam,
    ChatCompletionMessageFunctionToolCall,
    ChatCompletionMessageParam,
)

from kongming_agent.settings import Settings

log = logging.getLogger(__name__)


class LlmError(Exception):
    """LLM 调用失败(key 缺失/网络/超时/协议异常)的统一出口。"""


# usage 上报后台任务强引用集(防 GC;完成即摘)
_BACKGROUND_REPORTS: set[object] = set()

# 路由回退告警去重(R1-M1:按 task+name,防每节点刷屏)
_ROUTE_MISS_WARNED: set[tuple[str, str]] = set()

# 035:request 级 usage 收集器(bot 链路请求内同步采集)——在场时 _report
# 只收集不上报(bot 渠道唯一上报口=响应携带,与 fire-and-forget 不双写);
# 缺省(chat/recipe/titler 等)保持 021 fire-and-forget 零回归。
_USAGE_COLLECTOR: contextvars.ContextVar[list[dict[str, object]] | None] = contextvars.ContextVar(
    "km_usage_collector", default=None
)


def set_usage_collector(
    collector: list[dict[str, object]],
) -> contextvars.Token[list[dict[str, object]] | None]:
    return _USAGE_COLLECTOR.set(collector)


def reset_usage_collector(
    token: contextvars.Token[list[dict[str, object]] | None],
) -> None:
    _USAGE_COLLECTOR.reset(token)


@dataclass(frozen=True)
class LlmToolCall:
    """LLM 产出的单次工具调用;args 解析失败时 args=None + raw_args 原文(喂回自纠)。"""

    call_id: str
    name: str
    args: dict[str, object] | None
    raw_args: str = ""


@dataclass(frozen=True)
class ToolChatReply:
    """chat_with_tools 应答:content 与 tool_calls 至多一个有语义(互斥由 ReAct 判定)。"""

    content: str | None = None
    tool_calls: list[LlmToolCall] = field(default_factory=list[LlmToolCall])


class LlmClient:
    """薄封装:chat 文本问答 + tool_calls(019)+ embeddings;
    021:多模型注册表池 + 任务级路由(缺省主模型零回归)+ 用量采集上报。"""

    def __init__(
        self,
        settings: Settings,
        usage_reporter: (
            Callable[[list[dict[str, object]]], Coroutine[Any, Any, None]] | None
        ) = None,  # async callable(list[dict]),fire-and-forget
    ) -> None:
        self._model = settings.llm_model
        self._client: AsyncOpenAI | None = None
        if settings.llm_api_key:
            self._client = AsyncOpenAI(
                api_key=settings.llm_api_key,
                base_url=settings.llm_base_url,
                timeout=settings.llm_timeout_sec,
            )
        # 021 注册表池(主模型恒为 default;畸形/白名单外条目已在 parse 期剔除)
        self._pool: dict[str, tuple[AsyncOpenAI, str]] = {}
        self._image_limits: dict[str, float] = {}  # 036⑦:条目图片上限
        for name, entry in settings.parse_registry().items():
            self._pool[name] = (
                AsyncOpenAI(
                    api_key=entry.api_key,
                    base_url=entry.base_url,
                    timeout=settings.llm_timeout_sec,
                ),
                entry.model,
            )
            if entry.max_image_mb is not None:
                self._image_limits[name] = entry.max_image_mb
        self._routes = settings.parse_routes()
        self._reporter = usage_reporter
        self._default_image_limit_mb = settings.llm_max_image_mb
        # embedding 独立客户端(009):主模型与 embedding 供应商解耦(上游 028 教训)
        emb_key = settings.embedding_api_key or settings.llm_api_key
        emb_base = settings.embedding_base_url or settings.llm_base_url
        self._emb_model = settings.embedding_model
        self._emb_max_texts = settings.embedding_max_texts
        self._emb_batch_size = settings.embedding_batch_size
        self._emb_client: AsyncOpenAI | None = None
        if emb_key:
            self._emb_client = AsyncOpenAI(
                api_key=emb_key,
                base_url=emb_base,
                timeout=settings.embedding_timeout_sec,
            )

    async def aclose(self) -> None:
        """021① 清偿:显式关闭全部客户端(主/注册表池/embedding)——
        deps 指纹变更重建时调用,连接关闭不再依赖 GC 时机。"""
        clients: list[AsyncOpenAI] = []
        if self._client is not None:
            clients.append(self._client)
        clients.extend(c for c, _ in self._pool.values())
        if self._emb_client is not None:
            clients.append(self._emb_client)
        for c in clients:
            try:
                await c.close()
            except Exception:
                log.warning("LLM client close 失败(忽略)", exc_info=True)

    def image_limit_mb(self, task: str | None = None) -> float:
        """036⑦:当前生效模型的图片体积上限(MB)——task 路由命中条目
        有配上限取之,否则回退主模型上限(settings.llm_max_image_mb)。"""
        if task:
            name = self._routes.get(task)
            if name and name in self._image_limits:
                return self._image_limits[name]
        return self._default_image_limit_mb

    def _resolve(
        self, task: str | None, model_override: str | None = None
    ) -> tuple[AsyncOpenAI | None, str]:
        """025:model_override(Bot 配置)优先于任务路由;注册表未命中告警回退。"""
        """任务路由(021,单点):路由表命中注册表→该客户端;否则主模型零回归。
        R1-M1:路由目标不在注册表时告警一次(按 task+name 去重防刷屏)。"""
        if model_override:
            if model_override in self._pool:
                client, model = self._pool[model_override]
                return client, model
            marker = ("__override__", model_override)
            if marker not in _ROUTE_MISS_WARNED:
                _ROUTE_MISS_WARNED.add(marker)
                log.warning("Bot 模型覆盖 %s 不在注册表,回退任务路由/主模型", model_override)
        if task:
            name = self._routes.get(task)
            if name:
                if name in self._pool:
                    client, model = self._pool[name]
                    return client, model
                marker = (task, name)
                if marker not in _ROUTE_MISS_WARNED:
                    _ROUTE_MISS_WARNED.add(marker)
                    log.warning("任务 %s 路由目标 %s 不在注册表,回退主模型", task, name)
        return self._client, self._model

    def _report(
        self,
        *,
        task: str,
        model: str,
        trace_id: str | None,
        tenant_key: str | None = None,
        usage: object | None,
        status: Literal["SUCCESS", "ERROR"] = "SUCCESS",  # 038:失败调用也采集
        latency_ms: int | None = None,  # 038:调用耗时(毫秒)
    ) -> None:
        """用量采集(021):fire-and-forget,任何失败仅日志不阻断主链路。
        035:收集器在场(bot 链路)只收集不上报——唯一上报口=响应携带。
        038:status/latency_ms 补全——失败调用(超时/断连/协议错)同样
        落记录(失败率可见),usage 缺失不再丢弃(记 0)。"""
        record: dict[str, object] = {
            "trace_id": trace_id,
            "tenant_key": tenant_key,
            "model": model,
            "task": task,
            "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
            "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
            "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
            "status": status,
            "latency_ms": latency_ms,
        }
        collector = _USAGE_COLLECTOR.get()
        if collector is not None:
            collector.append({**record, "seq": len(collector)})
            return
        if self._reporter is None:
            return
        try:
            import asyncio

            task_obj = asyncio.create_task(self._reporter([record]))
            # 防 GC(后台任务强引用,完成即摘;010 同款)
            _BACKGROUND_REPORTS.add(task_obj)
            task_obj.add_done_callback(_BACKGROUND_REPORTS.discard)
        except Exception:
            log.warning("usage 上报调度失败(不阻断)", exc_info=True)

    async def chat(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        task: str | None = None,  # 021:任务类型(路由;缺省主模型)
        trace_id: str | None = None,  # 021:用量采集贯穿
        tenant_key: str | None = None,  # 021:用量租户维度(api 反解)
    ) -> str:
        client, model = self._resolve(task)
        if client is None:
            raise LlmError("KM_AGENT_LLM_API_KEY 未配置")
        # max_tokens 缺省不传(供应商默认),仅轻量端点(如标题改写)显式封顶(010);
        # 分支写法:openai 类型 None ≠ 省略,**kwargs 过不了 mypy 重载
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        t0 = time.monotonic()
        try:
            if max_tokens is None:
                resp = await client.chat.completions.create(
                    model=model, messages=messages, temperature=temperature
                )
            else:
                resp = await client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
        except Exception as e:
            self._report(
                task=task or "CHAT",
                model=model,
                trace_id=trace_id,
                tenant_key=tenant_key,
                usage=None,
                status="ERROR",
                latency_ms=int((time.monotonic() - t0) * 1000),
            )
            raise LlmError(f"LLM 调用失败: {e}") from e
        try:
            content = (resp.choices[0].message.content or "").strip()
        except (IndexError, AttributeError, TypeError) as e:
            # R1-B2:应答畸形(空 choices 等)=供应商协议错,同样归 ERROR 采集
            self._report(
                task=task or "CHAT",
                model=model,
                trace_id=trace_id,
                tenant_key=tenant_key,
                usage=resp.usage,
                status="ERROR",
                latency_ms=int((time.monotonic() - t0) * 1000),
            )
            raise LlmError(f"LLM 应答畸形: {e}") from e
        self._report(
            task=task or "CHAT",
            model=model,
            trace_id=trace_id,
            tenant_key=tenant_key,
            usage=resp.usage,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )
        return content

    async def chat_vision(
        self,
        system: str,
        user: str,
        *,
        images: list[tuple[str, str]],  # (mime, base64)
        task: str | None = None,  # 030:"vision" 走 021 路由
        trace_id: str | None = None,
        tenant_key: str | None = None,
    ) -> str:
        """vision 调用(030):content parts=[text, image_url(data-url)];
        task="vision" 经 021 路由到多模态模型;未配回退主模型(文本模型
        供应商报错即 LlmError,调用方 fail-closed)。"""
        client, model = self._resolve(task)
        if client is None:
            raise LlmError("KM_AGENT_LLM_API_KEY 未配置")
        content: list[dict[str, object]] = [{"type": "text", "text": user}]
        for mime, b64 in images:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{b64}"},
                }
            )
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": system},
            {"role": "user", "content": content},  # type: ignore[list-item,misc]
        ]
        t0 = time.monotonic()
        try:
            resp = await client.chat.completions.create(
                model=model, messages=messages, temperature=0.2
            )
        except Exception as e:
            self._report(
                task=task or "VISION",
                model=model,
                trace_id=trace_id,
                tenant_key=tenant_key,
                usage=None,
                status="ERROR",
                latency_ms=int((time.monotonic() - t0) * 1000),
            )
            raise LlmError(f"vision 调用失败: {e}") from e
        try:
            text = resp.choices[0].message.content or ""
        except (IndexError, AttributeError, TypeError) as e:
            # R1-B2:应答畸形=供应商协议错,同样归 ERROR 采集
            self._report(
                task=task or "VISION",
                model=model,
                trace_id=trace_id,
                tenant_key=tenant_key,
                usage=resp.usage,
                status="ERROR",
                latency_ms=int((time.monotonic() - t0) * 1000),
            )
            raise LlmError(f"vision 应答畸形: {e}") from e
        self._report(
            task=task or "VISION",
            model=model,
            trace_id=trace_id,
            tenant_key=tenant_key,
            usage=resp.usage,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )
        return text

    async def chat_with_tools(
        self,
        system: str,
        messages: list[ChatCompletionMessageParam],
        tools: list[dict[str, object]],
        *,
        temperature: float = 0.1,
        task: str | None = None,  # 021:任务类型(REACT 决策等)
        trace_id: str | None = None,
        tenant_key: str | None = None,
        model_override: str | None = None,  # 025:Bot 模型覆盖
    ) -> ToolChatReply:
        """ReAct 内核调用(019):tools 为 OpenAI 兼容 function schema 列表。

        返回 ToolChatReply:LLM 产出 tool_calls 时 content 通常为 None/空;
        不再调用工具时 tool_calls 为空、content 为最终答案。
        tool_call 的 arguments(JSON 字符串)解析失败不抛错——args=None +
        raw_args 原文,由 ReAct 节点收敛为「参数畸形」观察喂回 LLM 自纠。
        """
        client, model = self._resolve(task, model_override)
        if client is None:
            raise LlmError("KM_AGENT_LLM_API_KEY 未配置")
        full_messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": system},
            *messages,
        ]
        t0 = time.monotonic()
        try:
            # tools 有无两分支(openai 重载对 **kwargs 展开不过 mypy;同 chat 的
            # max_tokens 分支写法先例);dict 列表结构与 ChatCompletionFunctionToolParam
            # (TypedDict)对齐,cast 收窄给 mypy
            if tools:
                resp = await client.chat.completions.create(
                    model=model,
                    messages=full_messages,
                    temperature=temperature,
                    tools=cast("list[ChatCompletionFunctionToolParam]", tools),
                    tool_choice="auto",
                )
            else:
                resp = await client.chat.completions.create(
                    model=model, messages=full_messages, temperature=temperature
                )
        except Exception as e:
            self._report(
                task=task or "CHAT_TOOLS",
                model=model,
                trace_id=trace_id,
                tenant_key=tenant_key,
                usage=None,
                status="ERROR",
                latency_ms=int((time.monotonic() - t0) * 1000),
            )
            raise LlmError(f"LLM 调用失败: {e}") from e
        try:
            msg = resp.choices[0].message
        except (IndexError, AttributeError, TypeError) as e:
            # R1-B2:应答畸形(空 choices 等)=供应商协议错,同样归 ERROR 采集
            self._report(
                task=task or "CHAT_TOOLS",
                model=model,
                trace_id=trace_id,
                tenant_key=tenant_key,
                usage=resp.usage,
                status="ERROR",
                latency_ms=int((time.monotonic() - t0) * 1000),
            )
            raise LlmError(f"LLM 应答畸形: {e}") from e
        self._report(
            task=task or "CHAT_TOOLS",
            model=model,
            trace_id=trace_id,
            tenant_key=tenant_key,
            usage=resp.usage,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )
        calls: list[LlmToolCall] = []
        for tc in msg.tool_calls or []:
            # SDK 联合类型:function 工具调用与 custom 工具调用;本内核只产出
            # function 形态(custom 无 function 属性,跳过——防御未知供应商形态)
            if not isinstance(tc, ChatCompletionMessageFunctionToolCall):
                continue
            raw = tc.function.arguments or ""
            try:
                # json.loads 返回 Any,显式注解约束到 LlmToolCall.args 契约型
                parsed: dict[str, object] | None = json.loads(raw) if raw.strip() else {}
                if not isinstance(parsed, dict):
                    parsed = None  # 标量/数组畸形 → 喂回自纠
            except json.JSONDecodeError:
                parsed = None
            calls.append(
                LlmToolCall(call_id=tc.id, name=tc.function.name, args=parsed, raw_args=raw)
            )
        content = (msg.content or "").strip() or None
        return ToolChatReply(content=content, tool_calls=calls)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """批量向量化(009;openai 兼容 embeddings API)。

        供应商单批条数受限(dashscope text-embedding-v2=25,冒烟实证 28 条整批被拒),
        内部按 embedding_batch_size 切分多次调用后拼接。
        """
        if self._emb_client is None:
            raise LlmError("embedding/LLM API key 均未配置")
        if not texts or len(texts) > self._emb_max_texts:
            raise LlmError(f"embeddings 批量须为 1..{self._emb_max_texts} 条")
        out: list[list[float]] = []
        for start in range(0, len(texts), self._emb_batch_size):
            batch = texts[start : start + self._emb_batch_size]
            t0 = time.monotonic()
            try:
                resp = await self._emb_client.embeddings.create(model=self._emb_model, input=batch)
            except Exception as e:
                self._report(
                    task="EMBED",
                    model=self._emb_model,
                    trace_id=None,
                    usage=None,
                    status="ERROR",
                    latency_ms=int((time.monotonic() - t0) * 1000),
                )
                raise LlmError(f"embeddings 调用失败: {e}") from e
            # 按 index 归位(API 不保证返回有序);R1-B2:条数/index 集合缺失
            # 或重复=应答畸形(静默拼接会致调用方向量错位),归 ERROR 采集后
            # 收敛 LlmError(R2-B3:index 集合等价一并校验)
            data = sorted(resp.data, key=lambda d: d.index)
            if sorted(d.index for d in data) != list(range(len(batch))):
                self._report(
                    task="EMBED",
                    model=self._emb_model,
                    trace_id=None,
                    usage=getattr(resp, "usage", None),
                    status="ERROR",
                    latency_ms=int((time.monotonic() - t0) * 1000),
                )
                raise LlmError(f"embeddings 应答畸形: 期望 {len(batch)} 条实得 {len(data)}")
            # 021:embedding 用量同通道上报(不参与模型路由,009 立约)
            self._report(
                task="EMBED",
                model=self._emb_model,
                trace_id=None,
                usage=getattr(resp, "usage", None),
                latency_ms=int((time.monotonic() - t0) * 1000),
            )  # tenant 维度不适用(009 embedding 独立通道)
            out.extend(d.embedding for d in data)
        return out
