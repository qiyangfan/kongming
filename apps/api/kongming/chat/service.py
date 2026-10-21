"""chat 用例层:问答编排(认证授权已在 router 依赖层完成)。

编排顺序(chat spec「问答查询端点」):
租户解析(白名单)→ 会话(新建/归属校验)→ history 补全 → trace_id 生成 →
回调 agent → 消息落库(USER + ASSISTANT,三态映射)→ 响应组装(含图表建议)。

流式编排(query-streaming):前段与终态落库同同步路径共用唯一实现;
agent 流由后台任务消费,前端断线后继续至终态(断线续存)。
"""

import asyncio
import contextlib
import dataclasses
import json
import logging
import uuid
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any, cast

from kongming.chat.agent_client import AgentClient, AgentReply, AgentUnavailable, parse_agent_reply
from kongming.chat.costgate import ChatCostGate as ChatCostGate
from kongming.chat.export import to_csv, to_xlsx
from kongming.chat.models import (
    ChartCandidate,
    ChartSuggestion,
    derive_artifacts,
    humanize_stats,
    resolve_column_labels,
    suggest_charts,
)
from kongming.chat.ports import (
    AttachmentChecker,
    ChatRepo,
    ChatStoreError,
    ExecuteRunner,
    ExplainRunner,
    GuardrailPort,
    MemoryPort,
    OperLogger,
    PendingToolReader,
    StreamStoreFactory,
    ToolSpecProvider,
)
from kongming.datasource.ports import DatasourceChecker
from kongming.rag.ports import ColumnCommentReader, QueryHistoryWriter
from kongming.trace import ensure_trace_id

log = logging.getLogger(__name__)

SESSION_TITLE_LEN = 30  # 标题=首问前 30 字符(chat spec)
SESSION_TITLE_MAX_LEN = 200  # 标题列上限(chat spec「会话重命名」;strip 后判定)
HISTORY_FILL_LIMIT = 5  # 后端补全最近 5 条 user 问句

AGENT_UNAVAILABLE_TEXT = "问答服务暂不可用,请稍后重试"


def blocked_friendly(reason: str | None) -> str:
    """按拦截原因前缀映射用户文案;问答与执行计划共用。"""
    r = reason or ""
    if r.startswith("MEMORY_SENSITIVE"):
        # 显式记忆敏感内容拒落(独立文案,与通用安全检查区分)
        return "该内容含敏感信息(密码/密钥/证件等),不予记忆"
    if r.startswith("GUARDRAIL"):
        # 输入护栏拦截(对外不点名规则,统一安全检查文案)
        return "内容未通过安全检查,请调整后重试"
    if r.startswith("DATA_SCOPE"):
        return "该查询超出您的数据范围,暂不支持"
    if r.startswith("RESERVED_TABLE"):
        # 中性不责备——常见成因是模型越界而非用户过错
        return "该问题涉及的数据不允许查询,请调整问法或咨询管理员"
    return "该查询涉及非只读操作,已被拒绝"


class TenantForbidden(Exception):
    """未绑定目标租户(或租户不存在/禁用——同形不区分)。"""


class AttachmentNotFound(Exception):
    """附件不存在/越权/已消费——同形 404。"""


class SessionNotFound(Exception):
    """会话不存在或不属于当前用户+租户(同形不区分,防探测)。"""


class InvalidDatasource(Exception):
    """数据源不存在/禁用/不归属当前租户(同形不区分,防 key 探测;入口预检)。"""


class MessageNotFound(Exception):
    """消息不存在或不属于当前用户+租户(执行计划;同形不区分,防探测)。"""


class ExplainNoSql(Exception):
    """消息无落库 SQL(user 问句/纯失败轮)——无计划可分析。"""


class InvalidTitle(Exception):
    """标题去空白后为空或超列上限(重命名;参数错误,映射 400)。"""


@dataclass(frozen=True)
class ChatOutcome:
    session_id: str | None
    success: bool
    verdict: str | None = None
    summary: str = ""
    # default_factory 用带参泛型别名:裸 list/dict 工厂在 pyright strict 下产物 Unknown
    insights: list[dict[str, str]] = field(default_factory=list[dict[str, str]])
    stats: dict[str, object] = field(default_factory=dict[str, object])
    columns: list[str] = field(default_factory=list[str])
    rows: list[list[object]] = field(default_factory=list[list[object]])
    sql: str = ""
    chart: ChartSuggestion = field(default_factory=lambda: ChartSuggestion("none"))
    # 有序图表候选(首候选即默认单图 chart)
    chart_candidates: list[ChartCandidate] = field(default_factory=list[ChartCandidate])
    retry_count: int = 0
    error: str | None = None
    # 原始技术错误(FAILED 时携带;error 为用户语言,细节经前端折叠披露)
    error_detail: str | None = None
    # messageId 打通:assistant 落库消息 id(反馈提交引用;不落库路径 None)
    # 记录执行时使用的数据源，与会话快照口径一致。
    message_id: str | None = None
    datasource_key: str = "default"
    # 待确认写工具调用，供前端展示确认卡；缺省为空列表。
    pending_tool_calls: list[dict[str, object]] = field(default_factory=list[dict[str, object]])
    # 供记忆后台任务使用，不序列化进响应模型。
    tenant_id: str = ""
    # 类型化产物(派生自 sql/chart/summary)
    artifacts: list[dict[str, object]] = field(default_factory=list[dict[str, object]])
    # 附件引用摘要(filename/mime/size)
    attachments: list[dict[str, object]] = field(default_factory=list[dict[str, object]])
    # Agent 返回的语义层命中，随 result_json 落库并回显；空列表表示未命中。
    metric_hits: list[dict[str, str | None]] = field(default_factory=list[dict[str, str | None]])
    # 文档道结构化来源(净化后正文存活标记投影;随 result_json 落库+回显)
    sources: list[dict[str, str]] = field(default_factory=list[dict[str, str]])
    # 本轮为显式记忆短路(隐式提取任务据此排除,防同内容重复落 IMPLICIT)
    memory_shortcut: bool = False
    # 问答准备期捕获的偏好人工版本快照(内部携带给隐式提取后台任务,
    # 与 tenant_id/memory_shortcut 同为不序列化字段;None=捕获失败,任务跳过)
    pref_revision: int | None = None
    # 呈现层列标签(与 columns 等长对齐;未命中回退原始列名;
    # 空列表=未接线/查询失败,消费方回退 columns)
    column_labels: list[str] = field(default_factory=list[str])


