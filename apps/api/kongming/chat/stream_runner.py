"""流式问答的后台消费与中继:agent 流 → 帧队列(SSE 契约的唯一产出点)。

帧序契约:过程帧(stage/sql/summary/tool_call/trace)按 agent 到达顺序透传;
每轮恰一个终态帧(result 或 error);终止标记 None 最后入队。任何退出路径都
必须入队 None——中继生成器靠它收束,缺失即前端永久等心跳。
"""

import asyncio
import json
import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any, cast

from kongming.chat.agent_client import AgentClient, AgentReply, AgentUnavailable, parse_agent_reply
from kongming.chat.finalize import AGENT_UNAVAILABLE_TEXT, ReplyFinalizer, persist_unavailable
from kongming.chat.ports import ChatRepo, StreamStoreFactory
from kongming.chat.results import ChatOutcome
from kongming.chat.stage_trace import StageTraceCollector

log = logging.getLogger(__name__)

# 过程事件白名单(事件名/形状由 api 收口;trace=诊断信号通道,不进用户响应只作观测)
_PASSTHROUGH_EVENTS = frozenset({"stage", "sql", "summary", "tool_call", "trace"})

FrameQueue = asyncio.Queue[tuple[str, object] | None]


@dataclass(frozen=True)
class PreparedQuery:
    """问答前段产物(同步/流式共用同一编排口径)。"""

    tenant_id: str
    resolved_key: str
    sid: str | None
    effective_history: list[str]
    trace_id: str
    user_id: str = ""  # 护栏审计主体
    attachments: list[dict[str, object]] | None = None  # 附件摘要(归属已验)
    last_fallback: str = ""  # 上一轮兜底分类(澄清防循环判据;空=不携带)


@dataclass
class StreamHandle:
    """流式中继句柄:queue=待发帧,event 为 result 时 payload=ChatOutcome;
    task=后台消费任务(断线后照常跑完,调用方不 await)。"""

    queue: asyncio.Queue[tuple[str, object] | None]
    task: "asyncio.Task[None]"


# 后台流任务强引用(防 GC 边角;完成即摘)
_BACKGROUND_STREAM_TASKS: set["asyncio.Task[None]"] = set()


def spawn_background(coro: "Coroutine[Any, Any, Any]") -> "asyncio.Task[Any]":
    """创建后台任务并强引用到完成(防 GC)。"""
    task = asyncio.create_task(coro)
    _BACKGROUND_STREAM_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_STREAM_TASKS.discard)
    return task


async def noop() -> None:
    """空任务占位(短路轮句柄形态与正常轮一致:task 非 None 且已完成)。"""


