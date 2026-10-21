"""audit 路由(薄适配层):SQL 审计/操作日志查询(行级可见性在 service)
+ 归档手动触发/重导入。

权限点 system:audit:view(种子排序 9,仅 SUPER_ADMIN);详情 403 同形
(不区分「不存在」与「无权」,audit spec 单条详情防越权)。
"""

import re
import uuid
from datetime import datetime
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.audit.archive import run_archive, run_restore
from kongming.audit.repository import AuditRepository
from kongming.audit.service import AuditNotFound, AuditService
from kongming.auth.router import current_user, require_perm
from kongming.config import get_settings
from kongming.db import get_session

router = APIRouter(prefix="/audit", tags=["audit"])

_WINDOW_DIR_RE = re.compile(r"^\d{8}$")  # 窗口目录名=导出日,纯数字防路径穿越


def get_audit_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AuditService:
    return AuditService(AuditRepository(session))


class SqlAuditItemOut(BaseModel):
    id: str
    trace_id: str
    user_id: str | None
    user_nickname: str | None
    # 昵称缺失时前端回退登录用户名,禁渲染 id 片段
    user_username: str | None = None
    tenant_id: str
    tenant_key: str
    datasource_key: str
    question: str | None
    sql_text: str
    verdict: str
    block_reason: str | None
    row_count: int | None
    duration_ms: int | None
    created_at: datetime


class SqlAuditPageOut(BaseModel):
    total: int
    items: list[SqlAuditItemOut]


def _sql_item(r: dict[str, object]) -> SqlAuditItemOut:
    return SqlAuditItemOut(
        id=str(r["id"]),
        trace_id=str(r["trace_id"]),
        user_id=None if r["user_id"] is None else str(r["user_id"]),
        user_nickname=None if r["user_nickname"] is None else str(r["user_nickname"]),
        user_username=(None if r["user_username"] is None else str(r["user_username"])),
        tenant_id=str(r["tenant_id"]),
        tenant_key=str(r["tenant_key"]),
        datasource_key=str(r["datasource_key"]),
        question=None if r["question"] is None else str(r["question"]),
        sql_text=str(r["sql_text"]),
        verdict=str(r["verdict"]),
        block_reason=None if r["block_reason"] is None else str(r["block_reason"]),
        row_count=None if r["row_count"] is None else int(str(r["row_count"])),
        duration_ms=None if r["duration_ms"] is None else int(str(r["duration_ms"])),
        created_at=r["created_at"],  # type: ignore[arg-type]
    )


@router.get(
    "/sql/page",
    response_model=SqlAuditPageOut,
    dependencies=[Depends(require_perm("system:audit:view"))],
)
async def sql_page(
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[AuditService, Depends(get_audit_service)],
    page_num: Annotated[int, Query(ge=1, alias="page")] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
    trace_id: Annotated[str | None, Query(max_length=64)] = None,
    user_id: Annotated[uuid.UUID | None, Query()] = None,
    tenant_id: Annotated[uuid.UUID | None, Query()] = None,
    verdict: Annotated[Literal["PASS", "BLOCKED", "ERROR"] | None, Query()] = None,
    start_time: Annotated[datetime | None, Query()] = None,
    end_time: Annotated[datetime | None, Query()] = None,
) -> SqlAuditPageOut:
    total, rows = await svc.sql_page(
        actor_id=str(user["sub"]),
        trace_id=trace_id,
        user_id=None if user_id is None else str(user_id),
        tenant_id=None if tenant_id is None else str(tenant_id),
        verdict=verdict,
        start_time=start_time,
        end_time=end_time,
        page=page_num,
        size=size,
    )
    return SqlAuditPageOut(total=total, items=[_sql_item(r) for r in rows])


@router.get(
    "/sql/{audit_id}",
    response_model=SqlAuditItemOut,
    dependencies=[Depends(require_perm("system:audit:view"))],
)
async def sql_detail(
    audit_id: uuid.UUID,
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[AuditService, Depends(get_audit_service)],
) -> SqlAuditItemOut:
    try:
        row = await svc.sql_detail(actor_id=str(user["sub"]), audit_id=str(audit_id))
    except AuditNotFound:
        raise HTTPException(status_code=403, detail="AUDIT_FORBIDDEN") from None
    return _sql_item(row)


class OperLogItemOut(BaseModel):
    id: str
    user_id: str | None
    user_nickname: str | None
    # 昵称缺失时前端回退登录用户名,禁渲染 id 片段
    user_username: str | None = None
    action: str
    target: str | None
    detail: dict[str, object] | None
    ip: str | None
    created_at: datetime


class OperLogPageOut(BaseModel):
    total: int
    items: list[OperLogItemOut]


@router.get(
    "/oper/page",
    response_model=OperLogPageOut,
    dependencies=[Depends(require_perm("system:audit:view"))],
)
async def oper_page(
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[AuditService, Depends(get_audit_service)],
    page_num: Annotated[int, Query(ge=1, alias="page")] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
    action: Annotated[str | None, Query(max_length=64)] = None,
    user_id: Annotated[uuid.UUID | None, Query()] = None,
    start_time: Annotated[datetime | None, Query()] = None,
    end_time: Annotated[datetime | None, Query()] = None,
) -> OperLogPageOut:
    total, rows = await svc.oper_page(
        actor_id=str(user["sub"]),
        action=action,
        user_id=None if user_id is None else str(user_id),
        start_time=start_time,
        end_time=end_time,
        page=page_num,
        size=size,
    )
    return OperLogPageOut(
        total=total,
        items=[
            OperLogItemOut(
                id=str(r["id"]),
                user_id=None if r["user_id"] is None else str(r["user_id"]),
                user_nickname=(None if r["user_nickname"] is None else str(r["user_nickname"])),
                user_username=(None if r["user_username"] is None else str(r["user_username"])),
                action=str(r["action"]),
                target=None if r["target"] is None else str(r["target"]),
                detail=cast("dict[str, object]", r["detail"])
                if isinstance(r["detail"], dict)
                else None,
                ip=None if r["ip"] is None else str(r["ip"]),
                created_at=r["created_at"],  # type: ignore[arg-type]
            )
            for r in rows
        ],
    )


# ============ 归档(手动触发同函数;调度=lifespan 每日薄壳) ============


class ArchiveRunOut(BaseModel):
    exported: int
    deleted: int
    files: list[str]
    window_dir: str
    skipped_export: bool


class ArchiveRestoreIn(BaseModel):
    window_dir: str = Field(min_length=8, max_length=8, pattern=r"^\d{8}$")


class ArchiveRestoreOut(BaseModel):
    restored: int
    skipped: int


@router.post(
    "/archive/run",
    response_model=ArchiveRunOut,
    dependencies=[Depends(require_perm("system:audit:view"))],
)
async def archive_run(
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ArchiveRunOut:
    """手动触发归档(超管;与 lifespan 每日调度同一函数,幂等可重跑)。"""
    settings = get_settings()
    result = await run_archive(
        session,
        days=settings.audit_archive_days,
        archive_dir=settings.audit_archive_dir,
    )
    return ArchiveRunOut(
        exported=result.exported,
        deleted=result.deleted,
        files=result.files,
        window_dir=result.window_dir,
        skipped_export=result.skipped_export,
    )


@router.post(
    "/archive/restore",
    response_model=ArchiveRestoreOut,
    dependencies=[Depends(require_perm("system:audit:view"))],
)
async def archive_restore(
    req: ArchiveRestoreIn,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ArchiveRestoreOut:
    """重导入归档窗口(manifest SHA-256 校验;id 幂等)。
    window_dir 传输层 8 位数字(pattern)+服务端再校验,防路径穿越。"""
    if not _WINDOW_DIR_RE.fullmatch(req.window_dir):
        raise HTTPException(status_code=400, detail="WINDOW_DIR_INVALID") from None
    try:
        result = await run_restore(
            session, archive_dir=get_settings().audit_archive_dir, window_dir=req.window_dir
        )
    except (OSError, ValueError, KeyError) as e:
        raise HTTPException(status_code=400, detail=f"ARCHIVE_RESTORE_FAILED: {e}") from None
    return ArchiveRestoreOut(restored=result.restored, skipped=result.skipped)