@dataclass(frozen=True)
class SessionSummary:
    """会话列表项(侧栏)。"""

    id: str
    title: str
    updated_at: object  # datetime;类型宽放避免领域层 import datetime 之外的耦合


@dataclass(frozen=True)
class MessageResult:
    """assistant 消息结构化应答要素(回显形态,与 ChatOutcome 的结果子集对齐)。"""

    columns: list[str] = field(default_factory=list[str])
    rows: list[list[object]] = field(default_factory=list[list[object]])
    insights: list[dict[str, str]] = field(default_factory=list[dict[str, str]])
    stats: dict[str, object] = field(default_factory=dict[str, object])
    chart: ChartSuggestion = field(default_factory=lambda: ChartSuggestion("none"))
    # 有序图表候选(读档优先;存量档缺失→空,消费方回落单图 chart)
    chart_candidates: list[ChartCandidate] = field(default_factory=list[ChartCandidate])
    error: str | None = None
    # 原始技术错误(读档;存量档缺失→None,前端不渲染折叠块)
    error_detail: str | None = None
    # 类型化产物(读档优先,缺档按要素重算)
    artifacts: list[dict[str, object]] = field(default_factory=list[dict[str, object]])
    # 呈现层列标签(读档优先;存量档缺失→空列表,消费方回退 columns)
    column_labels: list[str] = field(default_factory=list[str])
    # 语义层口径命中(读档优先;存量档缺失→空,不反查指标表重算)
    metric_hits: list[dict[str, str | None]] = field(default_factory=list[dict[str, str | None]])
    # 文档道来源(读档优先;存量档缺失→空;名称快照不回查文档表)
    sources: list[dict[str, str]] = field(default_factory=list[dict[str, str]])


@dataclass(frozen=True)
class SessionMessage:
    """会话消息回显项(user 问句 / assistant 要素)。"""

    id: str
    role: str
    question: str | None = None
    summary: str | None = None
    sql: str | None = None
    status: str = "SUCCESS"
    error: str | None = None
    # 原始技术错误(FAILED 回显;web 折叠「技术详情」)
    error_detail: str | None = None
    result: MessageResult | None = None
    # 反馈状态回显(assistant 消息;user 消息恒 None)
    rating: str | None = None
    corrected_sql: str | None = None
    feedback_comment: str | None = None
    # 附件引用摘要(回显徽标;空数组=无附件)
    attachments: list[dict[str, object]] = field(default_factory=list[dict[str, object]])


@dataclass(frozen=True)
class ExplainView:
    """执行计划视图:PASS 含计划树;BLOCKED/ERROR 含友好文案。"""

    success: bool
    plan: object | None = None
    error: str | None = None


@dataclass(frozen=True)
class _PreparedQuery:
    """问答前段产物(同步/流式共用同一编排口径)。"""

    tenant_id: str
    resolved_key: str
    sid: str | None
    effective_history: list[str]
    trace_id: str
    user_id: str = ""  # 护栏审计主体
    attachments: list[dict[str, object]] | None = None  # 附件摘要(归属已验)


@dataclass
class StreamHandle:
    """流式中继句柄:queue=待发帧,event 为 result 时 payload=ChatOutcome;
    task=后台消费任务(断线后照常跑完,调用方不 await)。"""

    queue: asyncio.Queue[tuple[str, object] | None]
    task: "asyncio.Task[None]"


# 后台流任务强引用(防 GC 边角;完成即摘)
_BACKGROUND_STREAM_TASKS: set["asyncio.Task[None]"] = set()