class AgentStreamConsumer:
    """后台消费 agent 流至终态:过程帧透传;result 帧先落库(独立 StreamStore
    会话)再富化入队;传输失败收敛 error 终态+FAILED 落库。
    前端断线不影响本任务(断线续存);落库失败不阻断终态帧(与同步容错一致)。"""

    def __init__(
        self,
        *,
        agent: AgentClient,
        stream_store_factory: StreamStoreFactory | None,
        finalizer: ReplyFinalizer,
        repo: ChatRepo,
        implicit_memory_task: "Callable[..., Coroutine[Any, Any, None]] | None" = None,
    ) -> None:
        self._agent = agent
        self._stream_store_factory = stream_store_factory
        self._finalizer = finalizer
        self._repo = repo
        self._implicit_memory_task = implicit_memory_task

    def begin(
        self,
        p: PreparedQuery,
        *,
        user_id: str,
        question: str,
        orig_question: str,
        persist: bool,
        datasource_key: str,
        react_tools: list[dict[str, object]] | None = None,
        eff_history: list[str] | None = None,
        memory_hint: str = "",
        pref_revision: int | None = None,
        attachment_ids: list[str] | None = None,
    ) -> StreamHandle:
        """起后台消费任务并返回句柄(任务持强引用,断线不取消)。"""
        queue: asyncio.Queue[tuple[str, object] | None] = asyncio.Queue()
        task = spawn_background(
            self.consume(
                p,
                user_id=user_id,
                question=question,
                orig_question=orig_question,
                persist=persist,
                datasource_key=datasource_key,
                queue=queue,
                react_tools=react_tools,
                attachment_ids=attachment_ids,
                eff_history=eff_history,
                memory_hint=memory_hint,
                pref_revision=pref_revision,
            )
        )
        return StreamHandle(queue=queue, task=task)

    async def consume(
        self,
        p: PreparedQuery,
        *,
        user_id: str,
        question: str,
        orig_question: str = "",  # 回写原始问句(缺省=未脱敏同值)
        persist: bool,
        datasource_key: str,
        queue: "asyncio.Queue[tuple[str, object] | None]",
        react_tools: list[dict[str, object]] | None = None,
        eff_history: list[str] | None = None,  # 护栏脱敏后历史(缺省=prepare 原值)
        memory_hint: str = "",  # 记忆注入段
        pref_revision: int | None = None,  # 准备期人工版本快照(透传终态任务)
        attachment_ids: list[str] | None = None,
    ) -> None:
        """消费 agent 流并产出帧;异常收敛铁律:任何退出路径都入队终止标记(见模块说明)。"""
        await self._run(
            _StreamRun(
                p=p,
                user_id=user_id,
                question=question,
                orig_question=orig_question,
                persist=persist,
                datasource_key=datasource_key,
                queue=queue,
                react_tools=react_tools,
                eff_history=eff_history,
                memory_hint=memory_hint,
                pref_revision=pref_revision,
                attachment_ids=attachment_ids,
            )
        )

    async def _run(self, run: "_StreamRun") -> None:
        """执行单轮消费:转发过程帧、收敛终态、flush 后必达终止标记。"""
        terminal = _TerminalState()

        async def on_frame(event: str, data_json: str) -> None:
            if event == "result":
                await terminal.on_result(
                    self,
                    data_json,
                    p=run.p,
                    question=run.orig_question or run.question,  # 原始问句(回写口径)
                    persist=run.persist,
                    datasource_key=run.datasource_key,
                    pref_revision=run.pref_revision,
                    is_react=run.react_tools is not None,
                    stage_trace=run.collector.payload() or [],
                )
            elif event == "error":
                # agent 意外异常帧:等价传输层失败,由 api 统一收敛(不透传)
                terminal.error_text = AGENT_UNAVAILABLE_TEXT
            elif event in _PASSTHROUGH_EVENTS:
                await _forward_frame(event, data_json, run.queue, run.collector)

        try:
            await self._agent.query_stream(
                trace_id=run.p.trace_id,
                tenant_key=run.p.resolved_key,
                question=run.question,
                user_id=run.user_id,
                history=(
                    run.eff_history if run.eff_history is not None else run.p.effective_history
                ),
                datasource_key=run.datasource_key,
                on_frame=on_frame,
                react_tools=run.react_tools,
                memory_hint=run.memory_hint,
                attachment_ids=run.attachment_ids,
                last_fallback=run.p.last_fallback,
            )
        except AgentUnavailable:
            log.warning("agent 流式不可达 trace_id=%s", run.p.trace_id)
            terminal.error_text = AGENT_UNAVAILABLE_TEXT
        except Exception:
            # 响应格式错误或持久化异常也要写入 error 终态，避免前端一直等待。
            log.exception("流式消费任务异常 trace_id=%s", run.p.trace_id)
            terminal.error_text = AGENT_UNAVAILABLE_TEXT
        try:
            await terminal.flush(
                self,
                run.p,
                datasource_key=run.datasource_key,
                queue=run.queue,
                stage_trace=run.collector.payload() or [],
            )
        finally:
            await run.queue.put(None)  # 终止标记(生成器据此收束;必达)

    async def finalize_stream(
        self,
        *,
        reply: AgentReply,
        p: PreparedQuery,
        question: str,
        persist: bool,
        datasource_key: str,
        pref_revision: int | None = None,  # 准备期人工版本快照
        is_react: bool = False,  # ReAct 链路轮不评分(spec 评分范围)
        stage_trace: list[dict[str, object]] | None = None,  # 节点轨迹(流式轮)
    ) -> ChatOutcome:
        """流式终态落库(独立 StreamStore 会话;断线后照常执行)+标题任务触发。"""
        title_trigger: Callable[[str], Coroutine[Any, Any, None]] | None = None
        if self._stream_store_factory is not None:
            async with self._stream_store_factory() as store:
                outcome = await self._finalizer.finalize(
                    reply=reply,
                    sid=p.sid,
                    tenant_id=p.tenant_id,
                    datasource_key=datasource_key,
                    question=question,
                    persist=persist,
                    repo=store.repo,
                    history_writer=store.history_writer,
                    trace_id=p.trace_id,
                    attachments=p.attachments,
                    user_id=p.user_id,
                    is_react=is_react,
                    stage_trace=stage_trace,
                )
                title_trigger = store.trigger_title_rewrite
        else:  # 组合根未接线(部署缺陷):终态仍要回给前端,仅放弃断线持久化
            log.error("stream_store_factory 未注入,流式终态不落库")
            return await self._finalizer.finalize(
                reply=reply,
                sid=None,
                tenant_id=p.tenant_id,
                datasource_key=datasource_key,
                question=question,
                persist=False,
                repo=self._repo,
                history_writer=None,
                trace_id=p.trace_id,
                user_id=p.user_id,
                attachments=p.attachments,  # 兜底分支与主分支同源
                stage_trace=stage_trace,
            )
        if (
            title_trigger is not None
            and outcome.success
            and persist
            and outcome.session_id is not None
        ):
            # 标题任务触发条件与同步端点一致;result 帧已可先发,这里不阻塞
            spawn_background(title_trigger(outcome.session_id))
            self._spawn_implicit_memory(
                outcome, p=p, question=question, pref_revision=pref_revision
            )
        return outcome

    def _spawn_implicit_memory(
        self,
        outcome: ChatOutcome,
        *,
        p: PreparedQuery,
        question: str,
        pref_revision: int | None,
    ) -> None:
        """隐式记忆任务(流式同挂;显式短路轮排除;捕获失败(None)不挂任务)。"""
        # 任务经注入传入——service 不得 import memory_tasks,违反零框架契约
        if (
            not outcome.tenant_id
            or outcome.memory_shortcut
            or pref_revision is None
            or self._implicit_memory_task is None
        ):
            return
        spawn_background(
            self._implicit_memory_task(
                user_id=p.user_id,
                tenant_id=outcome.tenant_id,
                question=question,
                answer=outcome.summary,
                agent=self._agent,
                pref_revision=pref_revision,
            )
        )

    async def persist_stream_failure(
        self,
        p: PreparedQuery,
        *,
        datasource_key: str = "default",
        stage_trace: list[dict[str, object]] | None = None,  # 已收集轨迹如实落库
    ) -> None:
        """流式传输层失败的 FAILED 落库(独立会话);失败仅日志(不阻断 error 帧)。"""
        if self._stream_store_factory is None:
            log.error("stream_store_factory 未注入,FAILED 终态不落库")
            return
        try:
            async with self._stream_store_factory() as store:
                await persist_unavailable(
                    store.repo,
                    sid=p.sid,
                    datasource_key=datasource_key,
                    attachments=p.attachments,
                    stage_trace=stage_trace,
                )
        except Exception:  # noqa: BLE001  # 断线续存落库兜底:不阻断终态帧下发
            log.warning("流式 FAILED 终态落库异常 session=%s", p.sid, exc_info=True)


