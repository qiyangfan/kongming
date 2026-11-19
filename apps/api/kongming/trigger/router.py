"""trigger 路由(薄适配层):/system/triggers/* 管理端点 +
/webhooks/{path} 入站端点。调度器/目标执行组合根见 dependencies/adapters。
"""

import datetime as dt
import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.router import current_user, require_perm
from kongming.chat.repository import ChatRepository
from kongming.db import get_session, get_sessionmaker
from kongming.trigger.definition import TriggerDraft
from kongming.trigger.dependencies import make_trigger_service
from kongming.trigger.models import CronError, next_fire
from kongming.trigger.schemas import (
    CronPreviewOut,
    TriggerIn,
    TriggerOut,
    TriggerRunsPageOut,
    TriggerUpdateIn,
    to_trigger_row,
)
from kongming.trigger.service import (
    TargetNotFound,
    TriggerKeyTaken,
    TriggerNotFound,
    TriggerValidationError,
    WebhookRejected,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/system/triggers", tags=["trigger"])
webhook_router = APIRouter(tags=["webhook"])

MANAGE = "system:trigger:manage"


async def _tenant_or_403(user: dict[str, object], session: AsyncSession, header: str | None) -> str:
    repo = ChatRepository(session)
    uid = str(user["sub"])
    if header is not None:
        tid = await repo.find_bound_tenant(user_id=uid, tenant_key=header)
        if tid is None:
            raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN")
        return str(tid)
    default = await repo.find_default_tenant(user_id=uid)
    if default is None:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN")
    return str(default[0])


# ── 管理端点 ──


@router.get("/cron-preview", response_model=CronPreviewOut)
async def cron_preview(
    expr: Annotated[str, Query(max_length=64)],
    user: Annotated[dict[str, object], Depends(current_user)],
    _: Annotated[None, Depends(require_perm(MANAGE))],
) -> CronPreviewOut:
    """接下来 3 次触发时刻(单次看不出日历跳跃类缺陷);
    非法表达式 400 附原文案——与创建期校验同口径,禁第二套 cron 语义。"""
    try:
        fire = dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))  # Asia/Shanghai(与服务层同钟)
        fires: list[str] = []
        for _i in range(3):
            fire = next_fire(expr, fire)
            fires.append(fire.isoformat())
    except CronError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return CronPreviewOut(next=fires)