class ChatService:
    def __init__(
        self,
        repo: ChatRepo,
        agent: AgentClient,
        ds_checker: DatasourceChecker,
        history_writer: QueryHistoryWriter | None = None,  # 成功 SQL 回写(可选注入)
        explainer: ExplainRunner | None = None,  # 执行计划端口(组合根绑定)
        oper_logger: OperLogger | None = None,  # 导出审计(可选注入)
        stream_store_factory: StreamStoreFactory | None = None,  # 断线续存落库单元
        tool_specs: ToolSpecProvider | None = None,  # ReAct 授权工具注入
        pending_tools: PendingToolReader | None = None,  # 待确认调用聚合
        guardrail: GuardrailPort | None = None,  # prompt 安全护栏
        memory: MemoryPort | None = None,  # 用户长期记忆
        runner: ExecuteRunner | None = None,  # SQL 再执行端口
        attachment_checker: AttachmentChecker | None = None,
        implicit_memory_task: (  # 隐式提取任务(组合根绑定;None=不触发)
            "Callable[..., Coroutine[Any, Any, None]] | None"
        ) = None,
        column_comments: ColumnCommentReader | None = None,  # 列标签(可选注入)
        cost_gate: ChatCostGate | None = None,  # 成本双闸(可选注入)
    ) -> None:
        self._repo = repo
        self._agent = agent
        self._ds_checker = ds_checker
        self._history_writer = history_writer
        self._explainer = explainer
        self._oper_logger = oper_logger
        self._stream_store_factory = stream_store_factory
        self._tool_specs = tool_specs
        self._pending_tools = pending_tools
        self._guardrail = guardrail
        self._memory = memory
        self._runner = runner
        self._attachment_checker = attachment_checker
        self._implicit_memory_task = implicit_memory_task
        self._column_comments = column_comments
        self._cost_gate = cost_gate

    @property
    def agent_for_tasks(self) -> AgentClient:
        """后台任务用 agent 句柄(标题改写;AgentClient 无会话状态,响应后可安全复用)。"""
        return self._agent

    async def _resolve_tenant(self, user_id: str, tenant_key: str | None) -> tuple[str, str]:
        """X-Tenant-Id 解析:显式传入查白名单;缺省取用户默认租户(default 优先,
        否则最早授予)。返回 (tenant_id, tenant_key);未绑定一律 TenantForbidden。"""
        if tenant_key is not None:
            tenant_id = await self._repo.find_bound_tenant(user_id=user_id, tenant_key=tenant_key)
            if tenant_id is None:
                raise TenantForbidden(tenant_key)
            return tenant_id, tenant_key
        default = await self._repo.find_default_tenant(user_id=user_id)
        if default is None:
            raise TenantForbidden("<default>")
        return default

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
    ) -> _PreparedQuery:
        """问答前段(同步/流式共用唯一实现):租户→数据源预检→会话→history→
        trace_id;USER 消息落库也在此(流式路径须在 SSE 响应开始前完成)。"""
        tenant_id, resolved_key = await self._resolve_tenant(user_id, tenant_key)

        # 成本双闸(速率+配额)——租户解析后、烧 LLM 前 fail-fast;
        # 未配置时跳过成本限制，不影响现有问答行为。
        if self._cost_gate is not None:
            await self._cost_gate.check_login(tenant_id=tenant_id, user_id=user_id)

        # 数据源入口预检:非 default 校验存在+启用+归属当前租户,不符合条件
        # 返回统一 400;不调用 Agent、不建会话,连接信息留在控制面(ADR-002)
        if datasource_key != "default" and not await self._ds_checker.is_usable(
            datasource_key, tenant_id
        ):
            raise InvalidDatasource(datasource_key)

        # 会话:显式传入校验归属(不命中 404 同形);缺省且 persist 时新建
        sid: str | None = None
        if session_id is not None:
            sid = await self._repo.find_session_owned(
                session_id=session_id, user_id=user_id, tenant_id=tenant_id
            )
            if sid is None:
                raise SessionNotFound(session_id)
        elif persist:
            sid = await self._repo.create_session(
                user_id=user_id,
                tenant_id=tenant_id,
                title=question[:SESSION_TITLE_LEN],
                datasource_key=datasource_key,
            )

        # history:显式优先;否则按会话从消息表补全(最近 5 条)
        effective_history = history or (
            await self._repo.recent_user_questions(sid, limit=HISTORY_FILL_LIMIT)
            if sid is not None
            else []
        )

        # 附件归属校验(当前用户+租户+未消费;任一无效 404 同形,
        # 不进 agent 回调);checker 未接线(部署缺陷)且引用非空=fail-closed
        attachments: list[dict[str, object]] | None = None
        if attachment_ids:
            if self._attachment_checker is None:
                log.error("AttachmentChecker 未注入但请求携带附件")
                raise AttachmentNotFound(",".join(attachment_ids))
            attachments = await self._attachment_checker.validate_owned(
                ids=attachment_ids, user_id=user_id, tenant_id=tenant_id
            )
            if attachments is None:
                raise AttachmentNotFound(",".join(attachment_ids))

        trace_id = ensure_trace_id()  # 与响应头 X-Trace-Id / 用量落库同一值
        if sid is not None:
            await self._repo.insert_message(
                session_id=sid,
                role="USER",
                question=question,
                status="SUCCESS",
                attachments=attachments,
            )
            await self._repo.commit()
        return _PreparedQuery(
            tenant_id=tenant_id,
            resolved_key=resolved_key,
            sid=sid,
            effective_history=effective_history,
            trace_id=trace_id,
            user_id=user_id,
            attachments=attachments,
        )

    async def _react_tools(
        self, user_id: str, agent_mode: str, tenant_id: str
    ) -> list[dict[str, object]] | None:
        """React 模式下按用户授权返回工具；recipe 模式或未配置时返回 None。"""
        if agent_mode != "react" or self._tool_specs is None:
            return None
        return await self._tool_specs.authorized_tools(user_id, tenant_id)

    async def _memory_short_circuit(
        self,
        p: _PreparedQuery,
        *,
        question: str,
        persist: bool,
        datasource_key: str,
    ) -> ChatOutcome | None:
        """识别并处理“记住……”请求；非记忆请求或未配置记忆服务时返回 None，继续问答。"""
        if self._memory is None:
            return None
        content = await self._memory.explicit_intent(question)
        if content is None:
            return None
        saved = await self._memory.form_explicit(
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
            repo=self._repo,
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
        if self._memory is None:
            return "", None
        parts: list[str] = []
        try:
            mem = await self._memory.recall_hint(
                user_id=user_id, tenant_id=tenant_id, question=question
            )
            if mem:
                parts.append(mem)
        except Exception:  # noqa: BLE001  # 记忆读取失败不影响问答
            log.warning("记忆召回异常(降级无注入)", exc_info=True)
        try:
            pref = await self._memory.preference_hint(user_id=user_id, tenant_id=tenant_id)
            if pref:
                parts.append(pref)
        except Exception:  # noqa: BLE001  # 偏好读取失败不影响问答
            log.warning("偏好注入异常(降级无注入)", exc_info=True)
        revision: int | None = None
        try:
            revision = await self._memory.current_revision(user_id=user_id, tenant_id=tenant_id)
        except Exception:  # noqa: BLE001  # 版本捕获失败只放弃自动写入
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
        if self._guardrail is None:
            return False, question, history
        blocked, processed = await self._guardrail.check_input(
            trace_id=trace_id,
            user_id=user_id,
            tenant_id=tenant_id,
            texts=[question, *history],
        )
        return blocked, processed[0], processed[1:]

    async def _guard_blocked_outcome(
        self,
        p: _PreparedQuery,
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
            repo=self._repo,
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
        memory_hint, pref_revision = await self._recall_memory_hint(
            user_id=user_id, tenant_id=p.tenant_id, question=question
        )
        try:
            reply = await self._agent.query(
                trace_id=p.trace_id,
                tenant_key=p.resolved_key,
                question=question,
                user_id=user_id,
                history=eff_history,
                datasource_key=datasource_key,
                react_tools=await self._react_tools(user_id, agent_mode, p.tenant_id),
                memory_hint=memory_hint,
                attachment_ids=[str(a["id"]) for a in p.attachments or []],
            )
        except AgentUnavailable:
            log.warning("agent 不可达 trace_id=%s", p.trace_id)
            return await self._finalize_unavailable(
                sid=p.sid,
                repo=self._repo,
                datasource_key=datasource_key,
                attachments=p.attachments,
            )
        outcome = await self._finalize_reply(
            reply=reply,
            sid=p.sid,
            tenant_id=p.tenant_id,
            datasource_key=datasource_key,
            question=orig_question,  # 保存原始问句；脱敏文本仅传给 Agent，不写入召回库。
            persist=persist,
            repo=self._repo,
            history_writer=self._history_writer,
            trace_id=p.trace_id,
            attachments=p.attachments,
            user_id=user_id,
        )
        # 同步路径经 outcome 携带版本快照给 router 的后台任务
        return dataclasses.replace(outcome, pref_revision=pref_revision)

    # ============ 流式编排(query-streaming) ============

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
        queue: asyncio.Queue[tuple[str, object] | None] = asyncio.Queue()
        # 显式记忆短路先于输入护栏(原始问句判定,与同步同序;流式同语义:
        # result 直入队不起后台任务)
        short = await self._memory_short_circuit(
            p, question=question, persist=persist, datasource_key=datasource_key
        )
        if short is not None:
            await queue.put(("result", short))
            await queue.put(None)
            task = asyncio.create_task(asyncio.sleep(0))
            _BACKGROUND_STREAM_TASKS.add(task)
            task.add_done_callback(_BACKGROUND_STREAM_TASKS.discard)
            return StreamHandle(queue=queue, task=task)
        # 输入护栏(流式):拦截→result(REJECTED 形态)+ 终止标记直入队,
        # 不起后台 agent 任务(与同步同语义:不回调 Agent 面)
        orig_question = question  # 回写 query_history 须取原始问句
        blocked, question, eff_history = await self._guard_input(
            user_id=user_id,
            tenant_id=p.tenant_id,
            trace_id=p.trace_id,
            question=question,
            history=p.effective_history,
        )
        if blocked:
            outcome = await self._guard_blocked_outcome(
                p, question=question, persist=persist, datasource_key=datasource_key
            )
            await queue.put(("result", outcome))
            await queue.put(None)
            task = asyncio.create_task(asyncio.sleep(0))  # 空任务占位(句柄形态)
            _BACKGROUND_STREAM_TASKS.add(task)
            task.add_done_callback(_BACKGROUND_STREAM_TASKS.discard)
            return StreamHandle(queue=queue, task=task)
        memory_hint, pref_revision = await self._recall_memory_hint(
            user_id=user_id, tenant_id=p.tenant_id, question=question
        )
        task = asyncio.create_task(
            self._consume_agent_stream(
                p,
                user_id=user_id,
                question=question,
                orig_question=orig_question,
                persist=persist,
                datasource_key=datasource_key,
                queue=queue,
                react_tools=await self._react_tools(user_id, agent_mode, p.tenant_id),
                attachment_ids=[str(a["id"]) for a in p.attachments or []],
                eff_history=eff_history,
                memory_hint=memory_hint,
                pref_revision=pref_revision,  # 后台任务携带版本快照
            )
        )
        _BACKGROUND_STREAM_TASKS.add(task)
        task.add_done_callback(_BACKGROUND_STREAM_TASKS.discard)
        return StreamHandle(queue=queue, task=task)

    async def _consume_agent_stream(
        self,
        p: _PreparedQuery,
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
        """后台消费 agent 流至终态:过程帧透传;result 帧先落库
        (独立 StreamStore 会话)再富化入队;传输失败收敛 error 终态+FAILED 落库。
        前端断线不影响本任务(断线续存);落库失败不阻断终态帧(与同步容错一致)。
        异常收敛铁律:本方法任何退出路径都必须入队终止标记(None)——中继生成器
        靠它收束,缺失即前端永久等心跳。"""
        outcome: ChatOutcome | None = None
        error_text: str | None = None
        try:

            async def on_frame(event: str, data_json: str) -> None:
                nonlocal outcome, error_text
                if event == "result":
                    if outcome is not None:
                        # 终态幂等守卫:契约恰一个终态帧,首个即权威——
                        # 违约多发的 result 不得二次落库/回写/触发标题任务
                        log.warning("agent 流重复 result 帧(忽略) trace_id=%s", p.trace_id)
                        return
                    # 业务终态:解析→落库(独立会话)→富化;原帧不透传
                    try:
                        reply = parse_agent_reply(json.loads(data_json))
                    except (ValueError, TypeError, AttributeError) as e:
                        log.warning("agent result 帧解析失败: %s", e)
                        error_text = AGENT_UNAVAILABLE_TEXT
                        return
                    outcome = await self._stream_finalize(
                        reply=reply,
                        p=p,
                        question=orig_question or question,  # 原始问句(回写口径)
                        persist=persist,
                        datasource_key=datasource_key,
                        pref_revision=pref_revision,
                    )
                elif event == "error":
                    # agent 意外异常帧:等价传输层失败,由 api 统一收敛(不透传)
                    error_text = AGENT_UNAVAILABLE_TEXT
                elif event in ("stage", "sql", "summary", "tool_call", "trace"):
                    # 过程事件白名单透传(事件名/形状由 api 收口;
                    # trace=诊断信号通道,不进用户响应只作观测);
                    # 单帧畸形跳过不中断
                    try:
                        data = json.loads(data_json)
                    except ValueError:
                        log.warning("agent 过程帧解析失败(跳帧): %s", data_json[:100])
                        return
                    if isinstance(data, dict):
                        # isinstance 收窄产物键型未知,cast 对齐队列元素类型(运行时不变)
                        typed = cast("dict[str, object]", data)
                        await queue.put((event, typed))

            await self._agent.query_stream(
                trace_id=p.trace_id,
                tenant_key=p.resolved_key,
                question=question,
                user_id=user_id,
                history=eff_history if eff_history is not None else p.effective_history,
                datasource_key=datasource_key,
                on_frame=on_frame,
                react_tools=react_tools,
                memory_hint=memory_hint,
                attachment_ids=attachment_ids,
            )
        except AgentUnavailable:
            log.warning("agent 流式不可达 trace_id=%s", p.trace_id)
            error_text = AGENT_UNAVAILABLE_TEXT
        except Exception:
            # 响应格式错误或持久化异常也要写入 error 终态，避免前端一直等待。
            log.exception("流式消费任务异常 trace_id=%s", p.trace_id)
            error_text = AGENT_UNAVAILABLE_TEXT
        try:
            if outcome is not None:
                # 业务终态已落库(result 先finalize):即使读尾传输异常也按成功下发,
                # 不叠加 FAILED 消息(双落库收敛)
                await queue.put(("result", outcome))
            elif error_text is not None:
                # 传输层/意外失败终态:FAILED 落库(独立会话)+ error 帧
                await self._stream_finalize_unavailable(p, datasource_key=datasource_key)
                await queue.put(("error", {"error": error_text, "reason_code": None}))
            else:
                # agent 流正常结束但无终态帧(契约破坏):与不可达同收敛
                log.error("agent 流结束但缺终态帧 trace_id=%s", p.trace_id)
                await self._stream_finalize_unavailable(p, datasource_key=datasource_key)
                await queue.put(("error", {"error": AGENT_UNAVAILABLE_TEXT, "reason_code": None}))
        finally:
            await queue.put(None)  # 终止标记(生成器据此收束;必达)

    async def _stream_finalize(
        self,
        *,
        reply: AgentReply,
        p: _PreparedQuery,
        question: str,
        persist: bool,
        datasource_key: str,
        pref_revision: int | None = None,  # 准备期人工版本快照
    ) -> ChatOutcome:
        """流式终态落库(独立 StreamStore 会话;断线后照常执行)+标题任务触发。"""
        title_trigger: Callable[[str], Coroutine[Any, Any, None]] | None = None
        if self._stream_store_factory is not None:
            async with self._stream_store_factory() as store:
                outcome = await self._finalize_reply(
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
                )
                title_trigger = store.trigger_title_rewrite
        else:  # 组合根未接线(部署缺陷):终态仍要回给前端,仅放弃断线持久化
            log.error("stream_store_factory 未注入,流式终态不落库")
            return await self._finalize_reply(
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
            )
        if (
            title_trigger is not None
            and outcome.success
            and persist
            and outcome.session_id is not None
        ):
            # 标题任务触发条件与同步端点一致;result 帧已可先发,这里不阻塞
            t = asyncio.create_task(title_trigger(outcome.session_id))
            _BACKGROUND_STREAM_TASKS.add(t)
            t.add_done_callback(_BACKGROUND_STREAM_TASKS.discard)
            # 隐式记忆(流式同挂;显式短路轮排除;
            # 任务经注入传入——service 不得 import memory_tasks,违反零框架契约);
            # 携带准备期版本快照,捕获失败(None)不挂任务
            if (
                outcome.tenant_id
                and not outcome.memory_shortcut
                and pref_revision is not None
                and self._implicit_memory_task is not None
            ):
                t2 = asyncio.create_task(
                    self._implicit_memory_task(
                        user_id=p.user_id,
                        tenant_id=outcome.tenant_id,
                        question=question,
                        answer=outcome.summary,
                        agent=self._agent,
                        pref_revision=pref_revision,
                    )
                )
                _BACKGROUND_STREAM_TASKS.add(t2)
                t2.add_done_callback(_BACKGROUND_STREAM_TASKS.discard)
        return outcome

    async def _stream_finalize_unavailable(
        self, p: _PreparedQuery, *, datasource_key: str = "default"
    ) -> None:
        """流式传输层失败的 FAILED 落库(独立会话);失败仅日志(不阻断 error 帧)。"""
        if self._stream_store_factory is None:
            log.error("stream_store_factory 未注入,FAILED 终态不落库")
            return
        try:
            async with self._stream_store_factory() as store:
                await self._finalize_unavailable(
                    sid=p.sid,
                    repo=store.repo,
                    datasource_key=datasource_key,
                    attachments=p.attachments,
                )
        except Exception:  # noqa: BLE001  # 断线续存落库兜底:不阻断终态帧下发
            log.warning("流式 FAILED 终态落库异常 session=%s", p.sid, exc_info=True)

    async def _resolve_labels(
        self, *, tenant_id: str, datasource_key: str, columns: list[str], sql: str
    ) -> list[str]:
        """按 SQL 投影把列名映射为业务标签；无法溯源或查询失败时返回空列表，由调用方回退原列名。"""
        if not columns or self._column_comments is None:
            return []
        try:
            table_comments = await self._column_comments.column_comments(
                tenant_id=tenant_id, datasource_key=datasource_key
            )
        except Exception:  # noqa: BLE001  # 标签增强永不阻断主流程
            log.warning("列注释查询异常(标签回退原始列名)", exc_info=True)
            return []
        return resolve_column_labels(columns, table_comments, sql)

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
        trace_id: str = "",  # pending_tool_calls 聚合键(缺省空=不聚合)
        user_id: str = "",  # 输出护栏审计主体
        attachments: list[dict[str, object]] | None = None,
    ) -> ChatOutcome:
        """终态落库与响应组装(同步/流式共用唯一实现):
        三态映射 → chart 建议 → error 文案 → result_json 落库(容错)→ 回写。
        summary/insights 经输出护栏(泄漏拦截替换/凭证脱敏)后才落库返回。"""
        # 四态映射:PASS→SUCCESS;BLOCKED→REJECTED;NO_DATA→NO_DATA
        # (知识未覆盖如实说明,中性形态);ERROR/重试耗尽→FAILED
        if reply.success:
            status = "SUCCESS"
        elif reply.verdict == "BLOCKED":
            status = "REJECTED"
        elif reply.verdict == "NO_DATA":
            status = "NO_DATA"
        else:
            status = "FAILED"
        # 输出护栏(扩面):summary/insights + FAILED 错误原文(驱动回显
        # 可能含凭证形态)+ sql 文本(注释可藏提示词片段)全路径过滤;REJECTED 的
        # error 为 blocked_friendly 系统文案,过滤无害。
        summary = reply.summary
        insights = reply.insights
        sql_text = reply.sql
        error_raw = reply.error
        if self._guardrail is not None:
            tail = [error_raw or "", sql_text]
            filtered = await self._guardrail.filter_output(
                trace_id=trace_id,
                user_id=user_id,
                tenant_id=tenant_id,
                texts=[summary, *[i["text"] for i in insights], *tail],
            )
            summary = filtered[0]
            n_ins = len(insights)
            insights = [
                {"type": i["type"], "text": t}
                for i, t in zip(insights, filtered[1 : 1 + n_ins], strict=True)
            ]
            error_raw = filtered[1 + n_ins] or None
            sql_text = filtered[2 + n_ins]
        # 形状→有序候选(纯函数,含 sql 供 pie 可加闸);首候选展平为存量
        # 「默认单图」,chart 字段语义不变
        chart_candidates = (
            suggest_charts(reply.columns, reply.rows, sql=sql_text) if reply.success else []
        )
        chart = chart_candidates[0].as_default() if chart_candidates else ChartSuggestion("none")
        # 呈现层列标签(仅成功有结果集时;物理名/显示名双轨,SQL 不动)
        column_labels = (
            await self._resolve_labels(
                tenant_id=tenant_id,
                datasource_key=datasource_key,
                columns=reply.columns,
                sql=sql_text,
            )
            if reply.success
            else []
        )
        # 统计键用户可读化(标签单源;落库即事实源,回显读档幂等再应用)
        stats = humanize_stats(reply.stats, reply.columns, column_labels)

        error: str | None = None
        error_detail: str | None = None
        if status == "REJECTED":
            error = blocked_friendly(error_raw)
        elif status == "NO_DATA":
            # 中性形态——说明文案经 summary 传达(过护栏),不走错误包装
            summary = (
                error_raw or "当前数据源未找到回答该问题所需的数据,可切换数据源或调整问法后重试"
            )
            error = None
        elif status == "FAILED":
            # 用户语言进 error,原始技术错误进 error_detail(web 折叠披露)
            error = "查询执行失败,请调整问法后重试"
            error_detail = error_raw

        # 产物派生(三路径同源);如实说明不派生产物卡
        # (derive_artifacts 对非空 summary 会派生 document 卡,NO_DATA 跳过)
        artifacts = (
            []
            if status == "NO_DATA"
            else derive_artifacts(
                sql=sql_text, chart=chart, summary=summary, datasource_key=datasource_key
            )
        )
        mid = None
        if sid is not None:
            # chart 完整对象(x/y)+ error 文案随档落库,回显读档即事实源;
            # 存量 形态(裸 chartType)由 _to_message 重算兜底。
            # 落库段整体容错:agent 回调期间会话可能被并发删除(他端),
            # FK 违约不炸 500——结果照常返回,仅放弃该轮持久化。
            try:
                result_json = json.dumps(
                    {
                        "columns": reply.columns,
                        "column_labels": column_labels,
                        "rows": reply.rows,
                        "insights": insights,
                        "stats": stats,
                        "chart": {"type": chart.type, "x": chart.x, "y": chart.y},
                        "chart_candidates": [  # 随档(回显读档即事实源)
                            {
                                "type": c.type,
                                "x": c.x,
                                "y": list(c.y),
                                "series": c.series,
                            }
                            for c in chart_candidates
                        ],
                        "artifacts": artifacts,
                        "metric_hits": reply.metric_hits,  # 口径依据随档
                        "sources": reply.sources,  # 文档道来源随档
                        "error": error,
                        "error_detail": error_detail,  # 原始技术错误随档
                    },
                    ensure_ascii=False,
                    default=str,
                )
                # 四质量信号随终态单点落库(trace_id 已在函数签名;
                # resolved_question 仅 agent 实际改写且非空时携带;prompt_version
                # 随每轮 QueryOut 回传;同步/流式共用本出口)
                mid = await repo.insert_message(
                    session_id=sid,
                    role="ASSISTANT",
                    sql_text=sql_text or None,
                    result_json=result_json,
                    summary=summary or None,
                    status=status,
                    attachments=attachments,
                    trace_id=trace_id or None,
                    retry_count=reply.retry_count,
                    resolved_question=reply.resolved_question,
                    prompt_version=reply.prompt_version,
                )
                await self._persist_touch_quietly(repo, sid)
            except ChatStoreError:
                mid = None  # 落库失败即无消息 id(与未落库语义一致)
                log.warning("assistant 消息落库失败(会话可能已被删除) session=%s", sid)
                await repo.rollback()

        # 成功 SQL 回写 query_history:最终 SUCCESS 判定后;独立 try 不阻断;
        # persist=False 为测试通道不触发;空结果的成功查询同样回写(不依赖行数)。
        if reply.success and persist and history_writer is not None and reply.sql.strip():
            try:
                await history_writer.writeback(
                    tenant_id=tenant_id,
                    datasource_key=datasource_key,
                    question=question,  # 原始问句(与审计口径一致)
                    sql_text=reply.sql,
                    # 伴生:样本可见集合=执行主体可见范围(缺省=公共样本)
                    caller_user_id=user_id or None,
                )
            except Exception:  # noqa: BLE001  # 回写永不阻断主流程(writeback 内已兜底)
                log.warning("query_history 回写异常(不阻断)", exc_info=True)

        # 聚合本 trace 待确认写工具调用(前端确认卡);读取失败不阻断响应
        pending: list[dict[str, object]] = []
        if trace_id and self._pending_tools is not None:
            try:
                pending = await self._pending_tools.list_pending(trace_id)
            except Exception:  # noqa: BLE001  # 附加信息聚合永不阻断主流程
                log.warning("pending_tool_calls 聚合异常 trace_id=%s", trace_id, exc_info=True)

        return ChatOutcome(
            session_id=sid,
            success=reply.success,
            verdict=reply.verdict,
            summary=summary,
            insights=insights,
            stats=stats,
            columns=reply.columns,
            rows=reply.rows,
            sql=sql_text,
            chart=chart,
            chart_candidates=chart_candidates,
            retry_count=reply.retry_count,
            error=error,
            error_detail=error_detail,
            message_id=mid,
            datasource_key=datasource_key,
            pending_tool_calls=pending,
            tenant_id=tenant_id,
            artifacts=[dict(a) for a in artifacts],
            attachments=[dict(a) for a in attachments or []],
            column_labels=column_labels,
            metric_hits=reply.metric_hits,
            sources=reply.sources,
        )

    async def _finalize_unavailable(
        self,
        *,
        sid: str | None,
        repo: ChatRepo,
        datasource_key: str = "default",
        attachments: list[dict[str, object]] | None = None,  # 带附件轮与成功路径对称
    ) -> ChatOutcome:
        """agent 不可达终态(同步/流式同构):FAILED 消息落库(容错)+友好错误。"""
        mid: str | None = None
        if sid is not None:
            # 落库容错与成功路径对称:回调期间会话可能被并发删除
            try:
                mid = await repo.insert_message(
                    session_id=sid,
                    role="ASSISTANT",
                    result_json=json.dumps(
                        # artifacts 显式空数组:summary 列存了友好文案,
                        # 缺档会让 _to_message 重算出 document 卡,与同步响应
                        # ChatOutcome.artifacts=[] 不同源;冻结空档保三路径一致
                        {"error": AGENT_UNAVAILABLE_TEXT, "artifacts": []},
                        ensure_ascii=False,
                    ),
                    summary=AGENT_UNAVAILABLE_TEXT,
                    status="FAILED",
                    attachments=attachments,  # 与成功路径回显对称
                )
                await self._persist_touch_quietly(repo, sid)
            except ChatStoreError:
                mid = None  # 落库失败即无消息 id(与未落库语义一致)
                log.warning("FAILED 消息落库失败(会话可能已被删除) session=%s", sid)
                await repo.rollback()
        return ChatOutcome(
            session_id=sid,
            success=False,
            error=AGENT_UNAVAILABLE_TEXT,
            message_id=mid,
            datasource_key=datasource_key,  # 与同步分支口径一致
        )

    async def _persist_touch_quietly(self, repo: ChatRepo, session_id: str) -> None:
        """touch+commit;FK 违约(会话被并发删除)仅日志,不影响响应。"""
        try:
            await repo.touch_session(session_id)
            await repo.commit()
        except ChatStoreError:
            log.warning("会话 touch 失败(可能已被删除) session=%s", session_id)
            await repo.rollback()

    # ============ 会话列表/消息历史/删除 ============

    async def list_sessions(
        self, *, user_id: str, tenant_key: str | None, limit: int, keyword: str = ""
    ) -> list[SessionSummary]:
        tenant_id, _ = await self._resolve_tenant(user_id, tenant_key)
        rows = await self._repo.list_sessions(
            user_id=user_id, tenant_id=tenant_id, limit=limit, keyword=keyword.strip()
        )
        return [
            SessionSummary(id=str(r["id"]), title=str(r["title"]), updated_at=r["updated_at"])
            for r in rows
        ]

    async def rename_session(
        self, *, user_id: str, tenant_key: str | None, session_id: str, title: str
    ) -> SessionSummary:
        """重命名:标题去空白后判空与列上限;归属原子 UPDATE。成功后该标题
        即人工设定,不再被 LLM 自动改写覆盖(改写走 expected 条件更新,
        见 title_tasks)。"""
        tenant_id, _ = await self._resolve_tenant(user_id, tenant_key)
        cleaned = title.strip()
        if not cleaned or len(cleaned) > SESSION_TITLE_MAX_LEN:
            raise InvalidTitle(title)
        row = await self._repo.rename_session(
            session_id=session_id, user_id=user_id, tenant_id=tenant_id, title=cleaned
        )
        if row is None:
            raise SessionNotFound(session_id)
        await self._repo.commit()
        return SessionSummary(
            id=str(row["id"]), title=str(row["title"]), updated_at=row["updated_at"]
        )

    async def session_messages(
        self, *, user_id: str, tenant_key: str | None, session_id: str
    ) -> list[SessionMessage]:
        tenant_id, _ = await self._resolve_tenant(user_id, tenant_key)
        sid = await self._repo.find_session_owned(
            session_id=session_id, user_id=user_id, tenant_id=tenant_id
        )
        if sid is None:
            raise SessionNotFound(session_id)
        rows = await self._repo.list_messages(session_id=sid)
        return [self._to_message(r) for r in rows]

    async def delete_session(
        self, *, user_id: str, tenant_key: str | None, session_id: str
    ) -> None:
        tenant_id, _ = await self._resolve_tenant(user_id, tenant_key)
        deleted = await self._repo.delete_session(
            session_id=session_id, user_id=user_id, tenant_id=tenant_id
        )
        if not deleted:
            raise SessionNotFound(session_id)
        await self._repo.commit()

    # ============ 结果导出(chat spec「结果导出 CSV/Excel」) ============

    async def export_result(
        self,
        *,
        user_id: str,
        tenant_key: str | None,
        fmt: str,
        columns: list[str],
        rows: list[list[object]],
        max_rows: int,
    ) -> tuple[bytes, str, str]:
        """所见即所导(不重跑 SQL):仅做租户白名单复校 + 格式转换;
        返回 (内容字节, media_type, 扩展名)。截断仅 warn,不报错(chat spec)。"""
        await self._resolve_tenant(user_id, tenant_key)
        if fmt == "csv":
            content, truncated = to_csv(columns, rows, max_rows=max_rows)
            media_type, ext = "text/csv; charset=utf-8", "csv"
        else:
            content, truncated = to_xlsx(columns, rows, max_rows=max_rows)
            media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ext = "xlsx"
        if truncated:
            log.warning("导出行数截断: %d → %d 行", len(rows), max_rows)
        # 导出审计:数据外发可见性(audit spec 铁律,与 SqlAuditWriter 同语义);
        # 写失败不阻断导出,rollback 也包保护——连接级双失败时异常不逃逸
        # (裸 rollback 再抛会把导出变 500)
        if self._oper_logger is not None:
            try:
                await self._oper_logger.add(
                    user_id,
                    "chat.export",
                    None,
                    {"fmt": fmt, "rows": len(rows), "truncated": truncated},
                )
                await self._repo.commit()
            except Exception:  # noqa: BLE001
                log.warning("导出审计落库失败(不阻断)", exc_info=True)
                with contextlib.suppress(Exception):
                    await self._repo.rollback()
        return content, media_type, ext

    # ============ 执行计划(chat spec「EXPLAIN 执行计划」) ============

    async def rerun_artifact(
        self, *, user_id: str, tenant_key: str | None, message_id: str, sql: str
    ) -> ChatOutcome:
        """SQL 卡再执行:归属校验(会话快照数据源)+ 六层防御(复用
        internal 执行)+ 结果落新 assistant 消息(不覆盖原 artifact);
        输出经护栏(_finalize_reply 同源)。"""
        tenant_id, resolved_key = await self._resolve_tenant(user_id, tenant_key)
        target = await self._repo.find_explain_target(
            message_id=message_id, user_id=user_id, tenant_id=tenant_id
        )
        if target is None:
            raise MessageNotFound(message_id)
        ds_key = str(target["datasource_key"])
        if ds_key != "default" and not await self._ds_checker.is_usable(ds_key, tenant_id):
            raise InvalidDatasource(ds_key)
        # ExecuteRunner 端口由组合根绑定 internal.service.execute(与 EXPLAIN 同范式);
        # rerun 全程不用 explainer,不做无谓检查
        if self._runner is None:  # 组合根未接线(部署缺陷)——友好降级
            log.error("ExecuteRunner 未注入,再执行不可用")
            return await self._finalize_unavailable(
                sid=None, repo=self._repo, datasource_key=ds_key
            )
        trace_id = ensure_trace_id()  # 执行与输出护栏审计同一 trace
        result = await self._runner.run(
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
            repo=self._repo,
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
        tenant_id, resolved_key = await self._resolve_tenant(user_id, tenant_key)
        target = await self._repo.find_explain_target(
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
        if ds_key != "default" and not await self._ds_checker.is_usable(ds_key, tenant_id):
            raise InvalidDatasource(ds_key)
        if self._explainer is None:  # 组合根未接线(部署缺陷)——友好降级不裸 500
            log.error("ExplainRunner 未注入,执行计划不可用")
            return ExplainView(success=False, error="执行计划暂不可用,请稍后重试")
        outcome = await self._explainer.explain(
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

    @staticmethod
    def _to_message(row: dict[str, object]) -> SessionMessage:
        """行 → 回显项;result_json 读档优先,存量(仅单图/形态)缺失候选时
        以 suggest_charts 纯函数确定性重算补全(落库输入=原始输入,重算=原始输出)。"""
        role = str(row["role"])
        result: MessageResult | None = None
        error: str | None = None
        error_detail: str | None = None
        rj = row.get("result_json")
        if role == "ASSISTANT" and rj:
            try:
                parsed = json.loads(str(rj))
            except json.JSONDecodeError:
                parsed = None
            # 合法但非 dict 的 JSON(null/数组等)一律按空档处理,不炸回显
            # (json.loads 的 Any 经 isinstance 收窄键型仍 Unknown,真分支 cast 一次)
            d = cast("dict[str, object]", parsed) if isinstance(parsed, dict) else {}
            cols_raw = d.get("columns")
            rows_raw = d.get("rows")
            cols = (
                [str(c) for c in cast("list[object]", cols_raw)]
                if isinstance(cols_raw, list)
                else []
            )
            # 元素级窄化:畸形 JSON 深防御(服务端自身不产生,防手改库)
            rws = (
                [
                    list(cast("list[object]", r))
                    for r in cast("list[object]", rows_raw)
                    if isinstance(r, (list, tuple))
                ]
                if isinstance(rows_raw, list)
                else []
            )
            ins_raw = d.get("insights")
            # 候选读档优先;存量档(仅单图/形态)回落解析,缺档重算
            cands_raw = d.get("chart_candidates")
            chart_candidates: list[ChartCandidate] = []
            if isinstance(cands_raw, list):
                for c in cast("list[object]", cands_raw):
                    if not isinstance(c, dict):
                        continue
                    cd = cast("dict[str, object]", c)
                    type_raw = cd.get("type")
                    if not isinstance(type_raw, str):
                        continue
                    y_raw_list = cd.get("y")
                    x_c = cd.get("x")
                    series_raw = cd.get("series")
                    chart_candidates.append(
                        ChartCandidate(
                            type=type_raw,
                            x=None if x_c is None else str(x_c),
                            y=(
                                tuple(str(v) for v in cast("list[object]", y_raw_list))
                                if isinstance(y_raw_list, list)
                                else ()
                            ),
                            series=None if series_raw is None else str(series_raw),
                        )
                    )
            chart_raw = d.get("chart")
            # isinstance 守卫后 cast 收窄键型
            chart_d = cast("dict[str, object]", chart_raw) if isinstance(chart_raw, dict) else None
            if chart_candidates:
                chart = chart_candidates[0].as_default()
            elif chart_d is not None and chart_d.get("type"):
                # 存量单图档:按档渲染单图,候选置空(前端回落单图绑定)
                x_raw = chart_d.get("x")
                y_raw = chart_d.get("y")
                chart = ChartSuggestion(
                    type=str(chart_d["type"]),
                    x=None if x_raw is None else str(x_raw),
                    y=None if y_raw is None else str(y_raw),
                )
            else:
                # 存量兼容:只存 chartType(无 x/y)/缺档 → 纯函数重算
                chart_candidates = suggest_charts(cols, rws, sql=str(row.get("sql_text") or ""))
                chart = (
                    chart_candidates[0].as_default()
                    if chart_candidates
                    else ChartSuggestion("none")
                )
            err_raw = d.get("error")
            error = str(err_raw) if err_raw is not None else None
            # 读档:原始技术错误(存量档缺失→None)
            err_detail_raw = d.get("error_detail")
            if err_detail_raw is not None:
                error_detail = str(err_detail_raw)
            stats_raw = d.get("stats")
            # labels 先行读出供统计键归一;存量英文键幂等升级
            col_labels = (
                [str(x) for x in cast("list[object]", lb_raw)]
                if isinstance(lb_raw := d.get("column_labels"), list)
                else []
            )
            stats_h = (
                humanize_stats(dict(cast("dict[str, object]", stats_raw)), cols, col_labels)
                if isinstance(stats_raw, dict)
                else {}
            )
            # artifacts 读档;缺档(前存量)按要素确定性重算
            art_raw = d.get("artifacts")
            if isinstance(art_raw, list):
                artifacts = [
                    dict(cast("dict[str, object]", a))
                    for a in cast("list[object]", art_raw)
                    if isinstance(a, dict)
                ]
            else:
                artifacts = [
                    dict(a)
                    for a in derive_artifacts(
                        sql=str(row.get("sql_text") or ""),
                        chart=chart,
                        summary=str(row.get("summary") or ""),
                        datasource_key=str(row.get("datasource_key") or "default"),
                    )
                ]
            result = MessageResult(
                columns=cols,
                rows=rws,
                insights=(
                    [
                        dict(cast("dict[str, str]", i))
                        for i in cast("list[object]", ins_raw)
                        if isinstance(i, dict)
                    ]
                    if isinstance(ins_raw, list)
                    else []
                ),
                stats=stats_h,
                chart=chart,
                chart_candidates=chart_candidates,
                error=error,
                error_detail=error_detail,
                artifacts=artifacts,
                column_labels=col_labels,  # 读档;存量档缺失→空(消费方回退 columns)
                metric_hits=(  # 读档;存量档缺失→空,SHALL NOT 反查指标表重算
                    [
                        {
                            "metric_id": str(hd.get("metric_id") or ""),
                            "metric_name": str(hd.get("metric_name") or ""),
                            "sql_template": str(hd.get("sql_template") or ""),
                            "updated_at": (
                                str(hd["updated_at"]) if hd.get("updated_at") is not None else None
                            ),
                        }
                        for h in cast("list[object]", mh_raw)
                        if isinstance(h, dict)
                        for hd in [cast("dict[str, object]", h)]
                    ]
                    if isinstance(mh_raw := d.get("metric_hits"), list)
                    else []
                ),
                sources=(  # 读档;存量档缺失→空;名称为提问时点快照
                    [
                        {
                            "citation_key": str(sd.get("citation_key") or ""),
                            "source_ref": str(sd.get("source_ref") or ""),
                            "doc_name": str(sd.get("doc_name") or ""),
                        }
                        for si in cast("list[object]", src_raw)
                        if isinstance(si, dict)
                        for sd in [cast("dict[str, object]", si)]
                    ]
                    if isinstance(src_raw := d.get("sources"), list)
                    else []
                ),
            )
        question = row.get("question")
        summary = row.get("summary")
        sql_text = row.get("sql_text")
        rating = row.get("rating") if role == "ASSISTANT" else None
        corrected_sql = row.get("corrected_sql") if role == "ASSISTANT" else None
        fb_comment = row.get("feedback_comment") if role == "ASSISTANT" else None
        att_raw = row.get("attachments")
        attachments = (
            [
                dict(cast("dict[str, object]", a))
                for a in cast("list[object]", att_raw)
                if isinstance(a, dict)
            ]
            if isinstance(att_raw, list)
            else []
        )
        return SessionMessage(
            id=str(row["id"]),
            role=role,
            attachments=attachments,
            question=None if question is None else str(question),
            summary=None if summary is None else str(summary),
            sql=None if sql_text is None else str(sql_text),
            status=str(row["status"]),
            error=error,
            error_detail=error_detail,
            result=result,
            rating=None if rating is None else str(rating),
            corrected_sql=None if corrected_sql is None else str(corrected_sql),
            feedback_comment=None if fb_comment is None else str(fb_comment),
        )
