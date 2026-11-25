"""quality 路由(薄适配层):轮次质量汇总/明细分页(只读管理端点)。

权限点 system:chat-quality:view(种子顺延;SUPER_ADMIN 覆盖自动生效);
租户收敛在 service(data_scope=ALL 显式过滤,其余按请求租户强收敛)。
"""

import datetime as dt
import uuid
from typing import Annotated, cast
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.deps import CurrentUser, require_perm
from kongming.auth.repository import SqlOperLogRepo
from kongming.chat.export import to_csv
from kongming.chat.scoring import LOW_SCORE_THRESHOLD
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
    # 占比(0..1 四位小数;no_data_rate 分子分母均剔除兜底轮——兜底轮不是
    # 「没找到数据」,原始 no_data 计数如实保留)
    no_data_rate: float
    # 兜底轮分类计数(spec「兜底轮分类观测」;闲聊含复语直通与语义判定两来源)
    chitchat_count: int = 0
    clarify_count: int = 0
    blocked_rate: float
    failed_rate: float
    selfheal_rate: float
    down_rate: float
    # 平均 token:按 trace_id 分组 SUM 的轮次均值(口径;0 值不稀释)
    avg_tokens: float
    # 答案评分观测(spec「轮次质量观测」):已评分轮均分(None=零评分)、
    # 低分轮计数与阈值(阈值回显供前端展示口径)
    avg_score: float | None = None
    low_score_count: int = 0
    low_score_threshold: int = LOW_SCORE_THRESHOLD


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
    # 会话标识(同会话轮次串联的锚点;spec「同会话轮次串联」)
    session_id: str | None = None
    # 兜底轮分类(CHITCHAT=闲聊/CLARIFY=澄清;null=非兜底轮)
    fallback_type: str | None = None
    # 答案评分(spec:未评分轮为 null,呈现「未评分」而非 0 分)
    score: int | None = None
    grounding: int | None = None
    coverage: int | None = None


class QualityPageOut(BaseModel):
    total: int
    items: list[QualityRoundItemOut]


@router.get(
    "/summary",
    response_model=QualitySummaryOut,
    dependencies=[Depends(require_perm("system:chat-quality:view"))],
)
async def quality_summary(
    user: CurrentUser,
    svc: Annotated[QualityService, Depends(get_quality_service)],
    tenant_id: Annotated[uuid.UUID | None, Query()] = None,
    days: Annotated[int, Query(ge=1, le=90)] = 30,
    start: Annotated[dt.datetime | None, Query()] = None,
    end: Annotated[dt.datetime | None, Query()] = None,
) -> QualitySummaryOut:
    """轮次质量总览:四态占比/率值 + 按天趋势(≤90 天补零)+ 数据源切片 +
    prompt 版本切片;租户按权限收敛(observability 域口径,不做行级按用户拆分)。"""
    body = await svc.summary(
        actor_id=user.user_id,
        tenant_id=None if tenant_id is None else str(tenant_id),
        start=start,
        end=end,
        days=days,
        low_score_threshold=LOW_SCORE_THRESHOLD,
    )
    ov = cast("dict[str, object]", body["overview"])
    avg_score = ov.get("avg_score")
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
            chitchat_count=int(cast("int", ov.get("chitchat_count") or 0)),
            clarify_count=int(cast("int", ov.get("clarify_count") or 0)),
            avg_tokens=float(cast("float", ov["avg_tokens"])),
            avg_score=(float(cast("float", avg_score)) if avg_score is not None else None),
            low_score_count=int(cast("int", ov.get("low_score_count") or 0)),
            low_score_threshold=int(
                cast("int", ov.get("low_score_threshold") or LOW_SCORE_THRESHOLD)
            ),
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
    user: CurrentUser,
    svc: Annotated[QualityService, Depends(get_quality_service)],
    tenant_id: Annotated[uuid.UUID | None, Query()] = None,
    status: Annotated[str | None, Query(pattern=r"^(SUCCESS|FAILED|REJECTED|NO_DATA)$")] = None,
    datasource_key: Annotated[str | None, Query(max_length=64)] = None,
    fallback: Annotated[str | None, Query(pattern=r"^(CHITCHAT|CLARIFY|none)$")] = None,
    start: Annotated[dt.datetime | None, Query()] = None,
    end: Annotated[dt.datetime | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Field(ge=1, le=100)] = 20,
) -> QualityPageOut:
    """轮次明细分页(时间倒序):状态/数据源/兜底分类/时间范围筛选。"""
    total, rows = await svc.page(
        actor_id=user.user_id,
        tenant_id=None if tenant_id is None else str(tenant_id),
        status=status,
        datasource_key=datasource_key,
        fallback=fallback,
        start=start,
        end=end,
        page=page,
        size=size,
    )
    items = [_round_item(r) for r in rows]
    return QualityPageOut(total=total, items=items)


