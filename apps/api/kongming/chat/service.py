"""chat 用例层:问答编排(认证授权已在 router 依赖层完成)。

编排顺序(chat spec「问答查询端点」):
租户解析(白名单)→ 会话(新建/归属校验)→ history 补全 → trace_id 生成 →
回调 agent → 消息落库(USER + ASSISTANT,三态映射)→ 响应组装(含图表建议)。
"""

import contextlib
import json
import logging
import uuid
from dataclasses import dataclass, field

from kongming.chat.agent_client import AgentClient, AgentUnavailable
from kongming.chat.export import to_csv, to_xlsx
from kongming.chat.models import ChartSuggestion, suggest_chart
from kongming.chat.ports import ChatRepo, ChatStoreError, ExplainRunner, OperLogger
from kongming.datasource.ports import DatasourceChecker
from kongming.rag.ports import QueryHistoryWriter

log = logging.getLogger(__name__)

SESSION_TITLE_LEN = 30  # 标题=首问前 30 字符(chat spec)
SESSION_TITLE_MAX_LEN = 200  # 标题列上限(chat spec「会话重命名」;strip 后判定)
HISTORY_FILL_LIMIT = 5  # 后端补全最近 5 条 user 问句


def blocked_friendly(reason: str | None) -> str:
    """BLOCKED 分态文案(005 评审:固定文案以偏概全);问答与执行计划共用。"""
    r = reason or ""
    if r.startswith("DATA_SCOPE"):
        return "该查询超出您的数据范围,暂不支持"
    if r.startswith("RESERVED_TABLE"):
        return "该查询访问了不允许的表,已被拒绝"
    return "该查询涉及非只读操作,已被拒绝"


class TenantForbidden(Exception):
    """未绑定目标租户(或租户不存在/禁用——同形不区分)。"""


class SessionNotFound(Exception):
    """会话不存在或不属于当前用户+租户(同形不区分,防探测)。"""


class InvalidDatasource(Exception):
    """数据源不存在/禁用/不归属当前租户(同形不区分,防 key 探测;008 入口预检)。"""


class MessageNotFound(Exception):
    """消息不存在或不属于当前用户+租户(010 执行计划;同形不区分,防探测)。"""


class ExplainNoSql(Exception):
    """消息无落库 SQL(user 问句/纯失败轮)——无计划可分析(010)。"""


class InvalidTitle(Exception):
    """标题去空白后为空或超列上限(010 重命名;参数错误,映射 400)。"""


@dataclass(frozen=True)
class ChatOutcome:
    session_id: str | None
    success: bool
    verdict: str | None = None
    summary: str = ""
    insights: list[dict[str, str]] = field(default_factory=list)
    stats: dict[str, object] = field(default_factory=dict)
    columns: list[str] = field(default_factory=list)
    rows: list[list[object]] = field(default_factory=list)
    sql: str = ""
    chart: ChartSuggestion = field(default_factory=lambda: ChartSuggestion("none"))
    retry_count: int = 0
    error: str | None = None
    # 014 messageId 打通:assistant 落库消息 id(反馈提交引用;不落库路径 None)
    # 与本次执行的数据源快照(会话快照同源口径)
    message_id: str | None = None
    datasource_key: str = "default"


@dataclass(frozen=True)
class SessionSummary:
    """会话列表项(006 侧栏)。"""

    id: str
    title: str
    updated_at: object  # datetime;类型宽放避免领域层 import datetime 之外的耦合


@dataclass(frozen=True)
class MessageResult:
    """assistant 消息结构化应答要素(回显形态,与 ChatOutcome 的结果子集对齐)。"""

    columns: list[str] = field(default_factory=list)
    rows: list[list[object]] = field(default_factory=list)
    insights: list[dict[str, str]] = field(default_factory=list)
    stats: dict[str, object] = field(default_factory=dict)
    chart: ChartSuggestion = field(default_factory=lambda: ChartSuggestion("none"))
    error: str | None = None


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
    result: MessageResult | None = None
    # 014 反馈状态回显(assistant 消息;user 消息恒 None)
    rating: str | None = None
    corrected_sql: str | None = None
    feedback_comment: str | None = None


@dataclass(frozen=True)
class ExplainView:
    """执行计划视图(010):PASS 含计划树;BLOCKED/ERROR 含友好文案。"""

    success: bool
    plan: object | None = None
    error: str | None = None


