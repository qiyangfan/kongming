"""LLM 客户端(多协议出站:openai 兼容 / anthropic / openai Responses,ADR-008)。

ADR-002 注记:LLM key 是 agent 面唯一持有的外部凭证(非数据凭证)。
019 chat_with_tools(ReAct 内核):tools 参数原生透传;openai 通道保持既有
路径行为等价,anthropic/responses 通道经 llm_protocol 翻译层收敛。

072 出站韧性:四个出站点统一经 _resilient_call 接入——瞬时故障(连接建立
失败/读超时/408/429/5xx)按退避重发(尝试预算 ≤3,按上层 agent_timeout_sec
反推),瞬时故障耗尽后单层降级(任务路由目标 → 主模型 → 显式 fallback 条目,
Bot model_override 不降级),用量每尝试一条(失败率与重试成本可见)。
"""

import contextvars
import json
import logging
import time
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from functools import partial
from typing import Any, Literal, cast

from openai import AsyncOpenAI
from openai.types import CreateEmbeddingResponse
from openai.types.chat import (
    ChatCompletionFunctionToolParam,
    ChatCompletionMessageFunctionToolCall,
    ChatCompletionMessageParam,
)

from kongming_agent.llm_protocol import (
    PROTOCOL_ANTHROPIC,
    PROTOCOL_OPENAI,
    PROTOCOL_RESPONSES,
    AnthropicClient,
    ProtocolReply,
    responses_create,
)
from kongming_agent.llm_protocol import LlmError as LlmError  # 显式 re-export
from kongming_agent.llm_protocol import LlmToolCall as LlmToolCall  # 显式 re-export
from kongming_agent.retry import call_with_retry, is_transient_error
from kongming_agent.settings import DEFAULT_PROTOCOL, VALID_PROTOCOLS, Settings

log = logging.getLogger(__name__)

# 072:出站目标三元组(协议客户端, 实际模型名, 出站协议);_resolve 产物客户端可为
# None(未配 key),降级链解析后过滤
Target = tuple["AsyncOpenAI | AnthropicClient", str, str]
ResolvedTarget = tuple["AsyncOpenAI | AnthropicClient | None", str, str]


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
class ToolChatReply:
    """chat_with_tools 应答:content 与 tool_calls 至多一个有语义(互斥由 ReAct 判定)。"""

    content: str | None = None
    tool_calls: list[LlmToolCall] = field(default_factory=list[LlmToolCall])


def _same_target(a: ResolvedTarget, b: ResolvedTarget) -> bool:
    """同目标判定(降级链去重):客户端实例 + 模型名 + 协议三元一致。"""
    return a[0] is not None and b[0] is not None and a[0] is b[0] and a[1] == b[1] and a[2] == b[2]