class QualityScoreDetailOut(BaseModel):
    """单轮评分详情(spec「轮次质量观测」审计分解;404=未评分或越权)。"""

    message_id: str
    score: int
    grounding: int
    coverage: int
    decomposition: dict[str, object]
    score_model: str
    scored_at: dt.datetime


@router.get(
    "/score/{message_id}",
    response_model=QualityScoreDetailOut,
    dependencies=[Depends(require_perm("system:chat-quality:view"))],
)
async def quality_score_detail(
    message_id: uuid.UUID,
    user: CurrentUser,
    svc: Annotated[QualityService, Depends(get_quality_service)],
    tenant_id: Annotated[uuid.UUID | None, Query()] = None,
) -> QualityScoreDetailOut:
    """评分详情:维度分与完整主张分解(主张/重要度/支撑分级/覆盖标记);
    与汇总/明细同一权限点与租户收敛口径,不可见或未评分统一 404
    (不区分存在性,防探测)。"""
    detail = await svc.score_detail(
        actor_id=user.user_id,
        tenant_id=None if tenant_id is None else str(tenant_id),
        message_id=str(message_id),
    )
    if detail is None:
        raise HTTPException(status_code=404, detail="NOT_FOUND")
    decomposition = detail.get("decomposition")
    return QualityScoreDetailOut(
        message_id=str(detail["message_id"]),
        score=int(cast("int", detail["score"])),
        grounding=int(cast("int", detail["grounding"])),
        coverage=int(cast("int", detail["coverage"])),
        decomposition=(
            cast("dict[str, object]", decomposition) if isinstance(decomposition, dict) else {}
        ),
        score_model=str(detail.get("score_model") or ""),
        scored_at=cast("dt.datetime", detail["scored_at"]),
    )


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
        session_id=_opt_str(d.get("session_id")),
        fallback_type=_opt_str(d.get("fallback_type")),
        score=_opt_int(d.get("score")),
        grounding=_opt_int(d.get("grounding")),
        coverage=_opt_int(d.get("coverage")),
    )


def _opt_int(v: object) -> int | None:
    return None if v is None else int(cast("int", v))


def _opt_str(v: object) -> str | None:
    return None if v is None else str(v)


# ── 观测深化三端点(spec「同会话轮次串联」「节点阶段轨迹持久化」「轮次质量导出」)──


class QualityContextItemOut(BaseModel):
    """同会话轮次轻量行(诊断标记按已合并字段携带)。"""

    id: str
    question: str | None = None
    status: str
    fallback_type: str | None = None
    score: int | None = None
    created_at: dt.datetime


class QualityContextOut(BaseModel):
    session_id: str
    items: list[QualityContextItemOut]  # 时间升序,锚点轮必含


@router.get(
    "/context",
    response_model=QualityContextOut,
    dependencies=[Depends(require_perm("system:chat-quality:view"))],
)
async def quality_context(
    message_id: Annotated[uuid.UUID, Query()],
    user: CurrentUser,
    svc: Annotated[QualityService, Depends(get_quality_service)],
    tenant_id: Annotated[uuid.UUID | None, Query()] = None,
) -> QualityContextOut:
    """同会话轮次串联:以明细行任一轮次为锚,返回锚点及此前最近 50 轮的
    轻量列表(时间升序);越权锚点(不可见租户/不存在)统一 404 防探测。"""
    ctx = await svc.session_context(
        actor_id=user.user_id,
        tenant_id=None if tenant_id is None else str(tenant_id),
        message_id=str(message_id),
    )
    if ctx is None:
        raise HTTPException(status_code=404, detail="NOT_FOUND")
    items = cast("list[object]", ctx["items"])
    return QualityContextOut(
        session_id=str(ctx["session_id"]),
        items=[
            QualityContextItemOut(
                id=str(cast("dict[str, object]", it).get("id")),
                question=_opt_str(cast("dict[str, object]", it).get("question")),
                status=str(cast("dict[str, object]", it).get("status")),
                fallback_type=_opt_str(cast("dict[str, object]", it).get("fallback_type")),
                score=_opt_int(cast("dict[str, object]", it).get("score")),
                created_at=cast("dt.datetime", cast("dict[str, object]", it).get("created_at")),
            )
            for it in items
        ],
    )


