"""chat 路由(薄适配层):POST /chat/query + 会话列表/消息历史/删除(006)。

首个受权限点保护的端点(chat:execute,rbac spec 权限点校验机制首发);
租户上下文取 X-Tenant-Id 头(multitenant spec),缺省取用户默认租户。
"""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.router import current_user, require_perm
from kongming.chat.agent_client import AgentClient
from kongming.chat.service import (
    ChatOutcome,
    ChatService,
    InvalidDatasource,
    MessageResult,
    SessionMessage,
    SessionNotFound,
    TenantForbidden,
)
from kongming.config import get_settings
from kongming.datasource.repository import SqlDatasourceRepo
from kongming.db import get_session
from kongming.rag.cache import ContextCache
from kongming.rag.repository import RagRepository
from kongming.rag.service import RagService

router = APIRouter(prefix="/chat", tags=["chat"])


def get_agent_client() -> AgentClient:
    return AgentClient(get_settings())


def get_rag_service(session: AsyncSession) -> RagService:
    from kongming.rag.agent_callback import AgentRagClient
    from kongming.redis_client import get_redis

    client = AgentRagClient(get_redis())
    return RagService(
        RagRepository(session),
        embedder=client,
        describer=client,
        cache=ContextCache(get_redis()),
    )


def get_chat_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    agent: Annotated[AgentClient, Depends(get_agent_client)],
) -> ChatService:
    # history_writer=rag 回写端口(009;None 安全——注入失败不影响问答主链路)
    return ChatService(
        session, agent, SqlDatasourceRepo(session), get_rag_service(session)
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
) -> list[SessionItemOut]:
    try:
        rows = await svc.list_sessions(
            user_id=str(user["sub"]), tenant_key=x_tenant_id, limit=limit
        )
    except TenantForbidden:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None
    return [
        SessionItemOut(
            id=s.id, title=s.title, updated_at=s.updated_at  # type: ignore[arg-type]
        )
        for s in rows
    ]


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