class LlmClient:
    """薄封装:chat 文本问答 + tool_calls(019)+ embeddings;
    021:多模型注册表池 + 任务级路由(缺省主模型零回归)+ 用量采集上报;
    072:瞬时重试 + 单层降级(见 _resilient_call)。"""

    def __init__(
        self,
        settings: Settings,
        usage_reporter: (
            Callable[[list[dict[str, object]]], Coroutine[Any, Any, None]] | None
        ) = None,  # async callable(list[dict]),fire-and-forget
    ) -> None:
        self._model = settings.llm_model
        # 多协议(ADR-008):主模型协议(畸形值已在 fail_closed_checks 告警回退;
        # 此处兜底归一,防未跑启动检查的直构场景)
        self._protocol = (
            settings.llm_protocol if settings.llm_protocol in VALID_PROTOCOLS else DEFAULT_PROTOCOL
        )
        self._client: AsyncOpenAI | AnthropicClient | None = None
        if settings.llm_api_key:
            if self._protocol == PROTOCOL_ANTHROPIC:
                self._client = AnthropicClient(
                    base_url=settings.llm_base_url,
                    api_key=settings.llm_api_key,
                    timeout_sec=settings.llm_timeout_sec,
                )
            else:
                # openai 与 responses 共用 AsyncOpenAI 客户端(通道在调用期分支)
                self._client = AsyncOpenAI(
                    api_key=settings.llm_api_key,
                    base_url=settings.llm_base_url,
                    timeout=settings.llm_timeout_sec,
                )
        # 021 注册表池(主模型恒为 default;畸形/白名单外条目已在 parse 期剔除)
        # 多协议(ADR-008):池条目携带协议 (client, model, protocol)
        self._pool: dict[str, tuple[AsyncOpenAI | AnthropicClient, str, str]] = {}
        self._image_limits: dict[str, float] = {}  # 036⑦:条目图片上限
        self._pool_windows: dict[str, int] = {}  # 080:条目上下文窗口
        for name, entry in settings.parse_registry().items():
            if entry.protocol == PROTOCOL_ANTHROPIC:
                entry_client: AsyncOpenAI | AnthropicClient = AnthropicClient(
                    base_url=entry.base_url,
                    api_key=entry.api_key,
                    timeout_sec=settings.llm_timeout_sec,
                )
            else:
                entry_client = AsyncOpenAI(
                    api_key=entry.api_key,
                    base_url=entry.base_url,
                    timeout=settings.llm_timeout_sec,
                )
            self._pool[name] = (entry_client, entry.model, entry.protocol)
            if entry.max_image_mb is not None:
                self._image_limits[name] = entry.max_image_mb
            if entry.context_window is not None:
                self._pool_windows[name] = entry.context_window
        self._routes = settings.parse_routes()
        self._reporter = usage_reporter
        self._default_image_limit_mb = settings.llm_max_image_mb
        # embedding 独立客户端(009):主模型与 embedding 供应商解耦(上游 028 教训)
        emb_key = settings.embedding_api_key or settings.llm_api_key
        emb_base = settings.embedding_base_url or settings.llm_base_url
        self._emb_model = settings.embedding_model
        self._emb_max_texts = settings.embedding_max_texts
        self._emb_batch_size = settings.embedding_batch_size
        self._emb_dimensions = settings.embedding_dimensions
        self._emb_client: AsyncOpenAI | None = None
        if emb_key:
            self._emb_client = AsyncOpenAI(
                api_key=emb_key,
                base_url=emb_base,
                timeout=settings.embedding_timeout_sec,
            )
        # 072 出站韧性:尝试预算与退避参数(design D4:总尝试上限含降级目标)
        self._max_attempts = max(1, settings.llm_max_attempts)
        self._retry_base_ms = settings.llm_retry_base_ms
        self._retry_max_wait_ms = settings.llm_retry_max_wait_ms
        # 080 装配预算:窗口 − 预留输出 − 安全边际
        self._default_window = settings.prompt_context_window_default
        self._budget_reserve = (
            settings.prompt_reserved_output_tokens + settings.prompt_safety_margin_tokens
        )
        # 072 降级条目:名称入实例,目标按当前池解析(与 _resolve 同口径,
        # 启动期 fail_closed_checks 已告警校验;条目被剔除/不存在 → 降级关闭,
        # 不拒启——design D3)
        self._fallback_name = (settings.llm_fallback_entry or "").strip()
        if self._fallback_name and self._fallback_name not in self._pool:
            log.warning("降级条目 %s 不在注册表,视为未配置(降级关闭)", self._fallback_name)

    async def aclose(self) -> None:
        """021① 清偿:显式关闭全部客户端(主/注册表池/embedding)——
        deps 指纹变更重建时调用,连接关闭不再依赖 GC 时机。"""
        clients: list[AsyncOpenAI | AnthropicClient] = []
        if self._client is not None:
            clients.append(self._client)
        clients.extend(c for c, _, _ in self._pool.values())
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

    def context_window(self, task: str | None = None) -> int:
        """080:该 task 生效模型的上下文窗口——路由命中条目声明值优先,
        未声明回退全局缺省(窗口只用于装配预算,不出现在任何响应中)。"""
        if task:
            name = self._routes.get(task)
            if name and name in self._pool_windows:
                return self._pool_windows[name]
        return self._default_window

    def prompt_budget(self, task: str | None = None) -> int:
        """080:送模型前的提示词 token 预算 = 窗口 − 预留输出 − 安全边际。"""
        return max(1024, self.context_window(task) - self._budget_reserve)

    def _resolve(
        self, task: str | None, model_override: str | None = None
    ) -> tuple[AsyncOpenAI | AnthropicClient | None, str, str]:
        """任务路由(021,单点):路由表命中注册表→该客户端;否则主模型零回归。
        025:model_override(Bot 配置)优先于任务路由;注册表未命中告警回退;
        R1-M1:路由目标不在注册表时告警一次(按 task+name 去重防刷屏)。
        多协议(ADR-008):返回 (client, model, protocol)——openai/responses 条目
        持 AsyncOpenAI 客户端,anthropic 条目持 AnthropicClient。"""
        if model_override:
            if model_override in self._pool:
                client, model, protocol = self._pool[model_override]
                return client, model, protocol
            marker = ("__override__", model_override)
            if marker not in _ROUTE_MISS_WARNED:
                _ROUTE_MISS_WARNED.add(marker)
                log.warning("Bot 模型覆盖 %s 不在注册表,回退任务路由/主模型", model_override)
        if task:
            name = self._routes.get(task)
            if name:
                if name in self._pool:
                    client, model, protocol = self._pool[name]
                    return client, model, protocol
                marker = (task, name)
                if marker not in _ROUTE_MISS_WARNED:
                    _ROUTE_MISS_WARNED.add(marker)
                    log.warning("任务 %s 路由目标 %s 不在注册表,回退主模型", task, name)
        return self._client, self._model, self._protocol

    def _degrade_chain(self, task: str | None, model_override: str | None) -> list[ResolvedTarget]:
        """072 降级链(design D3):主目标(路由/override)→ 主模型(若不同)→
        fallback 条目(若配置且不重复)。Bot model_override 不降级(D7:
        显式选择的模型与计价不容被静默替换)。"""
        primary = self._resolve(task, model_override)
        if model_override:
            return [primary]
        chain: list[ResolvedTarget] = [primary]
        main: ResolvedTarget = (
            self._client,
            self._model,
            self._protocol,
        )
        if not _same_target(primary, main):
            chain.append(main)
        if self._fallback_name:
            fallback = self._pool.get(self._fallback_name)
            if fallback is not None and not any(
                _same_target(t, cast("ResolvedTarget", fallback)) for t in chain if t[0] is not None
            ):
                chain.append(fallback)
        return chain

    async def _resilient_call(
        self,
        *,
        task: str | None,
        model_override: str | None,
        trace_id: str | None,
        tenant_key: str | None,
        call_target: Callable[[AsyncOpenAI | AnthropicClient, str, str], Awaitable[Any]],
    ) -> Any:
        """072 韧性单点:链上目标依次尝试,瞬时故障按退避重发,耗尽才降级;
        主目标分得预算大头(总预算 − 下游目标各保留 1 次),下游不再重试。
        用量每尝试一条由 call_target 内部自报(实际模型名即该次尝试的模型)。"""
        chain = self._degrade_chain(task, model_override)
        primary_client = chain[0][0]
        if primary_client is None:
            raise LlmError("KM_AGENT_LLM_API_KEY 未配置")
        # 下游目标客户端 None(未配 key 的池条目不可能出现;主模型 None = 全局无 key,
        # 已被上面的 primary 检查覆盖)——防御性过滤
        usable: list[Target] = [cast("Target", chain[0])] + [
            cast("Target", t) for t in chain[1:] if t[0] is not None
        ]
        primary_attempts = max(1, self._max_attempts - (len(usable) - 1))
        last_exc: BaseException | None = None
        for idx, (_client, model, protocol) in enumerate(usable):
            attempts = primary_attempts if idx == 0 else 1
            try:
                return await call_with_retry(
                    partial(call_target, _client, model, protocol),
                    max_attempts=attempts,
                    base_delay_ms=self._retry_base_ms,
                    max_wait_ms=self._retry_max_wait_ms,
                )
            except Exception as e:  # noqa: BLE001  分类后决定降级或上抛
                if idx + 1 < len(usable) and is_transient_error(e):
                    log.warning(
                        "LLM 出站目标 %s(%s) 瞬时故障耗尽(尝试 %d 次),降级下一目标: %s",
                        model,
                        protocol,
                        attempts,
                        e,
                    )
                    last_exc = e
                    continue
                raise
        assert last_exc is not None  # noqa: S101  循环必已 return 或 raise(类型收窄)
        raise last_exc

    async def _protocol_create(
        self,
        client: AsyncOpenAI | AnthropicClient,
        protocol: str,
        *,
        model: str,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]],
        temperature: float,
        max_tokens: int | None = None,
    ) -> ProtocolReply:
        """anthropic / responses 协议出站单点(ADR-008 设计决策 2):翻译与
        错误映射收敛在 llm_protocol,此处只选路;usage 归一 ProtocolReply.usage
        回 _report。openai 协议不经此函数(既有路径零回归)。"""
        if protocol == PROTOCOL_ANTHROPIC:
            if not isinstance(client, AnthropicClient):
                raise LlmError("anthropic 协议条目的客户端形态异常(配置/池不一致)")
            # temperature 语义不适用于 anthropic(SDK 1.8.0 类型面已移除该参数)
            return await client.create(
                model=model, messages=messages, tools=tools, max_tokens=max_tokens
            )
        if protocol == PROTOCOL_RESPONSES:
            if not isinstance(client, AsyncOpenAI):
                raise LlmError("responses 协议条目的客户端形态异常(配置/池不一致)")
            return await responses_create(
                client,
                model=model,
                messages=messages,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        raise LlmError(f"未知出站协议: {protocol}")

    async def _chat_via_protocol(
        self,
        client: AsyncOpenAI | AnthropicClient,
        protocol: str,
        *,
        model: str,
        system: str,
        user: object,  # str(chat)或 vision content-parts 列表
        temperature: float,
        max_tokens: int | None,
        task: str,
        trace_id: str | None,
        tenant_key: str | None,
    ) -> str:
        """anthropic / responses 条目的 chat / chat_vision 通道(ADR-008):
        翻译出站 → usage 归一回 _report;调用失败/应答畸形收敛 LlmError 并按
        既有 ERROR 采集口径落记录(038)。072:每次尝试一条 usage(由外层重试
        循环逐次调用本函数,模型名取当次目标)。"""
        messages: list[dict[str, object]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        t0 = time.monotonic()
        try:
            reply = await self._protocol_create(
                client,
                protocol,
                model=model,
                messages=messages,
                tools=[],
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except Exception as e:
            self._report(
                task=task,
                model=model,
                trace_id=trace_id,
                tenant_key=tenant_key,
                usage=None,
                status="ERROR",
                latency_ms=int((time.monotonic() - t0) * 1000),
                protocol=protocol,
            )
            raise LlmError(f"LLM 调用失败: {e}") from e
        self._report(
            task=task,
            model=model,
            trace_id=trace_id,
            tenant_key=tenant_key,
            usage=reply.usage,
            latency_ms=int((time.monotonic() - t0) * 1000),
            protocol=protocol,
        )
        return reply.content or ""

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
        protocol: str = DEFAULT_PROTOCOL,  # 077:出站协议归因(三通道各透传)
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
            "protocol": protocol,  # 077:api 侧落库(内部契约,不进 OpenAPI)
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

        async def call_target(
            client: AsyncOpenAI | AnthropicClient,
            model: str,
            protocol: str,
        ) -> str:
            if protocol != PROTOCOL_OPENAI:
                return await self._chat_via_protocol(
                    client,
                    protocol,
                    model=model,
                    system=system,
                    user=user,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    task=task or "CHAT",
                    trace_id=trace_id,
                    tenant_key=tenant_key,
                )
            # protocol 分支已保证 openai 通道持 AsyncOpenAI(或测试替身);cast 仅收窄
            oai = cast("AsyncOpenAI", client)
            # max_tokens 缺省不传(供应商默认),仅轻量端点(如标题改写)显式封顶(010);
            # 分支写法:openai 类型 None ≠ 省略,**kwargs 过不了 mypy 重载
            messages: list[ChatCompletionMessageParam] = [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ]
            t0 = time.monotonic()
            try:
                if max_tokens is None:
                    resp = await oai.chat.completions.create(
                        model=model, messages=messages, temperature=temperature
                    )
                else:
                    resp = await oai.chat.completions.create(
                        model=model,
                        messages=messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                    )
            except Exception:
                # 072:每尝试一条 ERROR 记录,随后原异常上抛(分类/降级/重试由
                # _resilient_call 统一裁决)——最终失败仍收敛 LlmError(见下)
                self._report(
                    task=task or "CHAT",
                    model=model,
                    trace_id=trace_id,
                    tenant_key=tenant_key,
                    usage=None,
                    status="ERROR",
                    latency_ms=int((time.monotonic() - t0) * 1000),
                    protocol=PROTOCOL_OPENAI,
                )
                raise
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
                    protocol=PROTOCOL_OPENAI,
                )
                raise LlmError(f"LLM 应答畸形: {e}") from e
            self._report(
                task=task or "CHAT",
                model=model,
                trace_id=trace_id,
                tenant_key=tenant_key,
                usage=resp.usage,
                latency_ms=int((time.monotonic() - t0) * 1000),
                protocol=PROTOCOL_OPENAI,
            )
            return content

        try:
            return cast(
                "str",
                await self._resilient_call(
                    task=task,
                    model_override=None,
                    trace_id=trace_id,
                    tenant_key=tenant_key,
                    call_target=call_target,
                ),
            )
        except LlmError:
            raise
        except Exception as e:
            raise LlmError(f"LLM 调用失败: {e}") from e

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

        async def call_target(
            client: AsyncOpenAI | AnthropicClient,
            model: str,
            protocol: str,
        ) -> str:
            if protocol != PROTOCOL_OPENAI:
                content: list[dict[str, object]] = [{"type": "text", "text": user}]
                for mime, b64 in images:
                    content.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{b64}"},
                        }
                    )
                return await self._chat_via_protocol(
                    client,
                    protocol,
                    model=model,
                    system=system,
                    user=content,
                    temperature=0.2,
                    max_tokens=None,
                    task=task or "VISION",
                    trace_id=trace_id,
                    tenant_key=tenant_key,
                )
            # protocol 分支已保证 openai 通道持 AsyncOpenAI(或测试替身);cast 仅收窄
            oai = cast("AsyncOpenAI", client)
            content_parts: list[dict[str, object]] = [{"type": "text", "text": user}]
            for mime, b64 in images:
                content_parts.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}"},
                    }
                )
            messages: list[ChatCompletionMessageParam] = [
                {"role": "system", "content": system},
                {"role": "user", "content": content_parts},  # type: ignore[list-item,misc]
            ]
            t0 = time.monotonic()
            try:
                resp = await oai.chat.completions.create(
                    model=model, messages=messages, temperature=0.2
                )
            except Exception:
                self._report(
                    task=task or "VISION",
                    model=model,
                    trace_id=trace_id,
                    tenant_key=tenant_key,
                    usage=None,
                    status="ERROR",
                    latency_ms=int((time.monotonic() - t0) * 1000),
                    protocol=PROTOCOL_OPENAI,
                )
                raise
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
                    protocol=PROTOCOL_OPENAI,
                )
                raise LlmError(f"vision 应答畸形: {e}") from e
            self._report(
                task=task or "VISION",
                model=model,
                trace_id=trace_id,
                tenant_key=tenant_key,
                usage=resp.usage,
                latency_ms=int((time.monotonic() - t0) * 1000),
                protocol=PROTOCOL_OPENAI,
            )
            return text

        try:
            return cast(
                "str",
                await self._resilient_call(
                    task=task,
                    model_override=None,
                    trace_id=trace_id,
                    tenant_key=tenant_key,
                    call_target=call_target,
                ),
            )
        except LlmError:
            raise
        except Exception as e:
            raise LlmError(f"vision 调用失败: {e}") from e

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
        072:model_override 目标瞬时可重试但不降级(D7)。
        """

        async def call_target(
            client: AsyncOpenAI | AnthropicClient,
            model: str,
            protocol: str,
        ) -> ToolChatReply:
            if protocol != PROTOCOL_OPENAI:
                # 多协议(ADR-008):消息史原样翻译出站,应答回 ProtocolReply → 既有返回结构
                protocol_messages: list[dict[str, object]] = [
                    {"role": "system", "content": system},
                    *[dict(m) for m in messages],
                ]
                t0 = time.monotonic()
                try:
                    reply = await self._protocol_create(
                        client,
                        protocol,
                        model=model,
                        messages=protocol_messages,
                        tools=tools,
                        temperature=temperature,
                        max_tokens=None,
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
                        protocol=protocol,
                    )
                    raise LlmError(f"LLM 调用失败: {e}") from e
                self._report(
                    task=task or "CHAT_TOOLS",
                    model=model,
                    trace_id=trace_id,
                    tenant_key=tenant_key,
                    usage=reply.usage,
                    latency_ms=int((time.monotonic() - t0) * 1000),
                    protocol=protocol,
                )
                return ToolChatReply(content=reply.content, tool_calls=reply.tool_calls)
            # protocol 分支已保证 openai 通道持 AsyncOpenAI(或测试替身);cast 仅收窄
            oai = cast("AsyncOpenAI", client)
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
                    resp = await oai.chat.completions.create(
                        model=model,
                        messages=full_messages,
                        temperature=temperature,
                        tools=cast("list[ChatCompletionFunctionToolParam]", tools),
                        tool_choice="auto",
                    )
                else:
                    resp = await oai.chat.completions.create(
                        model=model, messages=full_messages, temperature=temperature
                    )
            except Exception:
                self._report(
                    task=task or "CHAT_TOOLS",
                    model=model,
                    trace_id=trace_id,
                    tenant_key=tenant_key,
                    usage=None,
                    status="ERROR",
                    latency_ms=int((time.monotonic() - t0) * 1000),
                    protocol=PROTOCOL_OPENAI,
                )
                raise
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
                    protocol=PROTOCOL_OPENAI,
                )
                raise LlmError(f"LLM 应答畸形: {e}") from e
            self._report(
                task=task or "CHAT_TOOLS",
                model=model,
                trace_id=trace_id,
                tenant_key=tenant_key,
                usage=resp.usage,
                latency_ms=int((time.monotonic() - t0) * 1000),
                protocol=PROTOCOL_OPENAI,
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

        try:
            return cast(
                "ToolChatReply",
                await self._resilient_call(
                    task=task,
                    model_override=model_override,
                    trace_id=trace_id,
                    tenant_key=tenant_key,
                    call_target=call_target,
                ),
            )
        except LlmError:
            raise
        except Exception as e:
            raise LlmError(f"LLM 调用失败: {e}") from e

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """批量向量化(009;openai 兼容 embeddings API)。

        供应商单批条数受限(dashscope text-embedding-v2=25,冒烟实证 28 条整批被拒),
        内部按 embedding_batch_size 切分多次调用后拼接。
        072:每批独立重试预算(瞬时故障),每尝试一条 usage。
        """
        if self._emb_client is None:
            raise LlmError("embedding/LLM API key 均未配置")
        if not texts or len(texts) > self._emb_max_texts:
            raise LlmError(f"embeddings 批量须为 1..{self._emb_max_texts} 条")
        out: list[list[float]] = []
        for start in range(0, len(texts), self._emb_batch_size):
            batch = texts[start : start + self._emb_batch_size]

            async def call_emb(batch: list[str] = batch) -> CreateEmbeddingResponse:
                t0 = time.monotonic()
                try:
                    return await self._emb_client.embeddings.create(  # type: ignore[union-attr]
                        model=self._emb_model, input=batch, dimensions=self._emb_dimensions
                    )
                except Exception:
                    self._report(
                        task="EMBED",
                        model=self._emb_model,
                        trace_id=None,
                        usage=None,
                        status="ERROR",
                        latency_ms=int((time.monotonic() - t0) * 1000),
                        protocol=PROTOCOL_OPENAI,
                    )
                    raise

            try:
                resp = await call_with_retry(
                    call_emb,
                    max_attempts=self._max_attempts,
                    base_delay_ms=self._retry_base_ms,
                    max_wait_ms=self._retry_max_wait_ms,
                )
            except Exception as e:
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
                    latency_ms=0,
                    protocol=PROTOCOL_OPENAI,
                )
                raise LlmError(f"embeddings 应答畸形: 期望 {len(batch)} 条实得 {len(data)}")
            # 021:embedding 用量同通道上报(不参与模型路由,009 立约)
            self._report(
                task="EMBED",
                model=self._emb_model,
                trace_id=None,
                usage=getattr(resp, "usage", None),
                latency_ms=0,
                protocol=PROTOCOL_OPENAI,
            )  # tenant 维度不适用(009 embedding 独立通道)
            # 维度校验(design D1):模型默认维度不作契约,供应商少传/漂移
            # dimensions 时显式报错,不把错维度向量放行给下游落库
            for d in data:
                if len(d.embedding) != self._emb_dimensions:
                    self._report(
                        task="EMBED",
                        model=self._emb_model,
                        trace_id=None,
                        usage=None,
                        status="ERROR",
                        latency_ms=0,
                        protocol=PROTOCOL_OPENAI,
                    )
                    raise LlmError(
                        f"embeddings 维度不符(期望 {self._emb_dimensions},实际 {len(d.embedding)})"
                    )
            out.extend(d.embedding for d in data)
        return out
