"""chat 用例层:问答编排(认证授权已在 router 依赖层完成)。

编排顺序(chat spec「问答查询端点」):
租户解析(白名单)→ 会话(新建/归属校验)→ history 补全 → trace_id 生成 →
回调 agent → 消息落库(USER + ASSISTANT,三态映射)→ 响应组装(含图表建议)。
"""

import json
import logging
import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from kongming.chat.agent_client import AgentClient, AgentUnavailable
from kongming.chat.models import ChartSuggestion, suggest_chart
from kongming.chat.repository import ChatRepository

log = logging.getLogger(__name__)

SESSION_TITLE_LEN = 30  # 标题=首问前 30 字符(chat spec)
HISTORY_FILL_LIMIT = 5  # 后端补全最近 5 条 user 问句


class TenantForbidden(Exception):
    """未绑定目标租户(或租户不存在/禁用——同形不区分)。"""


class SessionNotFound(Exception):
    """会话不存在或不属于当前用户+租户(同形不区分,防探测)。"""


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


class ChatService:
    def __init__(self, session: AsyncSession, agent: AgentClient) -> None:
        self._s = session
        self._repo = ChatRepository(session)
        self._agent = agent

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
            await self._s.commit()

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
            if sid is not None:
                await self._repo.insert_message(
                    session_id=sid,
                    role="ASSISTANT",
                    summary="问答服务暂不可用,请稍后重试",
                    status="FAILED",
                )
                await self._repo.touch_session(sid)
                await self._s.commit()
            return ChatOutcome(
                session_id=sid, success=False, error="问答服务暂不可用,请稍后重试"
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
            # BLOCKED 分态文案(005 评审:固定文案以偏概全)
            reason = reply.error or ""
            if reason.startswith("DATA_SCOPE"):
                error = "该查询超出您的数据范围,暂不支持"
            elif reason.startswith("RESERVED_TABLE"):
                error = "该查询访问了不允许的表,已被拒绝"
            else:
                error = "该查询涉及非只读操作,已被拒绝"
        elif status == "FAILED":
            error = f"查询失败:{reply.error}" if reply.error else "查询失败,请换个问法试试"

        if sid is not None:
            result_json = json.dumps(
                {
                    "columns": reply.columns,
                    "rows": reply.rows,
                    "insights": reply.insights,
                    "stats": reply.stats,
                    "chartType": chart.type,
                },
                ensure_ascii=False,
                default=str,
            )
            await self._repo.insert_message(
                session_id=sid,
                role="ASSISTANT",
                sql_text=reply.sql or None,
                result_json=result_json,
                summary=reply.summary or None,
                status=status,
            )
            await self._repo.touch_session(sid)
            await self._s.commit()

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
        )
