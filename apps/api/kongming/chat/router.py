"""chat 路由(薄适配层):POST /chat/query + 会话列表/消息历史/删除(006)
+ 重命名/搜索/导出/执行计划(010)。

首个受权限点保护的端点(chat:execute,rbac spec 权限点校验机制首发);
租户上下文取 X-Tenant-Id 头(multitenant spec),缺省取用户默认租户。
"""

import uuid
from datetime import datetime
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.repository import SqlOperLogRepo
from kongming.auth.router import current_user, require_perm
from kongming.chat.agent_client import AgentClient
from kongming.chat.ports import ExplainOutcome
from kongming.chat.repository import ChatRepository
from kongming.chat.service import (
    ChatOutcome,
    ChatService,
    ExplainNoSql,
    InvalidDatasource,
    InvalidTitle,
    MessageNotFound,
    MessageResult,
    SessionMessage,
    SessionNotFound,
    TenantForbidden,
)
from kongming.chat.title_tasks import rewrite_session_title
from kongming.config import get_settings
from kongming.datasource.repository import SqlDatasourceRepo
from kongming.db import get_session
from kongming.internal import service as internal_service
from kongming.rag.cache import ContextCache
from kongming.rag.repository import RagRepository
from kongming.rag.service import RagService

router = APIRouter(prefix="/chat", tags=["chat"])


def get_agent_client() -> AgentClient:
    return AgentClient(get_settings())


def get_rag_service(session: AsyncSession) -> RagService:
    from kongming.rag.agent_callback import get_agent_rag_client
    from kongming.redis_client import get_redis

    client = get_agent_rag_client(get_redis())  # 013:进程内单例,连接池跨请求复用
    return RagService(
        RagRepository(session),
        embedder=client,
        describer=client,
        cache=ContextCache(get_redis()),
    )


class _InternalExplainRunner:
    """组合根适配器(010):chat 域 ExplainRunner 端口 ← internal.service.explain。

    chat service/models 受 import-linter 零框架契约,不可间接 import internal
    (携 sqlalchemy);内部异常在此翻译为 chat 域异常,不越域泄漏。
    """

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def explain(
        self,
        *,
        trace_id: str,
        tenant_key: str,
        datasource_key: str,
        sql_text: str,
        user_id: str,
    ) -> ExplainOutcome:
        try:
            r = await internal_service.explain(
                self._s,
                trace_id=trace_id,
                tenant_key=tenant_key,
                datasource_key=datasource_key,
                sql_text=sql_text,
                user_id=user_id,
                question=None,
            )
        except internal_service.InvalidDatasource as e:
            # 竞态兜底(chat 已预检):禁用/删除发生在预检与执行之间
            raise InvalidDatasource(str(e)) from e
        except (internal_service.TenantNotFound, internal_service.TenantBindingForbidden) as e:
            raise TenantForbidden(str(e)) from e
        return ExplainOutcome(verdict=r.verdict, reason=r.reason, plan=r.plan)


def get_chat_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    agent: Annotated[AgentClient, Depends(get_agent_client)],
) -> ChatService:
    # history_writer=rag 回写端口(009;None 安全——注入失败不影响问答主链路)
    # explainer=执行计划端口(010,组合根绑定 internal.service.explain)
    # 013 构造反转:repo 在组合根装配,service 仅持 ChatRepo 端口(零框架契约)
    # 015 导出审计:oper_logger 绑定 auth 域 SqlOperLogRepo(007 组合模式)
    return ChatService(
        ChatRepository(session),
        agent,
        SqlDatasourceRepo(session),
        get_rag_service(session),
        _InternalExplainRunner(session),
        SqlOperLogRepo(session),
    )


class ChatQueryIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    session_id: uuid.UUID | None = None
    persist: bool = True  # 显式 false=临时对话不落库(chat spec)
    # 条数与单条双限:防超大 history 撑爆 LLM prompt(token 成本攻击面)
    history: list[Annotated[str, Field(max_length=2000)]] = Field(
        default_factory=list, max_length=10
    )
    datasource_key: str = Field(default="default", pattern=r"^[a-z][a-z0-9_]{1,63}$")


class ChartOut(BaseModel):
    type: str
    x: str | None = None
    y: str | None = None


class ChatQueryOut(BaseModel):
    session_id: str | None
    success: bool
    verdict: str | None
    summary: str
    insights: list[dict[str, str]]
    stats: dict[str, object]
    columns: list[str]
    rows: list[list[object]]
    sql: str
    chart: ChartOut
    retry_count: int
    error: str | None
    # 014 messageId 打通(纯增):assistant 落库消息 id(反馈提交引用)+执行数据源快照
    message_id: str | None = None
    datasource_key: str = "default"


def _out(o: ChatOutcome) -> ChatQueryOut:
    return ChatQueryOut(
        session_id=o.session_id,
        success=o.success,
        verdict=o.verdict,
        summary=o.summary,
        insights=o.insights,
        stats=o.stats,
        columns=o.columns,
        rows=o.rows,
        sql=o.sql,
        chart=ChartOut(type=o.chart.type, x=o.chart.x, y=o.chart.y),
        retry_count=o.retry_count,
        error=o.error,
        message_id=o.message_id,
        datasource_key=o.datasource_key,
    )


@router.post(
    "/query",
    response_model=ChatQueryOut,
    dependencies=[Depends(require_perm("chat:execute"))],
)
async def chat_query(
    req: ChatQueryIn,
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[ChatService, Depends(get_chat_service)],
    background: BackgroundTasks,
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> ChatQueryOut:
    try:
        outcome = await svc.query(
            user_id=str(user["sub"]),
            tenant_key=x_tenant_id,
            question=req.question,
            session_id=None if req.session_id is None else str(req.session_id),
            persist=req.persist,
            history=req.history[-5:],  # 防御性截断(agent 侧另有限制)
            datasource_key=req.datasource_key,
        )
    except TenantForbidden:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None
    except SessionNotFound:
        raise HTTPException(status_code=404, detail="SESSION_NOT_FOUND") from None
    except InvalidDatasource:
        # 不存在/禁用/越属同形(防 key 探测);与 internal 面同一 detail 码
        raise HTTPException(status_code=400, detail="DATASOURCE_INVALID") from None
    # 010 标题两阶段生成:首个 SUCCESS 轮后异步改写(守卫/条件写双闸在任务内);
    # persist=false 临时对话无会话可改写;REJECTED/FAILED 轮不触发(违规/失败问句不作素材)
    if outcome.success and req.persist and outcome.session_id is not None:
        background.add_task(rewrite_session_title, outcome.session_id, svc.agent_for_tasks)
    return _out(outcome)


# ============ 会话列表/消息历史/删除(006,web-nav 支撑端点) ============


class SessionItemOut(BaseModel):
    id: str
    title: str
    updated_at: datetime


class ChartInfoOut(BaseModel):
    type: str
    x: str | None = None
    y: str | None = None


class MessageResultOut(BaseModel):
    columns: list[str] = Field(default_factory=list)
    rows: list[list[object]] = Field(default_factory=list)
    insights: list[dict[str, str]] = Field(default_factory=list)
    stats: dict[str, object] = Field(default_factory=dict)
    chart: ChartInfoOut
    error: str | None = None


class SessionMessageOut(BaseModel):
    id: str
    role: str
    question: str | None = None
    summary: str | None = None
    sql: str | None = None
    status: str
    error: str | None = None
    result: MessageResultOut | None = None
    # 014 messageId 打通(纯增):assistant 消息反馈状态回显(user 消息恒 None)
    rating: str | None = None
    corrected_sql: str | None = None
    feedback_comment: str | None = None


def _msg_out(m: SessionMessage) -> SessionMessageOut:
    result: MessageResultOut | None = None
    if m.result is not None:
        r: MessageResult = m.result
        result = MessageResultOut(
            columns=r.columns,
            rows=r.rows,
            insights=r.insights,
            stats=r.stats,
            chart=ChartInfoOut(type=r.chart.type, x=r.chart.x, y=r.chart.y),
            error=r.error,
        )
    return SessionMessageOut(
        id=m.id,
        role=m.role,
        question=m.question,
        summary=m.summary,
        sql=m.sql,
        status=m.status,
        error=m.error,
        result=result,
        rating=m.rating,
        corrected_sql=m.corrected_sql,
        feedback_comment=m.feedback_comment,
    )


@router.get(
    "/sessions",
    response_model=list[SessionItemOut],
    dependencies=[Depends(require_perm("chat:execute"))],
)
async def list_sessions(
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[ChatService, Depends(get_chat_service)],
    x_tenant_id: Annotated[str | None, Header()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> list[SessionItemOut]:
    try:
        rows = await svc.list_sessions(
            user_id=str(user["sub"]),
            tenant_key=x_tenant_id,
            limit=limit,
            keyword=q or "",
        )
    except TenantForbidden:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None
    return [
        SessionItemOut(
            id=s.id, title=s.title, updated_at=s.updated_at  # type: ignore[arg-type]
        )
        for s in rows
    ]


class RenameIn(BaseModel):
    """重命名(010):去空白后非空与不超列上限(200)由领域层判定(013 补齐——
    spec 立约以 strip 后值为准,带空白 201 字 strip 后 ≤200 不再 422 误拒);
    max_length=2000 仅防离谱请求体(与 question 同宽),非业务上限。"""

    title: str = Field(min_length=1, max_length=2000)


@router.patch(
    "/sessions/{session_id}",
    response_model=SessionItemOut,
    dependencies=[Depends(require_perm("chat:execute"))],
)
async def rename_session(
    session_id: uuid.UUID,
    req: RenameIn,
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[ChatService, Depends(get_chat_service)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> SessionItemOut:
    try:
        s = await svc.rename_session(
            user_id=str(user["sub"]),
            tenant_key=x_tenant_id,
            session_id=str(session_id),
            title=req.title,
        )
    except TenantForbidden:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None
    except SessionNotFound:
        # 不存在/越权/跨租户同形 404,不泄露会话存在性(chat spec)
        raise HTTPException(status_code=404, detail="SESSION_NOT_FOUND") from None
    except InvalidTitle:
        raise HTTPException(status_code=400, detail="TITLE_INVALID") from None
    return SessionItemOut(
        id=s.id, title=s.title, updated_at=s.updated_at  # type: ignore[arg-type]
    )


@router.get(
    "/sessions/{session_id}/messages",
    response_model=list[SessionMessageOut],
    dependencies=[Depends(require_perm("chat:execute"))],
)
async def session_messages(
    session_id: uuid.UUID,
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[ChatService, Depends(get_chat_service)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> list[SessionMessageOut]:
    try:
        msgs = await svc.session_messages(
            user_id=str(user["sub"]),
            tenant_key=x_tenant_id,
            session_id=str(session_id),
        )
    except TenantForbidden:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None
    except SessionNotFound:
        # 不存在/越权/跨租户同形 404,不泄露会话存在性(chat spec 006 delta)
        raise HTTPException(status_code=404, detail="SESSION_NOT_FOUND") from None
    return [_msg_out(m) for m in msgs]


@router.delete(
    "/sessions/{session_id}",
    status_code=204,
    dependencies=[Depends(require_perm("chat:execute"))],
)
async def delete_session(
    session_id: uuid.UUID,
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[ChatService, Depends(get_chat_service)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> None:
    try:
        await svc.delete_session(
            user_id=str(user["sub"]), tenant_key=x_tenant_id, session_id=str(session_id)
        )
    except TenantForbidden:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None
    except SessionNotFound:
        raise HTTPException(status_code=404, detail="SESSION_NOT_FOUND") from None


# ============ 结果导出(010,chat spec「结果导出 CSV/Excel」) ============


class ExportIn(BaseModel):
    """所见即所导(010 design D3):前端传已展示的 columns+rows,不重跑 SQL。

    rows 传输层上限=R1 评审修复:截断发生在 JSON 解析之后,挡不住请求体本身
    的内存放大;max_length 在解析期即拒(422),service 层截断保留为双闸。
    """

    format: Literal["csv", "xlsx"]
    columns: list[str] = Field(min_length=1, max_length=200)
    rows: list[list[object]] = Field(default_factory=list, max_length=10000)


@router.post(
    "/export",
    dependencies=[Depends(require_perm("chat:execute"))],
)
async def export_result(
    req: ExportIn,
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[ChatService, Depends(get_chat_service)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> Response:
    try:
        content, media_type, ext = await svc.export_result(
            user_id=str(user["sub"]),
            tenant_key=x_tenant_id,
            fmt=req.format,
            columns=req.columns,
            rows=req.rows,
            max_rows=get_settings().export_max_rows,
        )
    except TenantForbidden:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None
    # 「查询结果-<时间戳>.<ext>」(chat spec);RFC 5987 filename* 携带中文名
    filename = quote(f"查询结果-{datetime.now():%Y%m%d-%H%M%S}.{ext}")
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


# ============ 执行计划(010,chat spec「EXPLAIN 执行计划」) ============


class ExplainIn(BaseModel):
    """message_id 引用形态(010 design D2):客户端不可提交裸 SQL。"""

    message_id: uuid.UUID


class ExplainOut(BaseModel):
    success: bool
    plan: object | None = None  # EXPLAIN (FORMAT JSON) 解析产物(顶层数组)
    error: str | None = None


@router.post(
    "/explain",
    response_model=ExplainOut,
    dependencies=[Depends(require_perm("chat:execute"))],
)
async def explain_message(
    req: ExplainIn,
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[ChatService, Depends(get_chat_service)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> ExplainOut:
    try:
        view = await svc.explain_message(
            user_id=str(user["sub"]), tenant_key=x_tenant_id, message_id=str(req.message_id)
        )
    except TenantForbidden:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None
    except MessageNotFound:
        # 不存在/越权/跨租户同形 404,不泄露消息存在性(与会话端点同语义)
        raise HTTPException(status_code=404, detail="MESSAGE_NOT_FOUND") from None
    except ExplainNoSql:
        raise HTTPException(status_code=400, detail="EXPLAIN_NO_SQL") from None
    except InvalidDatasource:
        raise HTTPException(status_code=400, detail="DATASOURCE_INVALID") from None
    return ExplainOut(success=view.success, plan=view.plan, error=view.error)
