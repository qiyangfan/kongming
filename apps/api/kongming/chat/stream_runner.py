"""流式问答的后台消费与中继:agent 流 → 公开事件队列(SSE 契约的唯一产出点)。

帧序契约:agent 过程帧(stage/sql/summary/tool_call)经形状校验后以信封事件
{"type","data"} 入队;诊断 trace 不出公开流;每轮恰一个终态(result=ChatOutcome
或失败标记);终止标记 None 最后入队。任何退出路径都必须入队 None——中继
生成器靠它收束,缺失即前端永久等心跳。

被请求取消的轮由本任务收敛为已取消终态(query-streaming spec「取消的执行效果与终态」)。
收尾(落库 + 终态帧)一旦开始就不被请求取消打断:一轮只落定一个终态。
"""

import asyncio
import json
import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any, Literal, cast

from pydantic import TypeAdapter, ValidationError

from kongming.chat.agent_client import AgentClient, AgentReply, AgentUnavailable, parse_agent_reply
from kongming.chat.finalize import (
    AGENT_UNAVAILABLE_TEXT,
    TURN_CANCELLED_TEXT,
    ReplyFinalizer,
    persist_cancelled,
    persist_unavailable,
)
from kongming.chat.inflight import InflightTurn, TurnSettlement
from kongming.chat.ports import (
    ChatRepo,
    StreamStoreFactory,
    TurnFederation,
    TurnRegistry,
    VoiceTurnBinding,
)
from kongming.chat.results import ChatOutcome
from kongming.chat.schemas import (
    StreamSqlEvent,
    StreamStageEvent,
    StreamSummaryEvent,
    StreamToolCallEvent,
)
from kongming.chat.stage_trace import StageTraceCollector

log = logging.getLogger(__name__)

# 过程事件白名单(trace=诊断信号,只存在于 agent 内部流,不出公开面)
_PROCESS_EVENTS = frozenset({"stage", "sql", "summary", "tool_call"})

_PROCESS_ADAPTER: TypeAdapter[
    StreamStageEvent | StreamSqlEvent | StreamSummaryEvent | StreamToolCallEvent
] = TypeAdapter(StreamStageEvent | StreamSqlEvent | StreamSummaryEvent | StreamToolCallEvent)


@dataclass(frozen=True)
class StreamFailure:
    """传输/意外失败标记:发送端据此下发预制的 AGENT_UNAVAILABLE 错误结尾。"""

    reason_code: Literal["AGENT_UNAVAILABLE"] = "AGENT_UNAVAILABLE"


# 出站帧:已校验的过程事件、领域终态、失败标记;None=终止标记
OutboundFrame = (
    StreamStageEvent
    | StreamSqlEvent
    | StreamSummaryEvent
    | StreamToolCallEvent
    | ChatOutcome
    | StreamFailure
)

type FrameQueue = asyncio.Queue[OutboundFrame | None]


def voice_turn_outcome(o: ChatOutcome) -> dict[str, object]:
    """语音轮终态回填给关联字段注册表的结果形状(voice 侧状态机消费 verdict/
    summary/columns 等键;短路轮与后台消费轮共用,防两处形状漂移)。"""
    return {
        "verdict": o.verdict,
        "success": o.success,
        "summary": o.summary,
        "sql": o.sql,
        "columns": o.columns,
        "rows": o.rows,
    }


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
    user_message_id: str | None = None  # 本轮用户消息 id(已取消行按它定位原轮时序)


