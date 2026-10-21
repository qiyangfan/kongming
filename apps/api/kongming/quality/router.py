"""quality 路由(薄适配层):轮次质量汇总/明细分页(只读管理端点)。

权限点 system:chat-quality:view(种子顺延;SUPER_ADMIN 覆盖自动生效);
租户收敛在 service(data_scope=ALL 显式过滤,其余按请求租户强收敛)。
"""

import datetime as dt
import uuid
from typing import Annotated, cast

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.router import current_user, require_perm
from kongming.db import get_session
from kongming.quality.repository import SqlQualityRepo
from kongming.quality.service import QualityService

router = APIRouter(prefix="/system/chat-quality", tags=["chat-quality"])


def get_quality_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> QualityService:
    return QualityService(SqlQualityRepo(session))


class QualityOverviewOut(BaseModel):
    total: int
    success: int
    no_data: int
    blocked: int
    failed: int
    # 占比(0..1 四位小数;分母=窗口内轮次)
    no_data_rate: float
    blocked_rate: float
    failed_rate: float
    selfheal_rate: float
    down_rate: float
    # 平均 token:按 trace_id 分组 SUM 的轮次均值(口径;0 值不稀释)
    avg_tokens: float


class QualityTrendPoint(BaseModel):
    date: str
    rounds: int
    success: int
    no_data: int
    blocked: int
    failed: int


class QualitySliceRow(BaseModel):
    datasource_key: str
    rounds: int
    success: int
    no_data: int
    blocked: int
    failed: int


class QualityPromptSliceRow(BaseModel):
    prompt_version: str
    rounds: int
    success: int
    no_data: int
    blocked: int
    failed: int


class QualitySummaryOut(BaseModel):
    overview: QualityOverviewOut
    trend: list[QualityTrendPoint]
    by_datasource: list[QualitySliceRow]
    by_prompt_version: list[QualityPromptSliceRow]
    days: int


class QualityRoundItemOut(BaseModel):
    id: str
    created_at: dt.datetime
    question: str | None = None
    resolved_question: str | None = None
    status: str
    retry_count: int
    rating: str | None = None
    trace_id: str | None = None
    prompt_version: str | None = None
    datasource_key: str


class QualityPageOut(BaseModel):
    total: int
    items: list[QualityRoundItemOut]


@router.get(
    "/summary",
    response_model=QualitySummaryOut,
    dependencies=[Depends(require_perm("system:chat-quality:view"))],
)
async def quality_summary(
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[QualityService, Depends(get_quality_service)],
    tenant_id: Annotated[uuid.UUID | None, Query()] = None,
    days: Annotated[int, Query(ge=1, le=90)] = 30,
    start: Annotated[dt.datetime | None, Query()] = None,
    end: Annotated[dt.datetime | None, Query()] = None,
) -> QualitySummaryOut:
    """轮次质量总览:四态占比/率值 + 按天趋势(≤90 天补零)+ 数据源切片 +
    prompt 版本切片;租户按权限收敛(observability 域口径,不做行级按用户拆分)。"""
    body = await svc.summary(
        actor_id=str(user["sub"]),
        tenant_id=None if tenant_id is None else str(tenant_id),
        start=start,
        end=end,
        days=days,
    )
    ov = cast("dict[str, object]", body["overview"])
    return QualitySummaryOut(
        overview=QualityOverviewOut(
            total=int(cast("int", ov["total"])),
            success=int(cast("int", ov["success"])),
            no_data=int(cast("int", ov["no_data"])),
            blocked=int(cast("int", ov["blocked"])),
            failed=int(cast("int", ov["failed"])),
            no_data_rate=float(cast("float", ov["no_data_rate"])),
            blocked_rate=float(cast("float", ov["blocked_rate"])),
            failed_rate=float(cast("float", ov["failed_rate"])),
            selfheal_rate=float(cast("float", ov["selfheal_rate"])),
            down_rate=float(cast("float", ov["down_rate"])),
            avg_tokens=float(cast("float", ov["avg_tokens"])),
        ),
        trend=[_trend_point(r) for r in cast("list[object]", body["trend"])],
        by_datasource=[_slice_row(r) for r in cast("list[object]", body["by_datasource"])],
        by_prompt_version=[
            _prompt_slice(r) for r in cast("list[object]", body["by_prompt_version"])
        ],
        days=int(cast("int", body["days"])),
    )


