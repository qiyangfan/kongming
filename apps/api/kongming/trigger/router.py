"""trigger 路由(031,薄适配层):/system/triggers/* 管理端点 +
/webhooks/{path} 入站端点 + 调度器/目标执行组合根。

目标执行(TargetRunner):独立会话装配 029/025 组合根(015/017 短事务
先例);创建者身份(权限放大面=节点级防御以创建者逐层复校,029 立约)。
"""

import datetime as dt
import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.repository import SqlOperLogRepo
from kongming.auth.router import current_user, require_perm
from kongming.chat.repository import ChatRepository
from kongming.config import get_settings
from kongming.datasource.cipher import get_cipher
from kongming.db import get_session, get_sessionmaker
from kongming.trigger.models import CronError, next_fire
from kongming.trigger.repository import SqlTriggerRepo
from kongming.trigger.scheduler import TriggerScheduler
from kongming.trigger.service import (
    TargetNotFound,
    TriggerKeyTaken,
    TriggerNotFound,
    TriggerService,
    TriggerValidationError,
    WebhookRejected,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/system/triggers", tags=["trigger"])
webhook_router = APIRouter(tags=["webhook"])

MANAGE = "system:trigger:manage"


class _HttpxNotifier:
    """Notifier 绑定(5s 超时;SSRF 判定复用 029 http_runner 拒绝段)。"""

    async def check_url(self, url: str) -> None:
        from kongming.workflow.http_runner import assert_url_allowed

        await assert_url_allowed(url)

    async def post(self, *, url: str, body: bytes, signature: str) -> None:
        import httpx

        async with httpx.AsyncClient(timeout=httpx.Timeout(5)) as client:
            resp = await client.post(
                url,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Kongming-Signature": signature,
                },
            )
            resp.raise_for_status()


def _make_service(session: AsyncSession, actor_id: str | None = None) -> TriggerService:
    settings = get_settings()

    class _Oper:
        async def log(self, *, action: str, target: str, detail: dict[str, object]) -> None:
            await SqlOperLogRepo(session).add(actor_id, action, target, detail)

    class _TargetCheckerImpl:
        """040 目标存在性(与前端候选同源,与派发期同口径):
        workflow=创建者视角可见集合(032 归属过滤:个人创建 + 所在团队,
        与 workflow 列表端点同源同参);bot=租户级存在。"""

        async def target_exists(
            self, *, target_type: str, target_key: str, tenant_id: str, creator_id: str
        ) -> bool:
            if target_type == "workflow":
                from kongming.team.repository import SqlTeamRepo
                from kongming.workflow.repository import SqlWorkflowRepo

                # 团队口径(R1 实锤回归):前端候选=list_workflows(team_ids=
                # 角色映射键集),缺 team_ids 则他人创建的团队 workflow 在候选
                # 可选、提交必拒——checker 与候选必须同源
                roles = await SqlTeamRepo(session).role_map(user_id=creator_id)
                rows = await SqlWorkflowRepo(session).list_latest(
                    tenant_id=tenant_id,
                    user_id=creator_id,
                    team_ids=list(roles.keys()),
                )
                return any(r["wf_key"] == target_key for r in rows)
            from kongming.bot.repository import SqlBotRepo

            return (
                await SqlBotRepo(session).get_by_key(tenant_id=tenant_id, bot_key=target_key)
                is not None
            )

    return TriggerService(
        repo=SqlTriggerRepo(session),
        cipher=get_cipher(settings.datasource_encrypt_key),
        runner=_TargetRunnerImpl(),
        notifier=_HttpxNotifier(),
        oper=_Oper(),
        target_checker=_TargetCheckerImpl(),
    )


