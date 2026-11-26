"""终态落库管线:三态映射 → 输出护栏 → 图表/标签/统计 → 落库 → 响应组装。

同步(T1)与流式(独立 StreamStore 会话)共用同一实现;落库顺序与消息 id 语义
是 SSE 契约的一部分,改动须保持逐字等价。
"""

import dataclasses
import json
import logging
from dataclasses import dataclass

from kongming.chat.agent_client import AgentReply
from kongming.chat.domain_errors import blocked_friendly
from kongming.chat.models import (
    Artifact,
    ChartCandidate,
    ChartSuggestion,
    MessageMeta,
    derive_artifacts,
    humanize_stats,
    relabel_column_mentions,
    resolve_column_labels,
    suggest_charts,
)
from kongming.chat.ports import ChatRepo, ChatStoreError, GuardrailPort, PendingToolReader
from kongming.chat.results import ChatOutcome
from kongming.rag.ports import ColumnCommentReader, QueryHistoryWriter

log = logging.getLogger(__name__)

AGENT_UNAVAILABLE_TEXT = "问答服务暂不可用,请稍后重试"
TURN_CANCELLED_TEXT = "本轮已取消"

# 落库状态 → 响应结论词(与 map_status 互逆):取消收敛采用已落库行时还原为响应口径
_SETTLED_VERDICTS = {
    "SUCCESS": "PASS",
    "REJECTED": "BLOCKED",
    "NO_DATA": "NO_DATA",
    "FAILED": "ERROR",
    "CANCELLED": "CANCELLED",
}


def map_status(reply: AgentReply) -> str:
    """四态映射:PASS→SUCCESS;BLOCKED→REJECTED;NO_DATA→NO_DATA;ERROR/重试耗尽→FAILED。"""
    if reply.success:
        return "SUCCESS"
    if reply.verdict == "BLOCKED":
        return "REJECTED"
    if reply.verdict == "NO_DATA":
        return "NO_DATA"
    return "FAILED"


@dataclass(frozen=True)
class _Guarded:
    """输出护栏后的呈现要素(summary/insights/错误原文/sql)。"""

    summary: str
    insights: list[dict[str, str]]
    sql_text: str
    error_raw: str | None


@dataclass(frozen=True)
class _Presentation:
    """终态呈现要素(图表/列标签/统计/产物;落库 result_json 与响应同源)。"""

    chart: ChartSuggestion
    chart_candidates: list[ChartCandidate]
    column_labels: list[str]
    stats: dict[str, object]
    artifacts: list[Artifact]


@dataclass(frozen=True)
class _ErrorView:
    """按终态给出的用户错误与原始技术错误。"""

    summary: str
    error: str | None
    error_detail: str | None


@dataclass(frozen=True)
class _Staged:
    """呈现前的终态要素:状态、护栏后文本、错误口径与呈现要素。"""

    status: str
    guarded: _Guarded
    view: _ErrorView
    presentation: _Presentation


@dataclass(frozen=True)
class _Committed:
    """已落库的终态:呈现要素、assistant 消息 id 与待确认调用聚合。"""

    stage: _Staged
    mid: str | None
    pending: list[dict[str, object]]


def _assign_error(status: str, guarded: _Guarded) -> _ErrorView:
    """终态 → error/error_detail 口径(REJECTED 友好文案;NO_DATA 走 summary)。"""
    error_raw = guarded.error_raw
    if status == "REJECTED":
        return _ErrorView(guarded.summary, blocked_friendly(error_raw), None)
    if status == "NO_DATA":
        # 中性形态——说明文案经 summary 传达(过护栏),不走错误包装
        return _ErrorView(
            error_raw or "当前数据源未找到回答该问题所需的数据,可切换数据源或调整问法后重试",
            None,
            None,
        )
    if status == "FAILED":
        # 用户语言进 error,原始技术错误进 error_detail(web 折叠披露)
        return _ErrorView(guarded.summary, "查询执行失败,请调整问法后重试", error_raw)
    return _ErrorView(guarded.summary, None, None)


def _relabel_guarded(guarded: _Guarded, columns: list[str], labels: list[str]) -> _Guarded:
    """把总结/洞察中的物理列名替换为呈现标签;护栏已处理的其他字段不动。"""
    if not labels:
        return guarded
    return dataclasses.replace(
        guarded,
        summary=relabel_column_mentions(guarded.summary, columns, labels),
        insights=[
            {
                "type": item["type"],
                "text": relabel_column_mentions(item["text"], columns, labels),
            }
            for item in guarded.insights
        ],
    )