@router.get(
    "/page",
    response_model=QualityPageOut,
    dependencies=[Depends(require_perm("system:chat-quality:view"))],
)
async def quality_page(
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[QualityService, Depends(get_quality_service)],
    tenant_id: Annotated[uuid.UUID | None, Query()] = None,
    status: Annotated[str | None, Query(pattern=r"^(SUCCESS|FAILED|REJECTED|NO_DATA)$")] = None,
    datasource_key: Annotated[str | None, Query(max_length=64)] = None,
    start: Annotated[dt.datetime | None, Query()] = None,
    end: Annotated[dt.datetime | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Field(ge=1, le=100)] = 20,
) -> QualityPageOut:
    """轮次明细分页(时间倒序):状态/数据源/时间范围筛选;NO_DATA 轮次首次
    拥有可见形态(该形态无执行、无审计行,任何观测面不可见)。"""
    total, rows = await svc.page(
        actor_id=str(user["sub"]),
        tenant_id=None if tenant_id is None else str(tenant_id),
        status=status,
        datasource_key=datasource_key,
        start=start,
        end=end,
        page=page,
        size=size,
    )
    items = [_round_item(r) for r in rows]
    return QualityPageOut(total=total, items=items)


# ── 行映射(repository dict[str, Any] → 响应模型;字段逐个显式收窄)──


def _trend_point(r: object) -> QualityTrendPoint:
    d = cast("dict[str, object]", r)
    return QualityTrendPoint(
        date=str(d["date"]),
        rounds=int(cast("int", d["rounds"])),
        success=int(cast("int", d["success"])),
        no_data=int(cast("int", d["no_data"])),
        blocked=int(cast("int", d["blocked"])),
        failed=int(cast("int", d["failed"])),
    )


def _slice_row(r: object) -> QualitySliceRow:
    d = cast("dict[str, object]", r)
    return QualitySliceRow(
        datasource_key=str(d["datasource_key"]),
        rounds=int(cast("int", d["rounds"])),
        success=int(cast("int", d["success"])),
        no_data=int(cast("int", d["no_data"])),
        blocked=int(cast("int", d["blocked"])),
        failed=int(cast("int", d["failed"])),
    )


def _prompt_slice(r: object) -> QualityPromptSliceRow:
    d = cast("dict[str, object]", r)
    return QualityPromptSliceRow(
        prompt_version=str(d["prompt_version"]),
        rounds=int(cast("int", d["rounds"])),
        success=int(cast("int", d["success"])),
        no_data=int(cast("int", d["no_data"])),
        blocked=int(cast("int", d["blocked"])),
        failed=int(cast("int", d["failed"])),
    )


def _round_item(r: object) -> QualityRoundItemOut:
    d = cast("dict[str, object]", r)
    return QualityRoundItemOut(
        id=str(d["id"]),
        created_at=cast("dt.datetime", d["created_at"]),
        question=_opt_str(d.get("question")),
        resolved_question=_opt_str(d.get("resolved_question")),
        status=str(d["status"]),
        retry_count=int(cast("int", d["retry_count"])),
        rating=_opt_str(d.get("rating")),
        trace_id=_opt_str(d.get("trace_id")),
        prompt_version=_opt_str(d.get("prompt_version")),
        datasource_key=str(d["datasource_key"]),
    )


def _opt_str(v: object) -> str | None:
    return None if v is None else str(v)