class _TargetRunnerImpl:
    """029/025 目标执行装配(独立会话;创建者身份)。"""

    async def run_target(
        self,
        *,
        target_type: str,
        target_key: str,
        tenant_id: str,
        tenant_key: str,
        creator_id: str,
        inputs: dict[str, Any],
        channel: str,
    ) -> tuple[bool, str, str]:
        async with get_sessionmaker()() as s:
            try:
                # R1-S3:创建者账号态闸(与 025 bot 分支对称,R1-L3)——禁用用户
                # 的权限绑定仍在,不经此闸其 cron 工作流持续以其权限执行;
                # 删除用户则绑定消失由六层 fail-closed(既定正确)
                from sqlalchemy import text as _status_text

                creator_status = (
                    await s.execute(
                        _status_text(
                            "SELECT status FROM public.sys_user WHERE id=CAST(:u AS uuid)"
                        ),
                        {"u": creator_id},
                    )
                ).scalar_one_or_none()
                if creator_status != "ACTIVE":
                    return False, "", f"创建者账号不可用(status={creator_status})"
                if target_type == "workflow":
                    # tenant_key 反解(触发器行只有 tenant_id)
                    from sqlalchemy import text as _text

                    from kongming.chat.router import get_agent_client
                    from kongming.workflow.http_runner import HttpNodeRunner
                    from kongming.workflow.repository import SqlWorkflowRepo
                    from kongming.workflow.router import (
                        AgentWorkflowRunner,
                        InternalSqlRunner,
                    )
                    from kongming.workflow.service import WorkflowService

                    tkey = (
                        await s.execute(
                            _text(
                                "SELECT tenant_key FROM public.sys_tenant WHERE id=CAST(:t AS uuid)"
                            ),
                            {"t": tenant_id},
                        )
                    ).scalar_one()
                    # R1 实锤对齐:派发期可见性与 checker 同口径——创建者是触发器
                    # 创建者,团队 workflow 成员(经 member_roles)可执行;缺此参
                    # 则团队目标 _assert_visible 恒 False,创建成功派发必败
                    from kongming.team.repository import SqlTeamRepo

                    member_roles = await SqlTeamRepo(s).role_map(user_id=creator_id)
                    svc = WorkflowService(
                        repo=SqlWorkflowRepo(s),
                        sql=InternalSqlRunner(s),
                        http=HttpNodeRunner(),
                        agent=AgentWorkflowRunner(s, get_agent_client()),
                    )
                    out = await svc.run(
                        tenant_id=tenant_id,
                        tenant_key=str(tkey),
                        wf_key=target_key,
                        user_id=creator_id,
                        inputs=inputs,
                        version=None,
                        member_roles=member_roles,
                        # 审查循环 B3:cron/webhook 均系统触发,
                        # MANUAL 仅留给人工端点
                        trigger_source="API",
                        trace_id=uuid.uuid4().hex,
                    )
                    # R1-S5:目标 run 关联回填(029 run_id 已随响应返回)
                    return (
                        out["status"] == "SUCCESS",
                        str(out.get("run_id") or ""),
                        str(out.get("error") or ""),
                    )
                # bot
                from kongming.bot.router import get_bot_service as _bot_service_factory
                from kongming.chat.router import get_agent_client as _gac

                bot_svc = _bot_service_factory(s, _gac())
                out = await bot_svc.run(
                    bot_key=target_key,
                    question=str(inputs.get("summary") or inputs.get("event") or "定时触发")
                    if inputs
                    else "定时触发,请执行你的职责",
                    channel=channel,
                    tenant_id_hint=tenant_id,
                )
                return bool(out.get("success")), "", str(out.get("error") or "")
            except Exception as e:  # noqa: BLE001  # 触发执行永不逃逸(run FAILED 落库)
                log.warning("trigger 目标执行异常", exc_info=True)
                return False, "", f"{type(e).__name__}: {str(e)[:300]}"


# 调度器单例(lifespan start/stop 调用)
scheduler = TriggerScheduler(service_factory=None)  # 工厂在 lifespan 注入


async def _scheduler_factory() -> tuple[TriggerService, Any]:
    session = get_sessionmaker()()
    svc = _make_service(session)

    async def _close() -> None:
        await session.close()

    return svc, _close


def start_scheduler() -> None:
    scheduler.attach_factory(_scheduler_factory)  # lifespan 注入点
    scheduler.start()


async def stop_scheduler() -> None:
    await scheduler.stop()


# ── 传输层模型 ──