@router.post("", status_code=201, dependencies=[Depends(require_perm(MANAGE))])
async def create_trigger(
    body: TriggerIn,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    svc = make_trigger_service(session, str(user["sub"]))
    try:
        out = await svc.create(
            TriggerDraft(
                tenant_id=tenant_id,
                tg_key=body.tg_key,
                name=body.name,
                type_=body.type,
                target_type=body.target_type,
                target_key=body.target_key,
                cron_expr=body.cron_expr,
                min_interval_sec=body.min_interval_sec,
                adapter=body.adapter,
                notify_url=body.notify_url,
                secret=body.secret,
                created_by=str(user["sub"]),
                max_retries=body.max_retries,
                task_template=body.task_template,
                imap_host=body.imap_host,
                imap_port=body.imap_port,
                imap_tls=body.imap_tls,
                imap_user=body.imap_user,
                imap_password=body.imap_password,
                imap_folder=body.imap_folder,
                imap_poll_interval_sec=body.imap_poll_interval_sec,
            )
        )
        await session.commit()
    except TriggerValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    except TargetNotFound:
        raise HTTPException(status_code=400, detail="TARGET_NOT_FOUND") from None
    except TriggerKeyTaken:
        raise HTTPException(status_code=409, detail="TRIGGER_KEY_TAKEN") from None
    return out


@router.get("", response_model=list[TriggerOut], dependencies=[Depends(require_perm(MANAGE))])
async def list_triggers(
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> list[dict[str, Any]]:
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    svc = make_trigger_service(session)
    return [to_trigger_row(r) for r in await svc.list_all(tenant_id=tenant_id)]


@router.put("/{trigger_id}", dependencies=[Depends(require_perm(MANAGE))])
async def update_trigger(
    trigger_id: str,
    body: TriggerUpdateIn,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> dict[str, bool]:
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    svc = make_trigger_service(session, str(user["sub"]))
    try:
        await svc.update(
            trigger_id=trigger_id,
            tenant_id=tenant_id,
            name=body.name,
            target_type=body.target_type,
            target_key=body.target_key,
            cron_expr=body.cron_expr,
            min_interval_sec=body.min_interval_sec,
            adapter=body.adapter,
            notify_url=body.notify_url,
            secret=body.secret,
            max_retries=body.max_retries,
            task_template=body.task_template,
            imap_host=body.imap_host,
            imap_port=body.imap_port,
            imap_tls=body.imap_tls,
            imap_user=body.imap_user,
            imap_password=body.imap_password,
            imap_folder=body.imap_folder,
            imap_poll_interval_sec=body.imap_poll_interval_sec,
        )
        await session.commit()
    except TriggerValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    except TargetNotFound:
        raise HTTPException(status_code=400, detail="TARGET_NOT_FOUND") from None
    except TriggerNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    return {"ok": True}


@router.post("/{trigger_id}/enable", dependencies=[Depends(require_perm(MANAGE))])
async def enable_trigger(
    trigger_id: str,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> dict[str, bool]:
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    svc = make_trigger_service(session, str(user["sub"]))
    try:
        await svc.set_enabled(trigger_id=trigger_id, tenant_id=tenant_id, enabled=True)
        await session.commit()
    except TriggerNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    return {"ok": True}


@router.post("/{trigger_id}/disable", dependencies=[Depends(require_perm(MANAGE))])
async def disable_trigger(
    trigger_id: str,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> dict[str, bool]:
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    svc = make_trigger_service(session, str(user["sub"]))
    try:
        await svc.set_enabled(trigger_id=trigger_id, tenant_id=tenant_id, enabled=False)
        await session.commit()
    except TriggerNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    return {"ok": True}


@router.delete("/{trigger_id}", dependencies=[Depends(require_perm(MANAGE))])
async def delete_trigger(
    trigger_id: str,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> dict[str, bool]:
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    svc = make_trigger_service(session, str(user["sub"]))
    try:
        await svc.delete(trigger_id=trigger_id, tenant_id=tenant_id)
        await session.commit()
    except TriggerNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    return {"ok": True}


@router.get(
    "/{trigger_id}/runs",
    response_model=TriggerRunsPageOut,
    dependencies=[Depends(require_perm(MANAGE))],
)
async def page_trigger_runs(
    trigger_id: str,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    svc = make_trigger_service(session)
    try:
        total, rows = await svc.page_runs(
            tenant_id=tenant_id, trigger_id=trigger_id, page=page, size=size
        )
    except TriggerNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    return {
        "total": total,
        "items": [
            {
                "id": str(r["id"]),
                "status": r["status"],
                "error": r["error"],
                "duration_ms": r["duration_ms"],
                "target_run_ref": r["target_run_ref"],
                "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
                "attempt": int(r.get("attempt") or 1),
                "retry_of_run_id": (
                    str(r["retry_of_run_id"]) if r.get("retry_of_run_id") else None
                ),
                "next_retry_at": (
                    r["next_retry_at"].isoformat() if r.get("next_retry_at") else None
                ),
                # 标准化摘要 JSON(邮件触发;原文不落库)
                "summary": str(r.get("summary") or ""),
            }
            for r in rows
        ],
    }


# ── webhook 入站(公开端点,无 JWT) ──


@webhook_router.post("/webhooks/{path}")
async def webhook_inbound(
    path: str,
    request: Request,
    x_signature: Annotated[str | None, Header()] = None,
    x_github_event: Annotated[str | None, Header()] = None,
    x_hub_signature_256: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    import asyncio

    # Content-Length 预检(超限不读 body 进内存)
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > 1 * 1024 * 1024:
        return JSONResponse(status_code=413, content={"detail": "PAYLOAD_TOO_LARGE"})
    body = await request.body()
    if len(body) > 1 * 1024 * 1024:
        return JSONResponse(status_code=413, content={"detail": "PAYLOAD_TOO_LARGE"})
    try:
        uuid.UUID(path)
    except ValueError:
        return JSONResponse(status_code=404, content={"detail": "NOT_FOUND"})
    # GitHub 实际签名头 X-Hub-Signature-256: sha256=<hex>,
    # 与 X-Signature 二取一(前缀剥除后同规校验)
    effective_sig = x_signature or ""
    if not effective_sig and x_hub_signature_256:
        effective_sig = x_hub_signature_256.removeprefix("sha256=")
    async with get_sessionmaker()() as s:
        svc = make_trigger_service(s)
        try:
            # 校验同步完成(防探测同形);执行后台异步(202 即返)
            row = await svc.find_webhook_row(path=path)
            if row is None or not row["enabled"]:
                raise WebhookRejected(path)
            svc.verify_signature(row=row, body=body, signature=effective_sig)
            inputs = svc.normalize_inputs(row=row, body=body, event_header=x_github_event or "")
        except WebhookRejected:
            return JSONResponse(status_code=401, content={"detail": "REJECTED"})
        task = asyncio.create_task(_fire_background(dict(row), inputs))
        task.add_done_callback(lambda _t: None)
    return JSONResponse(status_code=202, content={"accepted": True})


async def _fire_background(row: dict[str, Any], inputs: dict[str, Any]) -> None:
    async with get_sessionmaker()() as s:
        svc = make_trigger_service(s)
        try:
            await svc.fire_prepared(row=row, inputs=inputs)
        except Exception:
            log.warning("webhook 后台执行异常", exc_info=True)
