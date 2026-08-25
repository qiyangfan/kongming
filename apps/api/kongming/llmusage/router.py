"""llmusage 路由(021 multi-model-routing):用量上报(内部)+聚合查询(管理面)。"""

import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.router import require_perm
from kongming.db import get_session
from kongming.llmusage.repository import SqlLlmUsageRepo

log = logging.getLogger(__name__)

internal_router = APIRouter(prefix="/internal", tags=["internal"])
router = APIRouter(prefix="/system/llm-usage", tags=["llm-usage"])

MAX_RECORDS_PER_CALL = 64  # 单请求记录上限(防离谱请求体)


class UsageRecordIn(BaseModel):
    trace_id: str | None = Field(default=None, max_length=64)
    tenant_key: str | None = Field(default=None, max_length=64)
    model: str = Field(min_length=1, max_length=64)
    task: str = Field(min_length=1, max_length=24)
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)


class UsageReportIn(BaseModel):
    records: list[UsageRecordIn] = Field(min_length=1, max_length=MAX_RECORDS_PER_CALL)


class UsageReportOut(BaseModel):
    accepted: int


@internal_router.post("/llm-usage", response_model=UsageReportOut)
async def report_usage(
    req: UsageReportIn, session: Annotated[AsyncSession, Depends(get_session)]
) -> UsageReportOut:
    """agent 用量上报:tenant_key 反解 tenant_id(失败 NULL,聚合归 unknown)。"""
    repo = SqlLlmUsageRepo(session)
    keys = {r.tenant_key for r in req.records if r.tenant_key}
    key_to_id = await repo.resolve_tenants({k for k in keys if k})
    accepted = await repo.insert_batch(
        [
            {
                "trace_id": r.trace_id,
                "tenant_id": key_to_id.get(r.tenant_key or ""),
                "model": r.model,
                "task": r.task,
                "prompt_tokens": r.prompt_tokens,
                "completion_tokens": r.completion_tokens,
                "total_tokens": r.total_tokens,
            }
            for r in req.records
        ]
    )
    return UsageReportOut(accepted=accepted)


class UsageSummaryItem(BaseModel):
    tenant_key: str
    model: str
    task: str
    calls: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class UsageSummaryOut(BaseModel):
    items: list[UsageSummaryItem]


@router.get("/summary", response_model=UsageSummaryOut)
async def usage_summary(
    _: Annotated[None, Depends(require_perm("system:llm-usage:view"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    start: Annotated[datetime | None, Query()] = None,
    end: Annotated[datetime | None, Query()] = None,
) -> UsageSummaryOut:
    rows = await SqlLlmUsageRepo(session).summary(start=start, end=end)
    return UsageSummaryOut(
        items=[
            UsageSummaryItem(
                tenant_key=str(r["tenant_key"]),
                model=str(r["model"]),
                task=str(r["task"]),
                calls=int(r["calls"]),
                prompt_tokens=int(r["prompt_tokens"]),
                completion_tokens=int(r["completion_tokens"]),
                total_tokens=int(r["total_tokens"]),
            )
            for r in rows
        ]
    )