class QualityStageTraceOut(BaseModel):
    """节点阶段轨迹(有序数组;null=同步轮/无帧流,如实为空)。"""

    message_id: str
    stage_trace: list[dict[str, object]] | None = None


@router.get(
    "/stage-trace/{message_id}",
    response_model=QualityStageTraceOut,
    dependencies=[Depends(require_perm("system:chat-quality:view"))],
)
async def quality_stage_trace(
    message_id: uuid.UUID,
    user: CurrentUser,
    svc: Annotated[QualityService, Depends(get_quality_service)],
    tenant_id: Annotated[uuid.UUID | None, Query()] = None,
) -> QualityStageTraceOut:
    """单轮节点阶段轨迹;租户不可见统一 404(不区分存在性)。"""
    detail = await svc.stage_trace(
        actor_id=user.user_id,
        tenant_id=None if tenant_id is None else str(tenant_id),
        message_id=str(message_id),
    )
    if detail is None:
        raise HTTPException(status_code=404, detail="NOT_FOUND")
    trace = detail.get("stage_trace")
    return QualityStageTraceOut(
        message_id=str(message_id),
        stage_trace=cast("list[dict[str, object]]", trace) if isinstance(trace, list) else None,
    )


_EXPORT_COLUMNS = [
    "时间",
    "问题",
    "改写问题",
    "状态",
    "兜底分类",
    "重试",
    "用户反馈",
    "AI评分",
    "依据分",
    "覆盖分",
    "trace_id",
    "会话标识",
    "数据源",
    "prompt版本",
]


def _export_row(d: dict[str, object]) -> list[object]:
    """单轮导出行按列顺序取值(缺失值统一转空串/零)。"""
    return [
        _opt_str(d.get("created_at")),
        _opt_str(d.get("question")),
        _opt_str(d.get("resolved_question")),
        _opt_str(d.get("status")),
        _opt_str(d.get("fallback_type")),
        _opt_int(d.get("retry_count")),
        _opt_str(d.get("rating")),
        _opt_int(d.get("score")),
        _opt_int(d.get("grounding")),
        _opt_int(d.get("coverage")),
        _opt_str(d.get("trace_id")),
        _opt_str(d.get("session_id")),
        _opt_str(d.get("datasource_key")),
        _opt_str(d.get("prompt_version")),
    ]


def _export_rows_to_keyed(rows: list[dict[str, object]], *, truncated: bool) -> list[list[object]]:
    """逐轮取值并追加截断标记行。"""
    keyed: list[list[object]] = [_export_row(r) for r in rows]
    if truncated:
        keyed.append(
            [
                "……",
                "已达导出行数上限,仅导出最近 5000 行(筛选命中更多)",
                *([""] * (len(_EXPORT_COLUMNS) - 2)),
            ]
        )
    return keyed


@router.get(
    "/export",
    dependencies=[Depends(require_perm("system:chat-quality:view"))],
)
async def quality_export(
    user: CurrentUser,
    svc: Annotated[QualityService, Depends(get_quality_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[uuid.UUID | None, Query()] = None,
    status: Annotated[str | None, Query(pattern=r"^(SUCCESS|FAILED|REJECTED|NO_DATA)$")] = None,
    datasource_key: Annotated[str | None, Query(max_length=64)] = None,
    fallback: Annotated[str | None, Query(pattern=r"^(CHITCHAT|CLARIFY|none)$")] = None,
    start: Annotated[dt.datetime | None, Query()] = None,
    end: Annotated[dt.datetime | None, Query()] = None,
) -> StreamingResponse:
    """轮次质量导出(page 同筛选同收敛;CSV 转义纪律与问答结果导出同源;
    行数上限截断并附截断标记行;导出落操作日志)。"""
    rows, truncated = await svc.export_rows(
        actor_id=user.user_id,
        tenant_id=None if tenant_id is None else str(tenant_id),
        status=status,
        datasource_key=datasource_key,
        fallback=fallback,
        start=start,
        end=end,
    )
    keyed = _export_rows_to_keyed(rows, truncated=truncated)
    csv_bytes, _ = to_csv(_EXPORT_COLUMNS, keyed, max_rows=len(keyed))
    await SqlOperLogRepo(session).add(
        None,
        "chat-quality.export",
        None,
        {
            "status": status,
            "datasource_key": datasource_key,
            "fallback": fallback,
            "rows": len(keyed),
            "truncated": truncated,
        },
    )
    # add 仅 INSERT 不提交(chat 导出同款纪律):请求会话收尾会回滚未提交事务
    await session.commit()
    filename = quote("chat-quality.csv")
    return StreamingResponse(
        iter([csv_bytes]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )
