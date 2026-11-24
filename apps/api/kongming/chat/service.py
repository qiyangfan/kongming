"""chat 用例层:问答编排(认证授权已在 router 依赖层完成)。

编排顺序(chat spec「问答查询端点」):
租户解析(白名单)→ 会话(新建/归属校验)→ history 补全 → trace_id 生成 →
回调 agent → 消息落库(USER + ASSISTANT,三态映射)→ 响应组装(含图表建议)。

流式编排(query-streaming):前段与终态落库同同步路径共用唯一实现;
agent 流由后台任务消费,前端断线后继续至终态(断线续存)。终态落库见
finalize,流式消费见 stream_runner,回显映射见 message_view;
会话列表/重命名/删除与导出见 session_service。
"""

import asyncio
import dataclasses
import logging
import uuid
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any, TypedDict, Unpack

from kongming.chat.agent_client import AgentClient, AgentReply, AgentUnavailable
from kongming.chat.costgate import ChatCostGate
from kongming.chat.domain_errors import (
    AttachmentNotFound,
    ExplainNoSql,
    InvalidDatasource,
    MessageNotFound,
    SessionNotFound,
    TenantForbidden,
    blocked_friendly,
)
from kongming.chat.finalize import FinalizePorts, ReplyFinalizer, persist_unavailable
from kongming.chat.inflight import TurnCanceller, TurnSettlement
from kongming.chat.ports import (
    AttachmentChecker,
    ChatRepo,
    ExecuteRunner,
    ExplainRunner,
    GuardrailPort,
    MemoryPort,
    PendingToolReader,
    StreamStoreFactory,
    ToolSpecProvider,
    TurnRegistry,
)
from kongming.chat.results import ChatOutcome, ExplainView
from kongming.chat.stream_runner import (
    AgentStreamConsumer,
    PreparedQuery,
    StreamHandle,
    noop,
    spawn_background,
)
from kongming.datasource.ports import DatasourceChecker
from kongming.rag.ports import ColumnCommentReader, QueryHistoryWriter
from kongming.trace import ensure_trace_id

log = logging.getLogger(__name__)

SESSION_TITLE_LEN = 30  # 标题=首问前 30 字符(chat spec)
HISTORY_FILL_LIMIT = 5  # 后端补全最近 5 条 user 问句


async def resolve_tenant(repo: ChatRepo, user_id: str, tenant_key: str | None) -> tuple[str, str]:
    """X-Tenant-Id 解析:显式传入查白名单;缺省取用户默认租户(default 优先,
    否则最早授予)。返回 (tenant_id, tenant_key);未绑定一律 TenantForbidden。"""
    if tenant_key is not None:
        tenant_id = await repo.find_bound_tenant(user_id=user_id, tenant_key=tenant_key)
        if tenant_id is None:
            raise TenantForbidden(tenant_key)
        return tenant_id, tenant_key
    default = await repo.find_default_tenant(user_id=user_id)
    if default is None:
        raise TenantForbidden("<default>")
    return default


@dataclass(frozen=True)
class _AskRequest:
    """护栏放行后的本轮问句与编排选项(同步/流式共用)。"""

    user_id: str
    question: str
    orig_question: str
    persist: bool
    datasource_key: str
    agent_mode: str
    eff_history: list[str]


@dataclass(frozen=True)
class _TurnContext:
    """前段已解析出的本轮上下文(写入 USER 消息所需字段)。"""

    tenant_id: str
    resolved_key: str
    sid: str | None
    user_id: str
    question: str
    persist: bool
    history: list[str]
    datasource_key: str
    attachment_ids: list[str] | None


class _ServiceOptions(TypedDict, total=False):
    """ChatService 的可选注入项(缺省 None 表示该端口未接线)。"""

    history_writer: QueryHistoryWriter | None  # 成功 SQL 回写
    explainer: ExplainRunner | None  # 执行计划端口(组合根绑定)
    stream_store_factory: StreamStoreFactory | None  # 断线续存落库单元
    tool_specs: ToolSpecProvider | None  # ReAct 授权工具注入
    pending_tools: PendingToolReader | None  # 待确认调用聚合
    guardrail: GuardrailPort | None  # 输入/输出护栏
    memory: MemoryPort | None  # 用户长期记忆
    runner: ExecuteRunner | None  # SQL 再执行端口
    attachment_checker: AttachmentChecker | None  # 附件归属校验
    implicit_memory_task: Callable[..., Coroutine[Any, Any, None]] | None  # 隐式提取任务
    scoring_task: Callable[..., Coroutine[Any, Any, None]] | None  # 答案异步评分任务
    column_comments: ColumnCommentReader | None  # 列标签
    cost_gate: ChatCostGate | None  # 成本双闸(速率+配额)
    turns: TurnRegistry | None  # 在途轮登记(取消端点据此定位在途轮)


@dataclass(frozen=True)
class _Ports:
    """ChatService 持有的外部端口与任务句柄(组合根逐项注入)。"""

    repo: ChatRepo
    agent: AgentClient
    ds_checker: DatasourceChecker
    history_writer: QueryHistoryWriter | None
    explainer: ExplainRunner | None
    tool_specs: ToolSpecProvider | None
    guardrail: GuardrailPort | None
    memory: MemoryPort | None
    runner: ExecuteRunner | None
    attachment_checker: AttachmentChecker | None
    scoring_task: "Callable[..., Coroutine[Any, Any, None]] | None"
    cost_gate: ChatCostGate | None
    turns: TurnRegistry | None

    @classmethod
    def of(
        cls,
        repo: ChatRepo,
        agent: AgentClient,
        ds_checker: DatasourceChecker,
        options: _ServiceOptions,
    ) -> "_Ports":
        """必选端口直取,可选端口从注入项逐个取默认 None。"""
        return cls(
            repo=repo,
            agent=agent,
            ds_checker=ds_checker,
            history_writer=options.get("history_writer"),
            explainer=options.get("explainer"),
            tool_specs=options.get("tool_specs"),
            guardrail=options.get("guardrail"),
            memory=options.get("memory"),
            runner=options.get("runner"),
            attachment_checker=options.get("attachment_checker"),
            scoring_task=options.get("scoring_task"),
            cost_gate=options.get("cost_gate"),
            turns=options.get("turns"),
        )


@dataclass(frozen=True)
class _Engine:
    """终态管线与流式消费器(共用同一 ReplyFinalizer,避免两处各自组装)。"""

    finalizer: ReplyFinalizer
    streams: AgentStreamConsumer

    @classmethod
    def of(cls, ports: _Ports, options: _ServiceOptions) -> "_Engine":
        """按端口装配终态管线与流式消费器(两者共用同一 finalizer)。"""
        finalizer = ReplyFinalizer(
            FinalizePorts(
                guardrail=ports.guardrail,
                pending_tools=options.get("pending_tools"),
                column_comments=options.get("column_comments"),
            )
        )
        return cls(
            finalizer=finalizer,
            streams=AgentStreamConsumer(
                agent=ports.agent,
                stream_store_factory=options.get("stream_store_factory"),
                finalizer=finalizer,
                repo=ports.repo,
                implicit_memory_task=options.get("implicit_memory_task"),
                turns=ports.turns,
            ),
        )


