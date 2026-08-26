"""llmusage 路由(021 multi-model-routing):用量上报(内部)+聚合查询(管理面)。
038 agent-observability:聚合强化(失败/延迟)+按天趋势+明细分页+trace 时间线
(时间线=router 组合根并行调 llmusage/toolcall/audit 三 repo 只读查询,
chat 组合根先例;观测查询与采集通道隔离)。"""

import logging
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.audit.repository import AuditRepository
from kongming.auth.router import require_perm
from kongming.db import get_session
from kongming.llmusage.repository import SqlLlmUsageRepo
from kongming.toolcall.repository import SqlToolCallRepo

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
    # 038:缺省兼容旧 agent 上报(滚动升级窗口)
    latency_ms: int | None = Field(default=None, ge=0)
    status: Literal["SUCCESS", "ERROR"] = "SUCCESS"


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
                "latency_ms": r.latency_ms,
                "status": r.status,
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
    calls_error: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    latency_ms_avg: int | None = None
    latency_ms_max: int | None = None


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
                calls_error=int(r["calls_error"]),
                prompt_tokens=int(r["prompt_tokens"]),
                completion_tokens=int(r["completion_tokens"]),
                total_tokens=int(r["total_tokens"]),
                latency_ms_avg=(
                    int(r["latency_ms_avg"]) if r["latency_ms_avg"] is not None else None
                ),
                latency_ms_max=(
                    int(r["latency_ms_max"]) if r["latency_ms_max"] is not None else None
                ),
            )
            for r in rows
        ]
    )


class UsageTrendPoint(BaseModel):
    date: str
    calls: int
    calls_error: int
    total_tokens: int


class UsageTrendOut(BaseModel):
    points: list[UsageTrendPoint]


@router.get("/trend", response_model=UsageTrendOut)
async def usage_trend(
    _: Annotated[None, Depends(require_perm("system:llm-usage:view"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    days: Annotated[int, Query(ge=1, le=90)] = 30,
) -> UsageTrendOut:
    """按天趋势(Asia/Shanghai 日历日,含今天;逐天补零)。"""
    rows = await SqlLlmUsageRepo(session).trend(days=days)
    return UsageTrendOut(
        points=[
            UsageTrendPoint(
                date=str(r["date"]),
                calls=int(r["calls"]),
                calls_error=int(r["calls_error"]),
                total_tokens=int(r["total_tokens"]),
            )
            for r in rows
        ]
    )


class UsageRow(BaseModel):
    id: str
    trace_id: str | None = None
    tenant_key: str
    model: str
    task: str
    status: str
    bot_id: str | None = None
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    latency_ms: int | None = None
    created_at: datetime


class UsagePageOut(BaseModel):
    total: int
    rows: list[UsageRow]


@router.get("/page", response_model=UsagePageOut)
async def usage_page(
    _: Annotated[None, Depends(require_perm("system:llm-usage:view"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=200)] = 20,
    model: Annotated[str | None, Query(max_length=64)] = None,
    task: Annotated[str | None, Query(max_length=24)] = None,
    status: Literal["SUCCESS", "ERROR"] | None = None,
    trace_id: Annotated[str | None, Query(max_length=64)] = None,
    # 038③:租户筛选(uuid 直滤);R1-F1:UUID 类型闸防非法值 500
    tenant_id: Annotated[uuid.UUID | None, Query()] = None,
) -> UsagePageOut:
    total, rows = await SqlLlmUsageRepo(session).page(
        limit=size,
        offset=(page - 1) * size,
        model=model,
        task=task,
        status=status,
        trace_id=trace_id,
        tenant_id=str(tenant_id) if tenant_id is not None else None,
    )
    return UsagePageOut(
        total=total,
        rows=[
            UsageRow(
                id=str(r["id"]),
                trace_id=r["trace_id"],
                tenant_key=str(r["tenant_key"]),
                model=str(r["model"]),
                task=str(r["task"]),
                status=str(r["status"]),
                bot_id=(str(r["bot_id"]) if r["bot_id"] is not None else None),
                prompt_tokens=int(r["prompt_tokens"]),
                completion_tokens=int(r["completion_tokens"]),
                total_tokens=int(r["total_tokens"]),
                latency_ms=(int(r["latency_ms"]) if r["latency_ms"] is not None else None),
                created_at=r["created_at"],
            )
            for r in rows
        ],
    )


class TimelineEvent(BaseModel):
    kind: Literal["LLM", "TOOL", "SQL"]
    name: str
    status: str
    tokens: int | None = None
    latency_ms: int | None = None
    ts: datetime


class UsageTimelineOut(BaseModel):
    trace_id: str
    events: list[TimelineEvent]


@router.get("/timeline", response_model=UsageTimelineOut)
async def usage_timeline(
    _: Annotated[None, Depends(require_perm("system:llm-usage:view"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    trace_id: Annotated[str, Query(min_length=1, max_length=64)],
) -> UsageTimelineOut:
    """trace 时间线:LLM 调用+工具执行+SQL 执行三路事件按时间升序拼装;
    trace_id 不存在返回空事件流(与审计查询惯例一致)。"""
    events: list[dict[str, Any]] = []
    for r in await SqlLlmUsageRepo(session).list_by_trace(trace_id=trace_id):
        events.append(
            {
                "kind": "LLM",
                "name": f"{r['model']}/{r['task']}",
                "status": str(r["status"]),
                "tokens": int(r["total_tokens"]),
                "latency_ms": (
                    int(r["latency_ms"]) if r["latency_ms"] is not None else None
                ),
                "ts": r["created_at"],
            }
        )
    for r in await SqlToolCallRepo(session).list_by_trace(trace_id):
        events.append(
            {
                "kind": "TOOL",
                "name": str(r["tool_name"]),
                "status": str(r["status"]),
                "tokens": None,
                "latency_ms": (
                    int(r["duration_ms"]) if r["duration_ms"] is not None else None
                ),
                "ts": r["created_at"],
            }
        )
    for r in await AuditRepository(session).list_sql_by_trace(trace_id):
        events.append(
            {
                "kind": "SQL",
                # R1-B5:name=数据源(比 verdict 重复信息更有价值;status 承载 verdict)
                "name": str(r["datasource_key"]),
                "status": str(r["verdict"]),
                "tokens": None,
                "latency_ms": (
                    int(r["duration_ms"]) if r["duration_ms"] is not None else None
                ),
                "ts": r["created_at"],
            }
        )
    # R1-B4:同刻事件按 kind 次序(LLM<SQL<TOOL 字母序)保证输出完全确定
    # (跨表 id 不可比,跨表因果序本不存在——design「(created_at,id)」指
    # 组内 tiebreak,组间由本键补确定性)
    events.sort(key=lambda e: (e["ts"], e["kind"]))
    return UsageTimelineOut(
        trace_id=trace_id,
        events=[TimelineEvent(**e) for e in events],
    )