class ChatService:
    def __init__(
        self,
        repo: ChatRepo,
        agent: AgentClient,
        ds_checker: DatasourceChecker,
        history_writer: QueryHistoryWriter | None = None,  # 009:成功 SQL 回写(可选注入)
        explainer: ExplainRunner | None = None,  # 010:执行计划端口(组合根绑定)
        oper_logger: OperLogger | None = None,  # 015:导出审计(可选注入)
    ) -> None:
        self._repo = repo
        self._agent = agent
        self._ds_checker = ds_checker
        self._history_writer = history_writer
        self._explainer = explainer
        self._oper_logger = oper_logger

    @property
    def agent_for_tasks(self) -> AgentClient:
        """后台任务用 agent 句柄(010 标题改写;AgentClient 无会话状态,响应后可安全复用)。"""
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
    ) -> ChatOutcome:
        tenant_id, resolved_key = await self._resolve_tenant(user_id, tenant_key)

        # 数据源入口预检(008 design D8):非 default 校验存在+启用+归属当前租户,
        # fail-fast 同形 400——不进 LLM 链路、不建会话;连接信息不出控制面(ADR-002)
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

        trace_id = uuid.uuid4().hex
        if sid is not None:
            await self._repo.insert_message(
                session_id=sid, role="USER", question=question, status="SUCCESS"
            )
            await self._repo.commit()

        try:
            reply = await self._agent.query(
                trace_id=trace_id,
                tenant_key=resolved_key,
                question=question,
                user_id=user_id,
                history=effective_history,
                datasource_key=datasource_key,
            )
        except AgentUnavailable:
            log.warning("agent 不可达 trace_id=%s", trace_id)
            mid: str | None = None
            if sid is not None:
                # 落库容错与成功路径对称:回调期间会话可能被并发删除
                try:
                    mid = await self._repo.insert_message(
                        session_id=sid,
                        role="ASSISTANT",
                        result_json=json.dumps(
                            {"error": "问答服务暂不可用,请稍后重试"}, ensure_ascii=False
                        ),
                        summary="问答服务暂不可用,请稍后重试",
                        status="FAILED",
                    )
                    await self._persist_touch_quietly(sid)
                except ChatStoreError:
                    mid = None  # 落库失败即无消息 id(与未落库语义一致)
                    log.warning("FAILED 消息落库失败(会话可能已被删除) session=%s", sid)
                    await self._repo.rollback()
            return ChatOutcome(
                session_id=sid,
                success=False,
                error="问答服务暂不可用,请稍后重试",
                message_id=mid,
                datasource_key=datasource_key,
            )

        # 三态映射:PASS→SUCCESS;BLOCKED→REJECTED;ERROR/重试耗尽→FAILED
        if reply.success:
            status = "SUCCESS"
        elif reply.verdict == "BLOCKED":
            status = "REJECTED"
        else:
            status = "FAILED"
        chart = (
            suggest_chart(reply.columns, reply.rows) if reply.success else ChartSuggestion("none")
        )
        error: str | None = None
        if status == "REJECTED":
            error = blocked_friendly(reply.error)
        elif status == "FAILED":
            error = f"查询失败:{reply.error}" if reply.error else "查询失败,请换个问法试试"

        mid = None
        if sid is not None:
            # 006:chart 完整对象(x/y)+ error 文案随档落库,回显读档即事实源;
            # 存量 005 形态(裸 chartType)由 _to_message 重算兜底。
            # 落库段整体容错:agent 回调期间会话可能被并发删除(他端),
            # FK 违约不炸 500——结果照常返回,仅放弃该轮持久化。
            try:
                result_json = json.dumps(
                    {
                        "columns": reply.columns,
                        "rows": reply.rows,
                        "insights": reply.insights,
                        "stats": reply.stats,
                        "chart": {"type": chart.type, "x": chart.x, "y": chart.y},
                        "error": error,
                    },
                    ensure_ascii=False,
                    default=str,
                )
                mid = await self._repo.insert_message(
                    session_id=sid,
                    role="ASSISTANT",
                    sql_text=reply.sql or None,
                    result_json=result_json,
                    summary=reply.summary or None,
                    status=status,
                )
                await self._persist_touch_quietly(sid)
            except ChatStoreError:
                mid = None  # 落库失败即无消息 id(与未落库语义一致)
                log.warning("assistant 消息落库失败(会话可能已被删除) session=%s", sid)
                await self._repo.rollback()

        # 成功 SQL 回写 query_history(009):最终 SUCCESS 判定后;独立 try 不阻断;
        # persist=False 为测试通道不触发;空结果的成功查询同样回写(不依赖行数)。
        if (
            reply.success
            and persist
            and self._history_writer is not None
            and reply.sql.strip()
        ):
            try:
                await self._history_writer.writeback(
                    tenant_id=tenant_id,
                    datasource_key=datasource_key,
                    question=question,  # 原始问句(与审计口径一致)
                    sql_text=reply.sql,
                )
            except Exception:  # noqa: BLE001  # 回写永不阻断主流程(writeback 内已兜底)
                log.warning("query_history 回写异常(不阻断)", exc_info=True)

        return ChatOutcome(
            session_id=sid,
            success=reply.success,
            verdict=reply.verdict,
            summary=reply.summary,
            insights=reply.insights,
            stats=reply.stats,
            columns=reply.columns,
            rows=reply.rows,
            sql=reply.sql,
            chart=chart,
            retry_count=reply.retry_count,
            error=error,
            message_id=mid,
            datasource_key=datasource_key,
        )

    async def _persist_touch_quietly(self, session_id: str) -> None:
        """touch+commit;FK 违约(会话被并发删除)仅日志,不影响响应。"""
        try:
            await self._repo.touch_session(session_id)
            await self._repo.commit()
        except ChatStoreError:
            log.warning("会话 touch 失败(可能已被删除) session=%s", session_id)
            await self._repo.rollback()

    # ============ 会话列表/消息历史/删除(006) ============

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
        """重命名(010):标题去空白后非空且不超列上限(spec 立约,013 补齐——
        非法判定以 strip 后值为准,带空白 201 字 strip 后 ≤200 不再误拒);
        归属原子 UPDATE;成功后该标题即人工设定,
        不再被 LLM 自动改写覆盖(改写走 expected 条件更新,见 title_tasks)。"""
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

    # ============ 结果导出(010,chat spec「结果导出 CSV/Excel」) ============

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
            media_type = (
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            ext = "xlsx"
        if truncated:
            log.warning("导出行数截断: %d → %d 行", len(rows), max_rows)
        # 导出审计(015,010 挂账⑦清零):数据外发可见性;写失败不阻断导出
        # (audit spec 铁律,与 004 SqlAuditWriter 同语义);rollback 亦包保护
        # ——连接级双失败时异常不逃逸(R1-B1:裸 rollback 再抛会把导出变 500)
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

    # ============ 执行计划(010,chat spec「EXPLAIN 执行计划」) ============

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
        """行 → 回显项;result_json 读档优先,存量(005 形态)缺失 x/y/error 时
        以 suggest_chart 纯函数确定性重算补全(落库输入=原始输入,重算=原始输出)。"""
        role = str(row["role"])
        result: MessageResult | None = None
        error: str | None = None
        rj = row.get("result_json")
        if role == "ASSISTANT" and rj:
            try:
                parsed = json.loads(str(rj))
            except json.JSONDecodeError:
                parsed = None
            # 合法但非 dict 的 JSON(null/数组等)一律按空档处理,不炸回显
            d: dict[str, object] = parsed if isinstance(parsed, dict) else {}
            cols_raw = d.get("columns")
            rows_raw = d.get("rows")
            cols = [str(c) for c in cols_raw] if isinstance(cols_raw, list) else []
            # 元素级窄化:畸形 JSON 深防御(服务端自身不产生,防手改库)
            rws = (
                [list(r) for r in rows_raw if isinstance(r, (list, tuple))]
                if isinstance(rows_raw, list)
                else []
            )
            ins_raw = d.get("insights")
            chart_raw = d.get("chart")
            if isinstance(chart_raw, dict) and chart_raw.get("type"):
                chart = ChartSuggestion(
                    type=str(chart_raw["type"]),
                    x=chart_raw.get("x") if chart_raw.get("x") is None else str(chart_raw["x"]),
                    y=chart_raw.get("y") if chart_raw.get("y") is None else str(chart_raw["y"]),
                )
            else:
                # 存量兼容:005 只存 chartType(无 x/y)
                chart = suggest_chart(cols, rws)
            err_raw = d.get("error")
            error = str(err_raw) if err_raw is not None else None
            stats_raw = d.get("stats")
            result = MessageResult(
                columns=cols,
                rows=rws,
                insights=(
                    [dict(i) for i in ins_raw if isinstance(i, dict)]
                    if isinstance(ins_raw, list)
                    else []
                ),
                stats=dict(stats_raw) if isinstance(stats_raw, dict) else {},
                chart=chart,
                error=error,
            )
        question = row.get("question")
        summary = row.get("summary")
        sql_text = row.get("sql_text")
        rating = row.get("rating") if role == "ASSISTANT" else None
        corrected_sql = row.get("corrected_sql") if role == "ASSISTANT" else None
        fb_comment = row.get("feedback_comment") if role == "ASSISTANT" else None
        return SessionMessage(
            id=str(row["id"]),
            role=role,
            question=None if question is None else str(question),
            summary=None if summary is None else str(summary),
            sql=None if sql_text is None else str(sql_text),
            status=str(row["status"]),
            error=error,
            result=result,
            rating=None if rating is None else str(rating),
            corrected_sql=None if corrected_sql is None else str(corrected_sql),
            feedback_comment=None if fb_comment is None else str(fb_comment),
        )