class ChatService:
    def __init__(
        self,
        repo: ChatRepo,
        agent: AgentClient,
        ds_checker: DatasourceChecker,
        **options: Unpack[_ServiceOptions],
    ) -> None:
        """组合根装配:必选仓储/agent/数据源校验器,可选端口按关键字传入(缺省 None=不接线)。"""
        self._ports = _Ports.of(repo, agent, ds_checker, options)
        self._engine = _Engine.of(self._ports, options)

    @property
    def agent_for_tasks(self) -> AgentClient:
        """后台任务用 agent 句柄(标题改写;AgentClient 无会话状态,响应后可安全复用)。"""
        return self._ports.agent

    async def _prepare_query(
        self,
        *,
        user_id: str,
        tenant_key: str | None,
        question: str,
        session_id: str | None,
        persist: bool,
        history: list[str],
        datasource_key: str,
        attachment_ids: list[str] | None = None,
    ) -> PreparedQuery:
        """问答前段(同步/流式共用唯一实现):租户→数据源预检→会话→history→
        trace_id;USER 消息落库也在此(流式路径须在 SSE 响应开始前完成)。"""
        tenant_id, resolved_key = await resolve_tenant(self._ports.repo, user_id, tenant_key)
        await self._check_cost(tenant_id=tenant_id, user_id=user_id)
        await self._check_datasource(datasource_key, tenant_id)
        sid = await self._resolve_session(
            user_id=user_id,
            tenant_id=tenant_id,
            question=question,
            session_id=session_id,
            persist=persist,
            datasource_key=datasource_key,
        )
        return await self._record_user_turn(
            _TurnContext(
                tenant_id=tenant_id,
                resolved_key=resolved_key,
                sid=sid,
                user_id=user_id,
                question=question,
                persist=persist,
                history=history,
                datasource_key=datasource_key,
                attachment_ids=attachment_ids,
            )
        )

    async def _check_cost(self, *, tenant_id: str, user_id: str) -> None:
        """成本双闸(速率+配额):租户解析后、烧 LLM 前 fail-fast;未配置则跳过。"""
        if self._ports.cost_gate is not None:
            await self._ports.cost_gate.check_login(tenant_id=tenant_id, user_id=user_id)

    async def _check_datasource(self, datasource_key: str, tenant_id: str) -> None:
        """数据源入口预检:非 default 校验存在+启用+归属当前租户,否则统一 400。"""
        # 不调用 Agent、不建会话,连接信息留在控制面(ADR-002)
        if datasource_key != "default" and not await self._ports.ds_checker.is_usable(
            datasource_key, tenant_id
        ):
            raise InvalidDatasource(datasource_key)

    async def _record_user_turn(self, turn: _TurnContext) -> PreparedQuery:
        """补全 history/兜底分类/附件,落 USER 消息并生成本轮 trace_id。"""
        # history:显式优先;否则按会话从消息表补全(最近 5 条)
        effective_history = turn.history or (
            await self._ports.repo.recent_user_questions(turn.sid, limit=HISTORY_FILL_LIMIT)
            if turn.sid is not None
            else []
        )
        # 上一轮兜底分类(澄清防循环判据;仅落库会话携带,诊断字段不入响应)
        last_fallback = (
            (await self._ports.repo.last_fallback_type(turn.sid) or "")
            if turn.sid is not None
            else ""
        )
        attachments = await self._validate_attachments(
            turn.attachment_ids, user_id=turn.user_id, tenant_id=turn.tenant_id
        )
        trace_id = ensure_trace_id()  # 与响应头 X-Trace-Id / 用量落库同一值
        if turn.sid is not None:
            await self._ports.repo.insert_message(
                session_id=turn.sid,
                role="USER",
                question=turn.question,
                status="SUCCESS",
                attachments=attachments,
            )
            await self._ports.repo.commit()
        return PreparedQuery(
            tenant_id=turn.tenant_id,
            resolved_key=turn.resolved_key,
            sid=turn.sid,
            effective_history=effective_history,
            trace_id=trace_id,
            user_id=turn.user_id,
            attachments=attachments,
            last_fallback=last_fallback,
        )

    async def _resolve_session(
        self,
        *,
        user_id: str,
        tenant_id: str,
        question: str,
        session_id: str | None,
        persist: bool,
        datasource_key: str,
    ) -> str | None:
        """会话:显式传入校验归属(不命中 404 同形);缺省且 persist 时新建。"""
        if session_id is not None:
            sid = await self._ports.repo.find_session_owned(
                session_id=session_id, user_id=user_id, tenant_id=tenant_id
            )
            if sid is None:
                raise SessionNotFound(session_id)
            return sid
        if not persist:
            return None
        return await self._ports.repo.create_session(
            user_id=user_id,
            tenant_id=tenant_id,
            title=question[:SESSION_TITLE_LEN],
            datasource_key=datasource_key,
        )

    async def _validate_attachments(
        self, attachment_ids: list[str] | None, *, user_id: str, tenant_id: str
    ) -> list[dict[str, object]] | None:
        """附件归属校验(当前用户+租户+未消费;任一无效 404 同形,
        不进 agent 回调);checker 未接线(部署缺陷)且引用非空=fail-closed。"""
        if not attachment_ids:
            return None
        if self._ports.attachment_checker is None:
            log.error("AttachmentChecker 未注入但请求携带附件")
            raise AttachmentNotFound(",".join(attachment_ids))
        attachments = await self._ports.attachment_checker.validate_owned(
            ids=attachment_ids, user_id=user_id, tenant_id=tenant_id
        )
        if attachments is None:
            raise AttachmentNotFound(",".join(attachment_ids))
        return attachments

    async def _react_tools(
        self, user_id: str, agent_mode: str, tenant_id: str
    ) -> list[dict[str, object]] | None:
        """React 模式下按用户授权返回工具；recipe 模式或未配置时返回 None。"""
        if agent_mode != "react" or self._ports.tool_specs is None:
            return None
        return await self._ports.tool_specs.authorized_tools(user_id, tenant_id)

    async def _finalize_reply(
        self,
        *,
        reply: AgentReply,
        sid: str | None,
        tenant_id: str,
        datasource_key: str,
        question: str,
        persist: bool,
        repo: ChatRepo,
        history_writer: QueryHistoryWriter | None,
        trace_id: str = "",
        user_id: str = "",
        attachments: list[dict[str, object]] | None = None,
        is_react: bool = False,
        stage_trace: list[dict[str, object]] | None = None,
    ) -> ChatOutcome:
        """同步路径终态(委托 ReplyFinalizer;单轮终态唯一实现在 finalize)。"""
        return await self._engine.finalizer.finalize(
            reply=reply,
            sid=sid,
            tenant_id=tenant_id,
            datasource_key=datasource_key,
            question=question,
            persist=persist,
            repo=repo,
            history_writer=history_writer,
            trace_id=trace_id,
            user_id=user_id,
            attachments=attachments,
            is_react=is_react,
            stage_trace=stage_trace,
        )

    async def _finalize_unavailable(
        self,
        *,
        sid: str | None,
        repo: ChatRepo,
        datasource_key: str = "default",
        attachments: list[dict[str, object]] | None = None,
        stage_trace: list[dict[str, object]] | None = None,
    ) -> ChatOutcome:
        """agent 不可达终态(委托 finalize.persist_unavailable)。"""
        return await persist_unavailable(
            repo,
            sid=sid,
            datasource_key=datasource_key,
            attachments=attachments,
            stage_trace=stage_trace,
        )

    async def _memory_short_circuit(
        self,
        p: PreparedQuery,
        *,
        question: str,
        persist: bool,
        datasource_key: str,
    ) -> ChatOutcome | None:
        """识别并处理“记住……”请求；非记忆请求或未配置记忆服务时返回 None，继续问答。"""
        if self._ports.memory is None:
            return None
        content = await self._ports.memory.explicit_intent(question)
        if content is None:
            return None
        saved = await self._ports.memory.form_explicit(
            user_id=p.user_id, tenant_id=p.tenant_id, content=content
        )
        if saved:
            reply = AgentReply(success=True, verdict="PASS", summary=f"已记住:{content}")
        else:
            reply = AgentReply(success=False, verdict="BLOCKED", error="MEMORY_SENSITIVE")
        outcome = await self._finalize_reply(
            reply=reply,
            sid=p.sid,
            tenant_id=p.tenant_id,
            datasource_key=datasource_key,
            question=question,
            persist=persist,
            repo=self._ports.repo,
            history_writer=None,
            trace_id=p.trace_id,
            attachments=p.attachments,
            user_id=p.user_id,
        )
        return dataclasses.replace(outcome, memory_shortcut=True)

    async def _recall_memory_hint(
        self, *, user_id: str, tenant_id: str, question: str
    ) -> tuple[str, int | None]:
        """调用 Agent 前读取记忆与偏好提示,并捕获偏好人工版本;
        读取失败时不注入,版本缺失时跳过自动写入。"""
        if self._ports.memory is None:
            return "", None
        parts: list[str] = []
        try:
            mem = await self._ports.memory.recall_hint(
                user_id=user_id, tenant_id=tenant_id, question=question
            )
            if mem:
                parts.append(mem)
        except Exception:  # 记忆读取失败不影响问答
            log.warning("记忆召回异常(降级无注入)", exc_info=True)
        try:
            pref = await self._ports.memory.preference_hint(user_id=user_id, tenant_id=tenant_id)
            if pref:
                parts.append(pref)
        except Exception:  # 偏好读取失败不影响问答
            log.warning("偏好注入异常(降级无注入)", exc_info=True)
        revision: int | None = None
        try:
            revision = await self._ports.memory.current_revision(
                user_id=user_id, tenant_id=tenant_id
            )
        except Exception:  # 版本捕获失败只放弃自动写入
            log.warning("偏好人工版本捕获异常(本轮自动写入跳过)", exc_info=True)
        return "\n\n".join(parts), revision

    async def _guard_input(
        self,
        *,
        user_id: str,
        tenant_id: str,
        trace_id: str,
        question: str,
        history: list[str],
    ) -> tuple[bool, str, list[str]]:
        """检查问句和历史记录，返回拦截状态及处理后的文本；未配置时原样放行。"""
        if self._ports.guardrail is None:
            return False, question, history
        blocked, processed = await self._ports.guardrail.check_input(
            trace_id=trace_id,
            user_id=user_id,
            tenant_id=tenant_id,
            texts=[question, *history],
        )
        return blocked, processed[0], processed[1:]

    async def _guard_blocked_outcome(
        self,
        p: PreparedQuery,
        *,
        question: str,
        persist: bool,
        datasource_key: str,
    ) -> ChatOutcome:
        """输入护栏拦截的统一终态:REJECTED 落库(persist 时)+ 安全检查文案,
        不回调 Agent 面(spec「输入护栏」)。"""
        return await self._finalize_reply(
            reply=AgentReply(success=False, verdict="BLOCKED", error="GUARDRAIL_INPUT"),
            sid=p.sid,
            tenant_id=p.tenant_id,
            datasource_key=datasource_key,
            question=question,
            persist=persist,
            repo=self._ports.repo,
            history_writer=None,
            trace_id=p.trace_id,
            attachments=p.attachments,
            user_id=p.user_id,
        )

    async def query(
        self,
        *,
        user_id: str,
        tenant_key: str | None,
        question: str,
        session_id: str | None,
        persist: bool,
        history: list[str],
        datasource_key: str,
        agent_mode: str = "recipe",  # recipe(8 节点,缺省)/ react(ReAct 内核)
        attachment_ids: list[str] | None = None,
    ) -> ChatOutcome:
        p = await self._prepare_query(
            user_id=user_id,
            tenant_key=tenant_key,
            question=question,
            session_id=session_id,
            persist=persist,
            history=history,
            attachment_ids=attachment_ids,
            datasource_key=datasource_key,
        )
        # 显式记忆短路先于输入护栏(原始问句判定——脱敏后内容会漏检
        # 密码语境等敏感形态,spec「形成前 PII/凭证检测」以原文为准)
        short = await self._memory_short_circuit(
            p, question=question, persist=persist, datasource_key=datasource_key
        )
        if short is not None:
            return short
        # 拦截时返回 REJECTED 且不调用 Agent；脱敏文本传给 Agent。
        orig_question = question  # 回写 query_history 须取原始问句
        blocked, question, eff_history = await self._guard_input(
            user_id=user_id,
            tenant_id=p.tenant_id,
            trace_id=p.trace_id,
            question=question,
            history=p.effective_history,
        )
        if blocked:
            return await self._guard_blocked_outcome(
                p, question=question, persist=persist, datasource_key=datasource_key
            )
        return await self._answer(
            p,
            _AskRequest(
                user_id=user_id,
                question=question,
                orig_question=orig_question,
                persist=persist,
                datasource_key=datasource_key,
                agent_mode=agent_mode,
                eff_history=eff_history,
            ),
        )

    async def _answer(self, p: PreparedQuery, ask: _AskRequest) -> ChatOutcome:
        """护栏放行后的同步问答:记忆提示→ReAct 工具→agent→终态落库。"""
        memory_hint, pref_revision = await self._recall_memory_hint(
            user_id=ask.user_id, tenant_id=p.tenant_id, question=ask.question
        )
        try:
            react_tools = await self._react_tools(ask.user_id, ask.agent_mode, p.tenant_id)
            reply = await self._ports.agent.query(
                trace_id=p.trace_id,
                tenant_key=p.resolved_key,
                question=ask.question,
                user_id=ask.user_id,
                history=ask.eff_history,
                datasource_key=ask.datasource_key,
                react_tools=react_tools,
                memory_hint=memory_hint,
                attachment_ids=[str(a["id"]) for a in p.attachments or []],
                last_fallback=p.last_fallback,
            )
        except AgentUnavailable:
            log.warning("agent 不可达 trace_id=%s", p.trace_id)
            return await self._finalize_unavailable(
                sid=p.sid,
                repo=self._ports.repo,
                datasource_key=ask.datasource_key,
                attachments=p.attachments,
            )
        outcome = await self._finalize_reply(
            reply=reply,
            sid=p.sid,
            tenant_id=p.tenant_id,
            datasource_key=ask.datasource_key,
            question=ask.orig_question,  # 保存原始问句；脱敏文本仅传给 Agent，不写入召回库。
            persist=ask.persist,
            repo=self._ports.repo,
            history_writer=self._ports.history_writer,
            trace_id=p.trace_id,
            attachments=p.attachments,
            is_react=react_tools is not None,
            user_id=ask.user_id,
        )
        # 同步路径经 outcome 携带版本快照给 router 的后台任务
        return dataclasses.replace(outcome, pref_revision=pref_revision)

    # ============ 流式编排(query-streaming) ============

    @staticmethod
    def _immediate_handle(outcome: ChatOutcome) -> StreamHandle:
        """短路轮的句柄:result 直入队 + 终止标记,空任务占位(不起后台任务)。"""
        queue: asyncio.Queue[tuple[str, object] | None] = asyncio.Queue()
        queue.put_nowait(("result", outcome))
        queue.put_nowait(None)
        return StreamHandle(queue=queue, task=spawn_background(noop()))

    async def start_stream(
        self,
        *,
        user_id: str,
        tenant_key: str | None,
        question: str,
        session_id: str | None,
        persist: bool,
        history: list[str],
        datasource_key: str,
        agent_mode: str = "recipe",
        attachment_ids: list[str] | None = None,
    ) -> StreamHandle:
        """启动流式问答:前段(含 USER 落库)完成后创建后台消费任务并返回句柄;
        前段异常(403/404/400)在 SSE 响应开始前抛出,由 router 常规映射。"""
        p = await self._prepare_query(
            user_id=user_id,
            tenant_key=tenant_key,
            question=question,
            session_id=session_id,
            persist=persist,
            history=history,
            datasource_key=datasource_key,
            attachment_ids=attachment_ids,
        )
        # 显式记忆短路先于输入护栏(原始问句判定,与同步同序)
        short = await self._memory_short_circuit(
            p, question=question, persist=persist, datasource_key=datasource_key
        )
        if short is not None:
            return self._immediate_handle(short)
        # 输入护栏(流式):拦截→result(REJECTED 形态)+ 终止标记。
        orig_question = question  # 回写 query_history 须取原始问句
        blocked, question, eff_history = await self._guard_input(
            user_id=user_id,
            tenant_id=p.tenant_id,
            trace_id=p.trace_id,
            question=question,
            history=p.effective_history,
        )
        return await self._guarded_stream(
            p,
            _AskRequest(
                user_id=user_id,
                question=question,
                orig_question=orig_question,
                persist=persist,
                datasource_key=datasource_key,
                agent_mode=agent_mode,
                eff_history=eff_history,
            ),
            blocked=blocked,
        )

    async def _guarded_stream(
        self, p: PreparedQuery, ask: "_AskRequest", *, blocked: bool
    ) -> StreamHandle:
        """护栏判定后的流式分支:拦截给即时句柄,放行起后台消费任务。"""
        if blocked:
            outcome = await self._guard_blocked_outcome(
                p,
                question=ask.question,
                persist=ask.persist,
                datasource_key=ask.datasource_key,
            )
            return self._immediate_handle(outcome)
        return await self._spawn_consumer(
            p,
            user_id=ask.user_id,
            question=ask.question,
            orig_question=ask.orig_question,
            persist=ask.persist,
            datasource_key=ask.datasource_key,
            agent_mode=ask.agent_mode,
            eff_history=ask.eff_history,
        )

    async def _spawn_consumer(
        self,
        p: PreparedQuery,
        *,
        user_id: str,
        question: str,
        orig_question: str,
        persist: bool,
        datasource_key: str,
        agent_mode: str,
        eff_history: list[str],
    ) -> StreamHandle:
        """起后台消费任务(记忆提示→ReAct 工具→入队句柄),返回句柄。"""
        memory_hint, pref_revision = await self._recall_memory_hint(
            user_id=user_id, tenant_id=p.tenant_id, question=question
        )
        react_tools = await self._react_tools(user_id, agent_mode, p.tenant_id)
        return self._engine.streams.begin(
            p,
            user_id=user_id,
            question=question,
            orig_question=orig_question,
            persist=persist,
            datasource_key=datasource_key,
            react_tools=react_tools,
            eff_history=eff_history,
            memory_hint=memory_hint,
            pref_revision=pref_revision,  # 后台任务携带版本快照
            attachment_ids=[str(a["id"]) for a in p.attachments or []],
        )

    async def cancel_turn(self, *, user_id: str, trace_id: str) -> TurnSettlement:
        """取消一轮在途流式问答,或回答该轮已如何结束;
        不在途且本人名下无落库结果时抛 TurnNotFound。"""
        return await TurnCanceller(
            agent=self._ports.agent, turns=self._ports.turns, repo=self._ports.repo
        ).cancel(user_id=user_id, trace_id=trace_id)

    # ============ 执行计划(chat spec「EXPLAIN 执行计划」) ============

    async def rerun_artifact(
        self, *, user_id: str, tenant_key: str | None, message_id: str, sql: str
    ) -> ChatOutcome:
        """SQL 卡再执行:归属校验(会话快照数据源)+ 六层防御(复用
        internal 执行)+ 结果落新 assistant 消息(不覆盖原 artifact);
        输出经护栏(_finalize_reply 同源)。"""
        tenant_id, resolved_key = await resolve_tenant(self._ports.repo, user_id, tenant_key)
        target = await self._ports.repo.find_explain_target(
            message_id=message_id, user_id=user_id, tenant_id=tenant_id
        )
        if target is None:
            raise MessageNotFound(message_id)
        ds_key = str(target["datasource_key"])
        if ds_key != "default" and not await self._ports.ds_checker.is_usable(ds_key, tenant_id):
            raise InvalidDatasource(ds_key)
        # ExecuteRunner 端口由组合根绑定 internal.service.execute(与 EXPLAIN 同范式);
        # rerun 全程不用 explainer,不做无谓检查
        if self._ports.runner is None:  # 组合根未接线(部署缺陷)——友好降级
            log.error("ExecuteRunner 未注入,再执行不可用")
            return await self._finalize_unavailable(
                sid=None, repo=self._ports.repo, datasource_key=ds_key
            )
        trace_id = ensure_trace_id()  # 执行与输出护栏审计同一 trace
        result = await self._ports.runner.run(
            trace_id=trace_id,
            tenant_key=resolved_key,
            datasource_key=ds_key,
            sql_text=sql,
            user_id=user_id,
        )
        reply = AgentReply(
            success=result.verdict == "PASS",
            verdict=result.verdict,
            summary="",  # 再执行无 LLM 摘要;摘要留空(结果集即产物)
            columns=result.columns,
            rows=result.rows,
            sql=sql,
            error=result.reason,
        )
        # spec「写操作拦截:不执行,不落新结果消息」——非 PASS(BLOCKED/ERROR)
        # 不落新 assistant 消息(否则 REJECTED 的 sql_text 会再生成可执行的
        # sql 卡);错误经响应返回
        persist_sid = str(target["session_id"]) if result.verdict == "PASS" else None
        return await self._finalize_reply(
            reply=reply,
            sid=persist_sid,
            tenant_id=tenant_id,
            datasource_key=ds_key,
            question="",  # history_writer=None 时无消费方(再执行不回写 query_history)
            persist=True,
            repo=self._ports.repo,
            history_writer=None,  # 编辑后 SQL 不回写 query_history(防污染 few-shot)
            trace_id=trace_id,
            user_id=user_id,
        )

    async def explain_message(
        self, *, user_id: str, tenant_key: str | None, message_id: str
    ) -> ExplainView:
        """message_id 引用形态:客户端不可提交裸 SQL;被分析 SQL 恒为落库消息
        的 agent 产出,数据源取会话落库快照(非切换器实时值);完整防线在
        ExplainRunner 实现侧(与 /internal/sql/execute 同源私有段)。"""
        tenant_id, resolved_key = await resolve_tenant(self._ports.repo, user_id, tenant_key)
        target = await self._ports.repo.find_explain_target(
            message_id=message_id, user_id=user_id, tenant_id=tenant_id
        )
        if target is None:
            raise MessageNotFound(message_id)
        sql_raw = target.get("sql_text")
        sql_text = str(sql_raw) if sql_raw is not None else ""
        if not sql_text.strip():
            raise ExplainNoSql(message_id)
        ds_key = str(target["datasource_key"])
        # 数据源快照复校(与问答端点同形同码,防 key 探测)
        if ds_key != "default" and not await self._ports.ds_checker.is_usable(ds_key, tenant_id):
            raise InvalidDatasource(ds_key)
        if self._ports.explainer is None:  # 组合根未接线(部署缺陷)——友好降级不裸 500
            log.error("ExplainRunner 未注入,执行计划不可用")
            return ExplainView(success=False, error="执行计划暂不可用,请稍后重试")
        outcome = await self._ports.explainer.explain(
            trace_id=uuid.uuid4().hex,
            tenant_key=resolved_key,
            datasource_key=ds_key,
            sql_text=sql_text,
            user_id=user_id,
        )
        if outcome.verdict == "PASS":
            return ExplainView(success=True, plan=outcome.plan)
        if outcome.verdict == "BLOCKED":
            return ExplainView(success=False, error=blocked_friendly(outcome.reason))
        return ExplainView(
            success=False,
            error=(
                f"执行计划失败:{outcome.reason}" if outcome.reason else "执行计划失败,请稍后重试"
            ),
        )