class TriggerIn(BaseModel):
    tg_key: str = Field(min_length=2, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    type: str = Field(pattern="^(CRON|WEBHOOK)$")
    target_type: str = Field(pattern="^(workflow|bot)$")
    target_key: str = Field(min_length=1, max_length=64)
    cron_expr: str | None = Field(default=None, max_length=64)
    min_interval_sec: int = Field(default=60, ge=0, le=86400)
    adapter: str = Field(default="custom", pattern="^(custom|github)$")
    notify_url: str = Field(default="", max_length=512)
    secret: str = Field(default="", max_length=256)


class TriggerUpdateIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    target_type: str = Field(pattern="^(workflow|bot)$")
    target_key: str = Field(min_length=1, max_length=64)
    cron_expr: str | None = Field(default=None, max_length=64)
    min_interval_sec: int = Field(default=60, ge=0, le=86400)
    adapter: str = Field(default="custom", pattern="^(custom|github)$")
    notify_url: str = Field(default="", max_length=512)
    # R1-T4:密钥轮换(空=保持现值;更新 webhook 密钥后旧签名即失效)
    secret: str = Field(default="", max_length=256)


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


class TriggerOut(BaseModel):
    """031 触发器条目(审查循环 D-F2:契约化)。"""

    id: str
    tg_key: str
    name: str
    type: str
    target_type: str
    target_key: str
    cron_expr: str | None = None
    min_interval_sec: int
    webhook_path: str | None = None
    adapter: str
    notify_url: str = ""
    enabled: bool
    has_secret: bool
    last_fired_at: str | None = None
    next_fire_at: str | None = None


class TriggerRunOut(BaseModel):
    id: str
    status: str
    error: str | None = None
    duration_ms: int
    target_run_ref: str | None = None
    created_at: str | None = None


class TriggerRunsPageOut(BaseModel):
    total: int
    items: list[TriggerRunOut]


def _row_out(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "tg_key": row["tg_key"],
        "name": row["name"],
        "type": row["type"],
        "target_type": row["target_type"],
        "target_key": row["target_key"],
        "cron_expr": row.get("cron_expr"),
        "min_interval_sec": row["min_interval_sec"],
        "webhook_path": (str(row["webhook_path"]) if row.get("webhook_path") else None),
        "adapter": row["adapter"],
        "notify_url": row.get("notify_url") or "",
        "enabled": bool(row["enabled"]),
        "has_secret": bool(row.get("secret_cipher")),
        "last_fired_at": (row["last_fired_at"].isoformat() if row.get("last_fired_at") else None),
        "next_fire_at": (row["next_fire_at"].isoformat() if row.get("next_fire_at") else None),
    }


# ── 管理端点 ──


class CronPreviewOut(BaseModel):
    """040·11 cron 预览(与创建/调度同源的解析计算,前端防抖消费)。"""

    next: list[str]


@router.get("/cron-preview", response_model=CronPreviewOut)
async def cron_preview(
    expr: Annotated[str, Query(max_length=64)],
    user: Annotated[dict[str, object], Depends(current_user)],
    _: Annotated[None, Depends(require_perm(MANAGE))],
) -> CronPreviewOut:
    """接下来 3 次触发时刻(单次看不出日历跳跃类缺陷,036 教训);
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
    svc = _make_service(session, str(user["sub"]))
    try:
        out = await svc.create(
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
    svc = _make_service(session)
    return [_row_out(r) for r in await svc.list_all(tenant_id=tenant_id)]


@router.put("/{trigger_id}", dependencies=[Depends(require_perm(MANAGE))])
async def update_trigger(
    trigger_id: str,
    body: TriggerUpdateIn,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> dict[str, bool]:
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    svc = _make_service(session, str(user["sub"]))
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
    svc = _make_service(session, str(user["sub"]))
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
    svc = _make_service(session, str(user["sub"]))
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
    svc = _make_service(session, str(user["sub"]))
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
    svc = _make_service(session)
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

    # R1-T6:Content-Length 预检(超限不读 body 进内存)
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
    # R1-S6:GitHub 实际签名头 X-Hub-Signature-256: sha256=<hex>,
    # 与 X-Signature 二取一(前缀剥除后同规校验)
    effective_sig = x_signature or ""
    if not effective_sig and x_hub_signature_256:
        effective_sig = x_hub_signature_256.removeprefix("sha256=")
    async with get_sessionmaker()() as s:
        svc = _make_service(s)
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
        svc = _make_service(s)
        try:
            await svc.fire_prepared(row=row, inputs=inputs)
        except Exception:
            log.warning("webhook 后台执行异常", exc_info=True)