async def _writeback_history(
    reply: AgentReply,
    *,
    history_writer: QueryHistoryWriter | None,
    persist: bool,
    tenant_id: str,
    datasource_key: str,
    question: str,
    user_id: str,
) -> None:
    """成功 SQL 回写 query_history(最终 SUCCESS 判定后,异常仅记日志不阻断)。"""
    # persist=False 为测试通道不触发;空结果的成功查询同样回写(不依赖行数)
    if not (reply.success and persist and history_writer is not None and reply.sql.strip()):
        return
    try:
        await history_writer.writeback(
            tenant_id=tenant_id,
            datasource_key=datasource_key,
            question=question,  # 原始问句(与审计口径一致)
            sql_text=reply.sql,
            # 伴生:样本可见集合=执行主体可见范围(缺省=公共样本)
            caller_user_id=user_id or None,
        )
    except Exception:  # 回写永不阻断主流程(writeback 内已兜底)
        log.warning("query_history 回写异常(不阻断)", exc_info=True)


def _assemble_outcome(
    reply: AgentReply,
    committed: _Committed,
    *,
    sid: str | None,
    tenant_id: str,
    datasource_key: str,
    attachments: list[dict[str, object]] | None,
) -> ChatOutcome:
    """按落库结果组装响应(字段与 result_json 同源)。"""
    guarded = committed.stage.guarded
    view = committed.stage.view
    presentation = committed.stage.presentation
    return ChatOutcome(
        session_id=sid,
        success=reply.success,
        verdict=reply.verdict,
        summary=view.summary,
        insights=guarded.insights,
        stats=presentation.stats,
        columns=reply.columns,
        rows=reply.rows,
        sql=guarded.sql_text,
        chart=presentation.chart,
        chart_candidates=presentation.chart_candidates,
        retry_count=reply.retry_count,
        error=view.error,
        error_detail=view.error_detail,
        message_id=committed.mid,
        datasource_key=datasource_key,
        pending_tool_calls=committed.pending,
        tenant_id=tenant_id,
        artifacts=[dict(a) for a in presentation.artifacts],
        attachments=[dict(a) for a in attachments or []],
        column_labels=presentation.column_labels,
        metric_hits=reply.metric_hits,
        sources=reply.sources,
    )