@dataclass
class StreamHandle:
    """流式中继句柄:queue=已校验过程事件或终态元素(ChatOutcome/StreamFailure);
    task=后台消费任务(断线后照常跑完,调用方不 await)。"""

    queue: "asyncio.Queue[OutboundFrame | None]"
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
    """后台消费 agent 流至终态:过程事件按公开模型校验后入队;result 帧先落库
    (独立 StreamStore 会话)再富化入队;传输失败收敛失败标记+FAILED 落库。
    前端断线不影响本任务(断线续存);落库失败不阻断终态帧(与同步容错一致)。
    在途轮经登记端口登记,取消发起方据此找到并收敛本轮。"""

    def __init__(
        self,
        *,
        agent: AgentClient,
        stream_store_factory: StreamStoreFactory | None,
        finalizer: ReplyFinalizer,
        repo: ChatRepo,
        implicit_memory_task: "Callable[..., Coroutine[Any, Any, None]] | None" = None,
        turns: TurnRegistry | None = None,
        federation: TurnFederation | None = None,
        voice_binding: "VoiceTurnBinding | None" = None,
    ) -> None:
        self._agent = agent
        self._stream_store_factory = stream_store_factory
        self._finalizer = finalizer
        self._repo = repo
        self._implicit_memory_task = implicit_memory_task
        self._turns = turns
        self._federation = federation
        self._voice_binding = voice_binding

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
        queue: asyncio.Queue[OutboundFrame | None] = asyncio.Queue()
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
        queue: FrameQueue,
        react_tools: list[dict[str, object]] | None = None,
        eff_history: list[str] | None = None,  # 护栏脱敏后历史(缺省=prepare 原值)
        memory_hint: str = "",  # 记忆注入段
        pref_revision: int | None = None,  # 准备期人工版本快照(透传终态任务)
        attachment_ids: list[str] | None = None,
    ) -> None:
        """消费 agent 流并产出帧;异常收敛铁律:任何退出路径都入队终止标记(见模块说明)。"""
        run = _StreamRun(
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
        await self._register(run)
        try:
            await self._run(run, run.registration.turn)
        except asyncio.CancelledError:
            if run.registration.turn is None or not run.registration.turn.cancel_requested:
                raise  # 被外部取消且无人请求取消(进程关停等):原样上抛,不伪造终态
            # 请求取消的硬取消落在流阶段:收敛为已取消(收敛段不被后续取消打断)
            await _await_settle(
                asyncio.create_task(self._settle(run, run.registration.turn)),
                run.registration.turn,
            )
            raise
        finally:
            await run.queue.put(None)  # 终止标记(生成器据此收束;必达)
            if run.registration.turn is not None and self._turns is not None:
                self._turns.unregister(run.registration.turn)
            await self._release(run)

    async def _release(self, run: "_StreamRun") -> None:
        """跨实例登记的注销(凭据在登记产物上;无凭据即无外部状态要清)。"""
        if self._federation is not None and run.registration.federation_token is not None:
            await self._federation.release(
                trace_id=run.p.trace_id, token=run.registration.federation_token
            )

    async def _register(self, run: "_StreamRun") -> None:
        """本地 + 跨实例两级在途登记,产物记在 run.registration(句柄与注销凭据)。"""
        if self._turns is not None:
            # 登记以「用户 + trace_id」为键:入站 trace_id 可由调用方指定,他人同标识不得命中
            run.registration.turn = InflightTurn(
                user_id=run.user_id,
                trace_id=run.p.trace_id,
                task=cast("asyncio.Task[None]", asyncio.current_task()),
            )
            self._turns.register(run.registration.turn)
        if self._federation is None:
            return
        # 跨实例登记紧随本地登记:本地表是主,共享登记只是给别实例的指针
        run.registration.federation_token = await self._federation.claim(
            trace_id=run.p.trace_id, user_id=run.user_id
        )

    async def _run(self, run: "_StreamRun", turn: InflightTurn | None) -> None:
        """执行单轮消费:转发过程帧,随后收敛终态。"""
        terminal = run.terminal

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
                # agent error 帧:data 为含字符串 error 的对象才算合法结尾;
                # 由 api 统一收敛为友好文案(不透传 agent 原文)
                if _agent_error_shape_ok(data_json):
                    terminal.error_text = AGENT_UNAVAILABLE_TEXT
                else:
                    log.warning("agent error 帧形状不合格(跳帧) trace_id=%s", run.p.trace_id)
            elif event in _PROCESS_EVENTS:
                await _forward_process(event, data_json, run.queue, run.collector, run.p.trace_id)
            # trace 与未知事件不出公开流(诊断信号限 agent 内部)

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
        await _await_settle(asyncio.create_task(self._settle(run, turn)), turn)

    async def _settle(self, run: "_StreamRun", turn: InflightTurn | None) -> None:
        """终态收敛:被请求取消且没有业务终态时收敛为已取消(取消优先于失败收敛),
        否则按既有规则(result 或 error);收敛结果随终态记下,取消发起方据此回答。
        语音轮的终态回填在本处统一发生(取消/失败/结果三类收敛都要回填)。"""
        terminal = run.terminal
        stage_trace = run.collector.payload() or []
        cancelled = turn is not None and turn.cancel_requested
        if cancelled and terminal.outcome is None:
            await self._converge_cancelled(run, stage_trace=stage_trace)
        else:
            await terminal.flush(
                self,
                run.p,
                datasource_key=run.datasource_key,
                queue=run.queue,
                stage_trace=stage_trace,
            )
        if turn is not None:
            turn.settlement = terminal.settlement
        if self._voice_binding is not None and terminal.settlement is not None:
            o = terminal.outcome
            if o is not None:
                outcome: dict[str, object] = voice_turn_outcome(o)
            elif cancelled:
                outcome = {"verdict": "CANCELLED", "success": False}
            else:
                outcome = {"verdict": "FAILED", "success": False}
            self._voice_binding.settle(
                trace_id=run.p.trace_id, user_id=run.user_id, outcome=outcome
            )

    async def _converge_cancelled(
        self, run: "_StreamRun", *, stage_trace: list[dict[str, object]]
    ) -> None:
        """取消收敛:落已取消行(独立会话)并入队唯一的终态帧;不走正常终态管线,
        不落半截 SQL 或半截摘要,不触发标题改写/成功回写/评分/隐式记忆。"""
        p = run.p
        outcome: ChatOutcome
        if self._stream_store_factory is None:
            log.error("stream_store_factory 未注入,已取消终态不落库")
            outcome = ChatOutcome(
                session_id=p.sid, success=False, verdict="CANCELLED", summary=TURN_CANCELLED_TEXT
            )
        else:
            async with self._stream_store_factory() as store:
                outcome = await persist_cancelled(
                    store.repo,
                    sid=p.sid,
                    trace_id=p.trace_id,
                    user_id=run.user_id,
                    datasource_key=run.datasource_key,
                    attachments=p.attachments,
                    stage_trace=stage_trace,
                    user_message_id=p.user_message_id,
                )
        run.terminal.settlement = TurnSettlement(
            "CANCELLED" if outcome.verdict == "CANCELLED" else "COMPLETED", outcome.message_id
        )
        await run.queue.put(outcome)

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
    ) -> ChatOutcome:
        """流式传输层失败的 FAILED 落库(独立会话;失败行带本轮标识,取消收敛按它查重);
        返回该终态。落库失败仅日志(不阻断 error 帧),此时无落库消息标识。"""
        fallback = ChatOutcome(
            session_id=p.sid,
            success=False,
            error=AGENT_UNAVAILABLE_TEXT,
            datasource_key=datasource_key,
        )
        if self._stream_store_factory is None:
            log.error("stream_store_factory 未注入,FAILED 终态不落库")
            return fallback
        try:
            async with self._stream_store_factory() as store:
                return await persist_unavailable(
                    store.repo,
                    sid=p.sid,
                    trace_id=p.trace_id,
                    datasource_key=datasource_key,
                    attachments=p.attachments,
                    stage_trace=stage_trace,
                )
        except Exception:  # 断线续存落库兜底:不阻断终态帧下发
            log.warning("流式 FAILED 终态落库异常 session=%s", p.sid, exc_info=True)
            return fallback


async def _await_settle(settle: "asyncio.Task[None]", turn: InflightTurn | None) -> None:
    """等收尾任务结束。收尾(落库 + 终态帧)一旦开始就不被请求取消打断——终态只落定一次:
    请求取消落在收尾段时继续等它做完,本轮终态以收尾结果为准;外部取消(无人请求取消)
    则连带取消收尾并原样上抛,不伪造终态。"""
    while not settle.done():
        try:
            await asyncio.shield(settle)
        except asyncio.CancelledError:
            if turn is None or not turn.cancel_requested:
                settle.cancel()
                await asyncio.gather(settle, return_exceptions=True)
                raise
    await settle  # 收尾已结束:正常返回,或原样抛出它的异常


class _TerminalState:
    """单轮终态收集:首个 result 即权威(幂等守卫),否则按传输失败收敛。"""

    def __init__(self) -> None:
        self.outcome: ChatOutcome | None = None
        self.error_text: str | None = None
        self.settlement: TurnSettlement | None = None  # 本轮收敛结果(终态入队时记下)

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
        """解析并落库首个合法 result 帧;重复帧/形状不合格只记日志不改变终态。

        合法结尾形状:data 为 JSON 对象且 success 为布尔;不合格的 result 不算
        结尾信号,若其后仍有合法 result 则照常收敛。
        """
        if self.outcome is not None:
            # 终态幂等守卫:契约恰一个终态帧,首个即权威——
            # 违约多发的 result 不得二次落库/回写/触发标题任务
            log.warning("agent 流重复 result 帧(忽略) trace_id=%s", p.trace_id)
            return
        try:
            data = json.loads(data_json)
        except ValueError:
            log.warning("agent result 帧非 JSON trace_id=%s", p.trace_id)
            self.error_text = AGENT_UNAVAILABLE_TEXT
            return
        if not isinstance(data, dict):
            log.warning("agent result 帧非对象(不作为结尾) trace_id=%s", p.trace_id)
            self.error_text = AGENT_UNAVAILABLE_TEXT
            return
        typed = cast("dict[str, object]", data)
        if not isinstance(typed.get("success"), bool):
            log.warning("agent result 帧形状不合格(不作为结尾) trace_id=%s", p.trace_id)
            self.error_text = AGENT_UNAVAILABLE_TEXT
            return
        reply = parse_agent_reply(typed)
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
        queue: FrameQueue,
        stage_trace: list[dict[str, object]],
    ) -> None:
        """按终态入队唯一的终态(result=ChatOutcome 或失败标记);FAILED 先落库再入队。"""
        if self.outcome is not None:
            # 业务终态已落库(result 先finalize):即使读尾传输异常也按成功下发,
            # 不叠加 FAILED 消息(双落库收敛)
            self.settlement = TurnSettlement("COMPLETED", self.outcome.message_id)
            await queue.put(self.outcome)
            return
        if self.error_text is None:
            # agent 流正常结束但无终态帧(契约破坏):与不可达同收敛
            log.error("agent 流结束但缺终态帧 trace_id=%s", p.trace_id)
            self.error_text = AGENT_UNAVAILABLE_TEXT
        # 传输层/意外失败终态:FAILED 落库(独立会话)+ 预制失败结尾(发送端定文案)
        failed = await agent_sink.persist_stream_failure(
            p, datasource_key=datasource_key, stage_trace=stage_trace
        )
        self.settlement = TurnSettlement("COMPLETED", failed.message_id)
        await queue.put(StreamFailure())