@dataclass(frozen=True)
class _StreamRun:
    """单轮流式消费的全部入参(帧回调闭包据此捕获,避免逐个闭包变量)。"""

    p: PreparedQuery
    user_id: str
    question: str
    orig_question: str
    persist: bool
    datasource_key: str
    queue: "asyncio.Queue[tuple[str, object] | None]"
    react_tools: list[dict[str, object]] | None
    eff_history: list[str] | None
    memory_hint: str
    pref_revision: int | None
    attachment_ids: list[str] | None
    collector: StageTraceCollector = field(default_factory=StageTraceCollector)


class _TerminalState:
    """单轮终态收集:首个 result 即权威(幂等守卫),否则按传输失败收敛。"""

    def __init__(self) -> None:
        self.outcome: ChatOutcome | None = None
        self.error_text: str | None = None

    async def on_result(
        self,
        agent_sink: "AgentStreamConsumer",
        data_json: str,
        *,
        p: PreparedQuery,
        question: str,
        persist: bool,
        datasource_key: str,
        pref_revision: int | None,
        is_react: bool,
        stage_trace: list[dict[str, object]],
    ) -> None:
        """解析并落库首个 result 帧;重复帧/解析失败只记日志不改变终态。"""
        if self.outcome is not None:
            # 终态幂等守卫:契约恰一个终态帧,首个即权威——
            # 违约多发的 result 不得二次落库/回写/触发标题任务
            log.warning("agent 流重复 result 帧(忽略) trace_id=%s", p.trace_id)
            return
        try:
            reply = parse_agent_reply(json.loads(data_json))
        except (ValueError, TypeError, AttributeError) as e:
            log.warning("agent result 帧解析失败: %s", e)
            self.error_text = AGENT_UNAVAILABLE_TEXT
            return
        self.outcome = await agent_sink.finalize_stream(
            reply=reply,
            p=p,
            question=question,
            persist=persist,
            datasource_key=datasource_key,
            pref_revision=pref_revision,
            is_react=is_react,
            stage_trace=stage_trace,
        )

    async def flush(
        self,
        agent_sink: "AgentStreamConsumer",
        p: PreparedQuery,
        *,
        datasource_key: str,
        queue: "asyncio.Queue[tuple[str, object] | None]",
        stage_trace: list[dict[str, object]],
    ) -> None:
        """按终态入队唯一的终态帧(result 或 error);FAILED 先落库再入队。"""
        if self.outcome is not None:
            # 业务终态已落库(result 先finalize):即使读尾传输异常也按成功下发,
            # 不叠加 FAILED 消息(双落库收敛)
            await queue.put(("result", self.outcome))
            return
        if self.error_text is None:
            # agent 流正常结束但无终态帧(契约破坏):与不可达同收敛
            log.error("agent 流结束但缺终态帧 trace_id=%s", p.trace_id)
            self.error_text = AGENT_UNAVAILABLE_TEXT
        # 传输层/意外失败终态:FAILED 落库(独立会话)+ error 帧
        await agent_sink.persist_stream_failure(
            p, datasource_key=datasource_key, stage_trace=stage_trace
        )
        await queue.put(("error", {"error": self.error_text, "reason_code": None}))


async def _forward_frame(
    event: str,
    data_json: str,
    queue: "asyncio.Queue[tuple[str, object] | None]",
    collector: StageTraceCollector,
) -> None:
    """过程帧透传:单帧畸形跳过不中断;stage 帧同时采集节点轨迹。"""
    try:
        data = json.loads(data_json)
    except ValueError:
        log.warning("agent 过程帧解析失败(跳帧): %s", data_json[:100])
        return
    if not isinstance(data, dict):
        return
    # isinstance 收窄产物键型未知,cast 对齐队列元素类型(运行时不变)
    typed = cast("dict[str, object]", data)
    if event == "stage":
        collector.add_stage(typed)  # 轨迹随行(与透传并行)
    await queue.put((event, typed))