def _dumps_result_json(
    *,
    reply: AgentReply,
    column_labels: list[str],
    insights: list[dict[str, str]],
    stats: dict[str, object],
    chart: ChartSuggestion,
    chart_candidates: list[ChartCandidate],
    artifacts: list[Artifact],
    error: str | None,
    error_detail: str | None,
) -> str:
    """assistant 消息 result_json(chart 完整对象 + 候选随档;回显读档即事实源)。"""
    return json.dumps(
        {
            "columns": reply.columns,
            "column_labels": column_labels,
            "rows": reply.rows,
            "insights": insights,
            "stats": stats,
            "chart": {"type": chart.type, "x": chart.x, "y": chart.y},
            "chart_candidates": [  # 随档(回显读档即事实源)
                {"type": c.type, "x": c.x, "y": list(c.y), "series": c.series}
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


async def persist_unavailable(
    repo: ChatRepo,
    *,
    sid: str | None,
    trace_id: str | None = None,  # 本轮标识(流式失败行携带;取消收敛按它查重)
    datasource_key: str = "default",
    attachments: list[dict[str, object]] | None = None,  # 带附件轮与成功路径对称
    stage_trace: list[dict[str, object]] | None = None,  # 失败前已收集轨迹如实落库
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
                    # 缺档会让回显重算出 document 卡,与同步响应
                    # ChatOutcome.artifacts=[] 不同源;冻结空档保三路径一致
                    {"error": AGENT_UNAVAILABLE_TEXT, "artifacts": []},
                    ensure_ascii=False,
                ),
                summary=AGENT_UNAVAILABLE_TEXT,
                status="FAILED",
                attachments=attachments,  # 与成功路径回显对称
                meta=MessageMeta(trace_id=trace_id, stage_trace=stage_trace),
            )
            await touch_quietly(repo, sid)
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


async def persist_cancelled(
    repo: ChatRepo,
    *,
    sid: str | None,
    trace_id: str,
    user_id: str,
    datasource_key: str = "default",
    attachments: list[dict[str, object]] | None = None,
    stage_trace: list[dict[str, object]] | None = None,
    user_message_id: str | None = None,
) -> ChatOutcome:
    """已取消终态落库(裸终态行)并返回其终态:summary 承载取消说明,不含 SQL、
    结果集、图表与产物卡,携带取消前已收集的节点轨迹。同 trace 已有 assistant 行
    (取消与完成几乎同时)即采用该行、不再写——同一轮至多一条 assistant 应答;
    采用时结论词还原为响应口径(落库 REJECTED 即 BLOCKED)。查重读取失败时
    不写行,按裸终态返回(漏写一条已取消消息,优于同轮落两条应答)。
    已取消行沿用原轮用户消息的落库时刻,避免历史按落库先后排到下一问之后。"""
    cancelled = ChatOutcome(
        session_id=sid,
        success=False,
        verdict="CANCELLED",
        summary=TURN_CANCELLED_TEXT,
        datasource_key=datasource_key,
        attachments=[dict(a) for a in attachments or []],
    )
    if sid is None:
        return cancelled
    try:
        existing = await repo.assistant_message_by_trace(trace_id=trace_id, user_id=user_id)
    except Exception:
        # 查重读取失败(连接中断一类)即不写行:该会话上的插入必再失败,
        # 而绕过查重续写有同轮双行风险;裸终态由调用方照常下发
        log.warning("已取消收敛查重失败,本轮不写消息 session=%s", sid, exc_info=True)
        return cancelled
    try:
        if existing is not None:
            status = str(existing["status"])
            return ChatOutcome(
                session_id=sid,
                success=status == "SUCCESS",
                # 未知状态按失败呈现:不把没把握的终态说成已取消
                verdict=_SETTLED_VERDICTS.get(status, "ERROR"),
                summary=str(existing["summary"] or ""),
                message_id=str(existing["id"]),
                datasource_key=datasource_key,
                attachments=[dict(a) for a in attachments or []],
            )
        created_at = None
        if user_message_id is not None:
            try:
                created_at = await repo.message_created_at(message_id=user_message_id)
            except Exception:
                # 时序读取失败按落库时刻写入:位置不准,但不阻断取消收敛
                log.warning("已取消行原轮时序读取失败 session=%s", sid, exc_info=True)
        mid = await repo.insert_message(
            session_id=sid,
            role="ASSISTANT",
            # artifacts 显式空数组:summary 列存了取消说明,
            # 缺档会让回显重算出文档卡(与 FAILED 行同款处理)
            result_json=json.dumps({"artifacts": []}, ensure_ascii=False),
            summary=TURN_CANCELLED_TEXT,
            status="CANCELLED",
            attachments=attachments,
            meta=MessageMeta(trace_id=trace_id, stage_trace=stage_trace),
            created_at=created_at,
        )
        await touch_quietly(repo, sid)
    except ChatStoreError:
        log.warning("已取消消息落库失败(会话可能已被删除) session=%s", sid)
        await repo.rollback()
        return cancelled
    return dataclasses.replace(cancelled, message_id=mid)


async def touch_quietly(repo: ChatRepo, session_id: str) -> None:
    """touch+commit;FK 违约(会话被并发删除)仅日志,不影响响应。"""
    try:
        await repo.touch_session(session_id)
        await repo.commit()
    except ChatStoreError:
        log.warning("会话 touch 失败(可能已被删除) session=%s", session_id)
        await repo.rollback()


@dataclass(frozen=True)
class FinalizePorts:
    """终态管线所需可选端口(组合根注入;None=未接线)。"""

    guardrail: GuardrailPort | None = None
    pending_tools: PendingToolReader | None = None
    column_comments: ColumnCommentReader | None = None


class ReplyFinalizer:
    """把 AgentReply 落库并组装 ChatOutcome(单轮终态唯一出口)。"""

    def __init__(self, ports: FinalizePorts) -> None:
        self._ports = ports

    async def finalize(
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
        is_react: bool = False,  # ReAct 链路轮不评分(spec 评分范围)
        stage_trace: list[dict[str, object]] | None = None,  # 节点轨迹(流式轮;同步轮 None)
    ) -> ChatOutcome:
        """终态落库与响应组装(同步/流式共用唯一实现):
        三态映射 → chart 建议 → error 文案 → result_json 落库(容错)→ 回写。
        summary/insights 经输出护栏(泄漏拦截替换/凭证脱敏)后才落库返回。"""
        return _assemble_outcome(
            reply,
            await self._commit(
                reply,
                question=question,
                persist=persist,
                repo=repo,
                history_writer=history_writer,
                sid=sid,
                attachments=attachments,
                trace_id=trace_id,
                user_id=user_id,
                tenant_id=tenant_id,
                datasource_key=datasource_key,
                stage_trace=stage_trace,
            ),
            sid=sid,
            tenant_id=tenant_id,
            datasource_key=datasource_key,
            attachments=attachments,
        )

    async def _commit(
        self,
        reply: AgentReply,
        *,
        question: str,
        persist: bool,
        repo: ChatRepo,
        history_writer: QueryHistoryWriter | None,
        sid: str | None,
        attachments: list[dict[str, object]] | None,
        trace_id: str,
        user_id: str,
        tenant_id: str,
        datasource_key: str,
        stage_trace: list[dict[str, object]] | None,
    ) -> _Committed:
        """呈现准备 → 落库 → 回写 → 聚合待确认调用(顺序即契约)。"""
        stage = await self._prepare(
            reply,
            trace_id=trace_id,
            user_id=user_id,
            tenant_id=tenant_id,
            datasource_key=datasource_key,
            question=question,
        )
        mid = await self._persist(
            reply,
            stage=stage,
            sid=sid,
            repo=repo,
            traced=trace_id,
            attachments=attachments,
            stage_trace=stage_trace,
        )
        await _writeback_history(
            reply,
            history_writer=history_writer,
            persist=persist,
            tenant_id=tenant_id,
            datasource_key=datasource_key,
            question=question,
            user_id=user_id,
        )
        return _Committed(stage=stage, mid=mid, pending=await self._pending_calls(trace_id))

    async def _prepare(
        self,
        reply: AgentReply,
        *,
        trace_id: str,
        user_id: str,
        tenant_id: str,
        datasource_key: str,
        question: str,
    ) -> _Staged:
        """终态呈现前的处理:三态映射 → 输出护栏 → 标签归并 → 错误口径。"""
        status = map_status(reply)
        guarded = await self._guard_output(
            reply, trace_id=trace_id, user_id=user_id, tenant_id=tenant_id
        )
        # 形状→有序候选(纯函数,含 sql 供 pie 可加闸);首候选展平为存量
        # 「默认单图」,chart 字段语义不变
        chart_candidates = (
            suggest_charts(reply.columns, reply.rows, sql=guarded.sql_text) if reply.success else []
        )
        chart = chart_candidates[0].as_default() if chart_candidates else ChartSuggestion("none")
        # 呈现层列标签(仅成功有结果集时;物理名/显示名双轨,SQL 不动)
        column_labels = (
            await self._resolve_labels(
                tenant_id=tenant_id,
                datasource_key=datasource_key,
                columns=reply.columns,
                sql=guarded.sql_text,
                question=question,
                resolved_question=reply.resolved_question or "",
            )
            if reply.success
            else []
        )
        # 总结/洞察可能与 SQL 同源列名:统一换成同一套呈现标签后再落库
        guarded = _relabel_guarded(guarded, reply.columns, column_labels)
        view = _assign_error(status, guarded)
        # 统计键用户可读化(标签单源;落库即事实源,回显读档幂等再应用)
        stats = humanize_stats(reply.stats, reply.columns, column_labels)
        # 产物派生(三路径同源);如实说明不派生产物卡
        # (derive_artifacts 对非空 summary 会派生 document 卡,NO_DATA 跳过)
        artifacts = (
            []
            if status == "NO_DATA"
            else derive_artifacts(
                sql=guarded.sql_text,
                chart=chart,
                summary=view.summary,
                datasource_key=datasource_key,
            )
        )
        return _Staged(
            status=status,
            guarded=guarded,
            view=view,
            presentation=_Presentation(
                chart=chart,
                chart_candidates=chart_candidates,
                column_labels=column_labels,
                stats=stats,
                artifacts=artifacts,
            ),
        )

    async def _guard_output(
        self, reply: AgentReply, *, trace_id: str, user_id: str, tenant_id: str
    ) -> _Guarded:
        """输出护栏全路径过滤:summary/insights、FAILED 错误原文与 sql 文本;未接线原样返回。"""
        # 输出护栏(扩面):summary/insights + FAILED 错误原文(驱动回显
        # 可能含凭证形态)+ sql 文本(注释可藏提示词片段)全路径过滤;REJECTED 的
        # error 为 blocked_friendly 系统文案,过滤无害。
        summary = reply.summary
        insights = reply.insights
        sql_text = reply.sql
        error_raw = reply.error
        payload = _Guarded(summary, insights, sql_text, error_raw)
        if self._ports.guardrail is None:
            return payload
        tail = [error_raw or "", sql_text]
        filtered = await self._ports.guardrail.filter_output(
            trace_id=trace_id,
            user_id=user_id,
            tenant_id=tenant_id,
            texts=[summary, *[i["text"] for i in insights], *tail],
        )
        n_ins = len(insights)
        return _Guarded(
            summary=filtered[0],
            insights=[
                {"type": i["type"], "text": t}
                for i, t in zip(insights, filtered[1 : 1 + n_ins], strict=True)
            ],
            sql_text=filtered[2 + n_ins],
            error_raw=filtered[1 + n_ins] or None,
        )

    async def _resolve_labels(
        self,
        *,
        tenant_id: str,
        datasource_key: str,
        columns: list[str],
        sql: str,
        question: str,
        resolved_question: str,
    ) -> list[str]:
        """按 SQL 投影与用户原句映射业务标签;查询失败或无词时回退原列名。"""
        reader = self._ports.column_comments
        if not columns or reader is None:
            return []
        try:
            table_comments = await reader.column_comments(
                tenant_id=tenant_id, datasource_key=datasource_key
            )
        except Exception:  # 标签增强永不阻断主流程
            log.warning("列注释查询异常(标签回退原始列名)", exc_info=True)
            return []
        return resolve_column_labels(
            columns,
            table_comments,
            sql,
            question=question,
            resolved_question=resolved_question,
        )

    async def _persist(
        self,
        reply: AgentReply,
        *,
        stage: _Staged,
        sid: str | None,
        repo: ChatRepo,
        traced: str,
        attachments: list[dict[str, object]] | None,
        stage_trace: list[dict[str, object]] | None,
    ) -> str | None:
        """落库 assistant 消息(result_json 由呈现要素组装);返回 message_id。"""
        # 落库先于回写:message_id 是再执行/反馈的引用锚点,顺序不可换
        return await self._persist_assistant(
            reply=reply,
            sql_text=stage.guarded.sql_text,
            status=stage.status,
            sid=sid,
            repo=repo,
            traced=traced,
            attachments=attachments,
            stage_trace=stage_trace,
            result_json=_dumps_result_json(
                reply=reply,
                column_labels=stage.presentation.column_labels,
                insights=stage.guarded.insights,
                stats=stage.presentation.stats,
                chart=stage.presentation.chart,
                chart_candidates=stage.presentation.chart_candidates,
                artifacts=stage.presentation.artifacts,
                error=stage.view.error,
                error_detail=stage.view.error_detail,
            ),
            summary=stage.view.summary,
        )

    async def _persist_assistant(
        self,
        *,
        reply: AgentReply,
        sql_text: str,
        status: str,
        sid: str | None,
        repo: ChatRepo,
        traced: str,
        attachments: list[dict[str, object]] | None,
        stage_trace: list[dict[str, object]] | None,
        result_json: str,
        summary: str,
    ) -> str | None:
        """ASSISTANT 消息落库(整体容错;失败即无消息 id);返回 message_id。"""
        if sid is None:
            return None
        # 落库段整体容错:agent 回调期间会话可能被并发删除(他端),
        # FK 违约不炸 500——结果照常返回,仅放弃该轮持久化。
        try:
            mid = await repo.insert_message(
                session_id=sid,
                role="ASSISTANT",
                sql_text=sql_text or None,
                result_json=result_json,
                summary=summary or None,
                status=status,
                attachments=attachments,
                meta=MessageMeta(
                    trace_id=traced or None,
                    retry_count=reply.retry_count,
                    resolved_question=reply.resolved_question,
                    prompt_version=reply.prompt_version,
                    fallback_type=reply.fallback_type,
                    stage_trace=stage_trace,
                ),
            )
            await touch_quietly(repo, sid)
        except ChatStoreError:
            log.warning("assistant 消息落库失败(会话可能已被删除) session=%s", sid)
            await repo.rollback()
            return None  # 落库失败即无消息 id(与未落库语义一致)
        else:
            return mid

    async def _pending_calls(self, trace_id: str) -> list[dict[str, object]]:
        """聚合本 trace 待确认写工具调用(前端确认卡);读取失败不阻断响应。"""
        reader = self._ports.pending_tools
        if not trace_id or reader is None:
            return []
        try:
            return await reader.list_pending(trace_id)
        except Exception:  # 附加信息聚合永不阻断主流程
            log.warning("pending_tool_calls 聚合异常 trace_id=%s", trace_id, exc_info=True)
            return []
