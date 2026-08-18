"""chat 路由(薄适配层):POST /chat/query——用户面问答端点。

首个受权限点保护的端点(chat:execute,rbac spec 权限点校验机制首发);
租户上下文取 X-Tenant-Id 头(multitenant spec),缺省取用户默认租户。
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.router import current_user, require_perm
from kongming.chat.agent_client import AgentClient
from kongming.chat.service import ChatOutcome, ChatService, SessionNotFound, TenantForbidden
from kongming.config import get_settings
from kongming.db import get_session

router = APIRouter(prefix="/chat", tags=["chat"])


def get_agent_client() -> AgentClient:
    return AgentClient(get_settings())


def get_chat_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    agent: Annotated[AgentClient, Depends(get_agent_client)],
) -> ChatService:
    return ChatService(session, agent)


class ChatQueryIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    session_id: uuid.UUID | None = None
    persist: bool = True  # 显式 false=临时对话不落库(chat spec)
    # 条数与单条双限:防超大 history 撑爆 LLM prompt(token 成本攻击面)
    history: list[Annotated[str, Field(max_length=2000)]] = Field(
        default_factory=list, max_length=10
    )
    datasource_key: str = Field(default="default", max_length=64)


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
    return _out(outcome)