@dataclass
class _TurnRegistration:
    """在途登记的产物:本地轮句柄与跨实例注销凭据(登记发生在 consume 内)。"""

    turn: InflightTurn | None = None
    federation_token: str | None = None


@dataclass(frozen=True)
class _StreamRun:
    """单轮流式消费的全部入参(帧回调闭包据此捕获,避免逐个闭包变量)。"""

    p: PreparedQuery
    user_id: str
    question: str
    orig_question: str
    persist: bool
    datasource_key: str
    queue: "asyncio.Queue[OutboundFrame | None]"
    react_tools: list[dict[str, object]] | None
    eff_history: list[str] | None
    memory_hint: str
    pref_revision: int | None
    attachment_ids: list[str] | None
    collector: StageTraceCollector = field(default_factory=StageTraceCollector)
    # 终态收集随行携带:硬取消从流的任意位置抛出后,收敛段仍能读到已收集的终态
    terminal: _TerminalState = field(default_factory=_TerminalState)
    # 在途登记产物(可变容器):frozen 的 run 不就地赋值,与 terminal 同范式
    registration: _TurnRegistration = field(default_factory=_TurnRegistration)


async def _forward_process(
    event: str,
    data_json: str,
    queue: "asyncio.Queue[OutboundFrame | None]",
    collector: StageTraceCollector,
    trace_id: str,
) -> None:
    """过程帧入队:组合信封后按公开模型校验;坏消息跳过并按类别记日志(不含原文)。

    只有通过校验的 stage 进入节点轨迹;非法形状既不外发也不污染阶段记录。
    """
    try:
        data = json.loads(data_json)
    except ValueError:
        log.warning("agent 过程帧非 JSON(跳帧) trace_id=%s", trace_id)
        return
    if not isinstance(data, dict):
        log.warning("agent 过程帧非对象(跳帧) trace_id=%s kind=%s", trace_id, type(data).__name__)
        return
    try:
        frame = _PROCESS_ADAPTER.validate_python({"type": event, "data": data})
    except ValidationError as e:
        # 只记事件名与失败类别;原始内容不进日志
        log.warning(
            "agent 过程帧形状不合格(跳帧) trace_id=%s event=%s errors=%s",
            trace_id,
            event,
            e.error_count(),
        )
        return
    if event == "stage":
        collector.add_stage(frame.data.model_dump())  # 轨迹只收有效 stage
    await queue.put(frame)


def _agent_error_shape_ok(data_json: str) -> bool:
    """agent error 帧的合法结尾形状:JSON 对象且含字符串 error。"""
    try:
        data = json.loads(data_json)
    except ValueError:
        return False
    if not isinstance(data, dict):
        return False
    return isinstance(cast("dict[str, object]", data).get("error"), str)
