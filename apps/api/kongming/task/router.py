"""task 路由(090):公开端点(创建/列表/详情/暂停/恢复/取消/审批)
+ 内部编排回调(plan/step-start/step-result;X-Internal-Token 闸)。

权限点:task:create / task:read / task:manage;归属收口=本人本租户,404 同形。
执行派发=创建/真实恢复/审批决策时触发一次 agent 编排(拉取式,步间连续,
挂起即停;同态幂等操作不二次派发)。
"""

import asyncio
import logging
import uuid as _uuid
from datetime import datetime
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.router import current_user, require_perm
from kongming.chat.agent_client import AgentClient
from kongming.config import get_settings
from kongming.db import get_session, get_sessionmaker, uuid7
from kongming.task import models as m
from kongming.task.repository import SqlTaskRepo
from kongming.task.service import (
    TaskConflict,
    TaskInvalidTransition,
    TaskNotFound,
    TaskService,
)
from kongming.trace import ensure_trace_id

log = logging.getLogger(__name__)

router = APIRouter(prefix="/tasks", tags=["task"])
internal_router = APIRouter(prefix="/internal/task", tags=["internal"])


def _get_service(session: AsyncSession) -> TaskService:
    return TaskService(SqlTaskRepo(session))


async def _tenant_id(session: AsyncSession, user_id: str, tenant_key: str | None) -> str:
    from kongming.tenant import resolve_tenant

    resolved = await resolve_tenant(session, user_id=user_id, tenant_key=tenant_key)
    if resolved is None:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None
    return resolved[0]


# ── 请求/响应模型 ──


class CreateIn(BaseModel):
    goal: str = Field(min_length=1, max_length=4000)
    datasource_key: str = Field(default="default", max_length=64)
    session_id: str | None = None
    budget: dict[str, int] | None = None


class TaskCreateOut(BaseModel):
    task_id: str
    status: str


class TaskItem(BaseModel):
    id: str
    goal: str
    status: str
    datasource_key: str
    fail_reason: str | None
    created_at: str
    updated_at: str


class TaskPageOut(BaseModel):
    items: list[TaskItem]
    total: int
    page: int
    size: int


class StepOut(BaseModel):
    id: str
    ord: int
    goal: str
    status: str
    require_confirm: bool
    evidence: dict[str, object] | None
    fail_reason: str | None
    checkpoint: dict[str, object] | None


class TaskDetailOut(BaseModel):
    task: TaskItem
    steps: list[StepOut]
    events: list[dict[str, object]]
    budget_config: dict[str, object]
    budget_used: dict[str, object]
    trace_root: str | None = None


class TaskMutateOut(BaseModel):
    task_id: str
    status: str


class ApproveIn(BaseModel):
    approve: bool = True


# ── 公开端点 ──


@router.post(
    "",
    status_code=202,
    response_model=TaskCreateOut,
    dependencies=[Depends(require_perm("task:create"))],
)
async def create_task(
    body: CreateIn,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> TaskCreateOut:
    uid = str(user["sub"])
    tid = await _tenant_id(session, uid, x_tenant_id)
    trace_root = ensure_trace_id()
    task_id = str(uuid7())
    svc = _get_service(session)
    await svc.create(
        task_id=task_id,
        tenant_id=tid,
        user_id=uid,
        goal=body.goal,
        datasource_key=body.datasource_key,
        session_id=body.session_id,
        trace_root=trace_root,
        budget=body.budget,
    )
    await session.commit()
    _schedule_run(task_id=task_id)
    return TaskCreateOut(task_id=task_id, status="PLANNING")


@router.get(
    "",
    response_model=TaskPageOut,
    dependencies=[Depends(require_perm("task:read"))],
)
async def list_tasks(
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
    page: int = 1,
    size: int = 20,
) -> TaskPageOut:
    tid = await _tenant_id(session, str(user["sub"]), x_tenant_id)
    svc = _get_service(session)
    p, s = max(1, page), min(100, max(1, size))
    items, total = await svc.list_tasks(tenant_id=tid, user_id=str(user["sub"]), page=p, size=s)
    return TaskPageOut(items=[_item(r) for r in items], total=total, page=p, size=s)


@router.get(
    "/{task_id}",
    response_model=TaskDetailOut,
    dependencies=[Depends(require_perm("task:read"))],
)
async def get_task(
    task_id: str,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> TaskDetailOut:
    _uuid_or_404(task_id)
    tid = await _tenant_id(session, str(user["sub"]), x_tenant_id)
    svc = _get_service(session)
    try:
        detail = await svc.get_detail(task_id=task_id, tenant_id=tid, user_id=str(user["sub"]))
    except TaskNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    return _detail_out(detail)


@router.post(
    "/{task_id}/pause",
    response_model=TaskMutateOut,
    dependencies=[Depends(require_perm("task:manage"))],
)
async def pause_task(
    task_id: str,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
    version: int | None = None,
) -> TaskMutateOut:
    out, _moved = await _mutate(task_id, user, session, x_tenant_id, version, op="pause")
    return out


@router.post(
    "/{task_id}/resume",
    response_model=TaskMutateOut,
    dependencies=[Depends(require_perm("task:manage"))],
)
async def resume_task(
    task_id: str,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> TaskMutateOut:
    out, moved = await _mutate(task_id, user, session, x_tenant_id, None, op="resume")
    if moved:
        _schedule_run(task_id=task_id)  # 同态幂等(已 RUNNING)不二次派发,防双执行循环
    return out


@router.post(
    "/{task_id}/cancel",
    response_model=TaskMutateOut,
    dependencies=[Depends(require_perm("task:manage"))],
)
async def cancel_task(
    task_id: str,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> TaskMutateOut:
    out, _moved = await _mutate(task_id, user, session, x_tenant_id, None, op="cancel")
    return out


@router.post(
    "/{task_id}/steps/{step_id}/approve",
    response_model=TaskMutateOut,
    dependencies=[Depends(require_perm("task:manage"))],
)
async def approve_step(
    task_id: str,
    step_id: str,
    body: ApproveIn,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> TaskMutateOut:
    uid = str(user["sub"])
    _uuid_or_404(task_id)
    _uuid_or_404(step_id)
    tid = await _tenant_id(session, uid, x_tenant_id)
    svc = _get_service(session)
    try:
        await svc.approve_step(
            task_id=task_id, step_id=step_id, tenant_id=tid, user_id=uid, approve=body.approve
        )
        await session.commit()
    except TaskNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    except TaskInvalidTransition as e:
        # 非法迁移的审计事件已入事务:先提交留痕,再 409(spec 拒绝并留审计)
        await session.commit()
        raise HTTPException(status_code=409, detail=str(e)) from e
    except TaskConflict as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    detail = await svc.get_detail(task_id=task_id, tenant_id=tid, user_id=uid)
    # 批准与拒绝都要派发:批准=该步回队重跑;拒绝=跳过该步续跑其余步骤
    # (编排挂起即停,审批决策是循环唯一的再启动口)
    _schedule_run(task_id=task_id)
    return TaskMutateOut(task_id=task_id, status=str(detail["task"]["status"]))


# ── 内部编排回调(agent 面任务循环回写)──


class PlanStepIn(BaseModel):
    goal: str = Field(min_length=1, max_length=2000)
    require_confirm: bool = False


class TaskPlanIn(BaseModel):
    task_id: str
    tenant_key: str
    steps: list[PlanStepIn] = Field(min_length=1, max_length=32)


@internal_router.post("/plan")
async def task_plan(
    body: TaskPlanIn, session: Annotated[AsyncSession, Depends(get_session)]
) -> dict[str, object]:
    from kongming.task.models import StepPlan

    plans = [StepPlan(goal=s.goal, require_confirm=s.require_confirm) for s in body.steps]
    if not plans:
        raise HTTPException(status_code=422, detail="EMPTY_PLAN")
    tenant_id = await _tenant_key_to_id(session, body.tenant_key)
    try:
        created = await _get_service(session).apply_plan(
            task_id=body.task_id, tenant_id=tenant_id, steps=plans
        )
        await session.commit()
    except TaskConflict as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return {"steps": created}


@internal_router.get("/steps")
async def task_steps(
    task_id: str,
    tenant_key: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    tenant_id = await _tenant_key_to_id(session, tenant_key)
    # 任务-租户归属校验(同 token 不得横读他租户任务步骤)
    task = await SqlTaskRepo(session).get_task_system(task_id=task_id, tenant_id=tenant_id)
    if task is None:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    steps = await SqlTaskRepo(session).get_steps(task_id=task_id)
    return {
        "steps": [
            {
                "id": str(s["id"]),
                "ord": int(str(s["ord"])),
                "goal": str(s["goal"]),
                "status": str(s["status"]),
                "require_confirm": bool(s["require_confirm"]),
                "checkpoint": s.get("checkpoint"),
            }
            for s in steps
        ]
    }


class StepStartIn(BaseModel):
    task_id: str
    tenant_key: str
    step_id: str


@internal_router.post("/step-start")
async def task_step_start(
    body: StepStartIn, session: Annotated[AsyncSession, Depends(get_session)]
) -> dict[str, object]:
    tenant_id = await _tenant_key_to_id(session, body.tenant_key)
    try:
        out = await _get_service(session).begin_step(
            task_id=body.task_id, tenant_id=tenant_id, step_id=body.step_id
        )
        await session.commit()
    except TaskNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    except TaskConflict as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return out


class AppendedStepIn(BaseModel):
    goal: str = Field(min_length=1, max_length=2000)
    require_confirm: bool = False


class StepResultIn(BaseModel):
    task_id: str
    tenant_key: str
    step_id: str
    status: str
    summary: str = ""
    evidence: dict[str, object] = Field(default_factory=dict)
    fail_reason: str | None = None
    checkpoint: dict[str, object] = Field(default_factory=dict)
    tokens_used: int = 0
    appended_steps: list[AppendedStepIn] = Field(default_factory=list[AppendedStepIn])


@internal_router.post("/step-result")
async def task_step_result(
    body: StepResultIn, session: Annotated[AsyncSession, Depends(get_session)]
) -> dict[str, object]:
    from kongming.task.models import StepPlan, StepResult

    result = StepResult(
        step_id=body.step_id,
        status=body.status,
        summary=body.summary,
        evidence=body.evidence,
        fail_reason=body.fail_reason,
        checkpoint=body.checkpoint,
        tokens_used=body.tokens_used,
        appended_steps=[
            StepPlan(goal=s.goal, require_confirm=s.require_confirm) for s in body.appended_steps
        ],
    )
    tenant_id = await _tenant_key_to_id(session, body.tenant_key)
    try:
        decision = await _get_service(session).finish_step(
            task_id=body.task_id, tenant_id=tenant_id, result=result
        )
        await session.commit()
    except TaskNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    except TaskConflict as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    # 证据拒绝也是决策(非提交失败):200 携带决策原文,agent 按拒绝语义
    # 收口(422 会被回调客户端归一为链路失败,拒绝原因丢失、分支不可达)
    return {"decision": decision}


class FinishIn(BaseModel):
    task_id: str
    tenant_key: str
    status: str
    fail_reason: str | None = None


@internal_router.post("/finish", status_code=204)
async def task_finish(
    body: FinishIn, session: Annotated[AsyncSession, Depends(get_session)]
) -> None:
    from kongming.task.models import T_DONE, T_FAILED

    if body.status not in (T_DONE, T_FAILED):
        raise HTTPException(status_code=422, detail="INVALID_FINAL_STATUS")
    tenant_id = await _tenant_key_to_id(session, body.tenant_key)
    try:
        await _get_service(session).finish(
            task_id=body.task_id,
            tenant_id=tenant_id,
            status=body.status,
            fail_reason=body.fail_reason,
        )
        await session.commit()
    except TaskNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    except TaskInvalidTransition as e:
        # 非法迁移的审计事件已入事务:先提交留痕,再 409(spec 拒绝并留审计)
        await session.commit()
        raise HTTPException(status_code=409, detail=str(e)) from e
    except TaskConflict as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


async def _tenant_key_to_id(session: AsyncSession, tenant_key: str) -> str:
    """内部回调的租户解析(agent 传 key,库内为 id;不存在 404 同形)。"""
    from sqlalchemy import text

    row = (
        await session.execute(
            text("SELECT id FROM public.sys_tenant WHERE tenant_key=:k AND status='ACTIVE'"),
            {"k": tenant_key},
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="TENANT_NOT_FOUND") from None
    return str(row)


# ── 内部 ──


def _uuid_or_404(value: str) -> None:
    """路径参数 UUID 形校验:畸形串 404 同形(而非 DBAPI 裸 500)。"""
    try:
        _uuid.UUID(value)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None


def _item(r: dict[str, Any]) -> TaskItem:
    created = r["created_at"]
    updated = r["updated_at"]
    return TaskItem(
        id=str(r["id"]),
        goal=str(r["goal"]),
        status=str(r["status"]),
        datasource_key=str(r["datasource_key"]),
        fail_reason=cast("str | None", r.get("fail_reason")),
        created_at=created.isoformat() if isinstance(created, datetime) else str(created),
        updated_at=updated.isoformat() if isinstance(updated, datetime) else str(updated),
    )


def _detail_out(detail: dict[str, Any]) -> TaskDetailOut:
    task = detail["task"]
    steps = [
        StepOut(
            id=str(s["id"]),
            ord=int(str(s["ord"])),
            goal=str(s["goal"]),
            status=str(s["status"]),
            require_confirm=bool(s.get("require_confirm")),
            evidence=cast("dict[str, object] | None", s.get("evidence")),
            fail_reason=cast("str | None", s.get("fail_reason")),
            checkpoint=cast("dict[str, object] | None", s.get("checkpoint")),
        )
        for s in detail["steps"]
    ]
    task_row = dict(task)
    return TaskDetailOut(
        task=_item(task_row),
        steps=steps,
        events=cast("list[dict[str, object]]", task_row.get("events") or []),
        budget_config=cast("dict[str, object]", task_row.get("budget_config") or {}),
        budget_used=cast("dict[str, object]", task_row.get("budget_used") or {}),
        trace_root=cast("str | None", task_row.get("trace_root")),
    )


async def _mutate(
    task_id: str,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: AsyncSession,
    x_tenant_id: str | None,
    version: int | None,
    *,
    op: str,
) -> tuple[TaskMutateOut, bool]:
    uid = str(user["sub"])
    _uuid_or_404(task_id)
    tid = await _tenant_id(session, uid, x_tenant_id)
    svc = _get_service(session)
    moved = True
    try:
        if op == "pause":
            status = await svc.pause(task_id=task_id, tenant_id=tid, user_id=uid, version=version)
        elif op == "resume":
            status, moved = await svc.resume(task_id=task_id, tenant_id=tid, user_id=uid)
        else:
            status = await svc.cancel(task_id=task_id, tenant_id=tid, user_id=uid)
        await session.commit()
    except TaskNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    except TaskInvalidTransition as e:
        # 非法迁移的审计事件已入事务:先提交留痕,再 409(spec 拒绝并留审计)
        await session.commit()
        raise HTTPException(status_code=409, detail=str(e)) from e
    except TaskConflict as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return TaskMutateOut(task_id=task_id, status=status), moved


# ── 执行派发(拉取式:创建/恢复触发一次;任务自持会话,连接卫生同 088)──

_GEN_TASKS: set[asyncio.Task[None]] = set()

_TASK_ROW_SQL = (
    "SELECT t.id, t.tenant_id, tn.tenant_key, t.user_id, t.goal, t.budget_config,"
    "       t.datasource_key, t.trace_root"
    " FROM public.sys_agent_task t"
    " JOIN public.sys_tenant tn ON tn.id = t.tenant_id"
    " WHERE t.id = CAST(:i AS uuid)"
)


def _schedule_run(*, task_id: str) -> None:
    """后台派发 agent 任务循环(强引用集防 GC);编排回调自持短会话,
    agent 长任务期间不占池;终态收口全部走 service.finish 白名单
    (编排迟到/用户已取消暂停时拒绝越权改写——终态不复活);
    agent 未终态化=链路异常 → INTERRUPTED(可显式恢复),不静默滞留。"""

    async def _run() -> None:
        agent = AgentClient(get_settings())
        collector = _Collector(agent=agent, task_id=task_id)
        try:
            final = await collector.dispatch()
            if final in (m.T_DONE, m.T_FAILED):
                await collector.finish_whitelisted(final)
            elif final not in (
                m.T_PAUSED,
                m.T_AWAITING_APPROVAL,
                m.T_CANCELLED,
                m.T_INTERRUPTED,
            ):
                # 链路异常(agent 不可达/坏报文/未知终态口径)≈ 进程中断:
                # 收口 INTERRUPTED(可显式恢复),不谎称失败终态;白名单拒绝
                # (如审批挂起中)则保持现态
                await collector.finish_whitelisted(
                    m.T_INTERRUPTED, reason="任务编排链路异常(agent 未终态化),可显式恢复"
                )
        except Exception as e:
            log.exception("任务编排后台任务失败: task_id=%s", task_id)
            try:
                await collector.finish_whitelisted(
                    m.T_INTERRUPTED, reason=f"任务编排后台任务异常: {str(e)[:180]}"
                )
            except Exception:
                log.exception("任务中断终态落库失败: task_id=%s", task_id)

    task = asyncio.get_running_loop().create_task(_run())
    _GEN_TASKS.add(task)
    task.add_done_callback(_GEN_TASKS.discard)


class _Collector:
    """一次后台派发的编排驱动与终态收口(段一会话读行,段二 agent,段三白名单收口)。"""

    def __init__(self, *, agent: AgentClient, task_id: str) -> None:
        self._agent = agent
        self._task_id = task_id

    async def _read_row(self) -> dict[str, Any] | None:
        from sqlalchemy import text

        async with get_sessionmaker()() as session:
            row = (
                (await session.execute(text(_TASK_ROW_SQL), {"i": self._task_id}))
                .mappings()
                .one_or_none()
            )
            await session.commit()
        return dict(row) if row is not None else None

    async def dispatch(self) -> str:
        """段一读任务行 + 授权工具注入集(短会话)→ 段二 agent 编排。
        返回 agent 报告的终态口径(任务不存在返回空串)。"""
        from kongming.auth.repository import SqlPermRepo
        from kongming.toolcall import registry as tool_registry

        row = await self._read_row()
        if row is None:
            return ""
        tools: list[dict[str, object]] = []
        async with get_sessionmaker()() as session:
            codes = await SqlPermRepo(session).list_codes(str(row["user_id"]))
            tools = [dict(s) for s in tool_registry.filter_specs(set(codes))]
            await session.commit()
        payload: dict[str, object] = {
            "task_id": self._task_id,
            "trace_root": str(row["trace_root"] or ""),
            # 租户键以任务行归属为准(header 缺省/他人租户视图不污染回调解析)
            "tenant_key": str(row["tenant_key"] or "default"),
            "datasource_key": str(row["datasource_key"] or "default"),
            "user_id": str(row["user_id"]),
            "goal": str(row["goal"] or ""),
            "budget": dict(cast("dict[str, object]", row["budget_config"] or {})),
            "tools": tools,
        }
        result = await self._agent.run_task(payload)
        return str((result or {}).get("final_status") or "")

    async def finish_whitelisted(self, status: str, reason: str | None = None) -> None:
        """段三终态收口:重读当前行后经 service.finish 白名单(白名单+CAS+
        DONE 步骤校验);任务已被用户/控制面推进到不可达终态时拒绝并保持
        现态(终态不复活),不覆盖用户决策。"""
        fresh = await self._read_row()
        if fresh is None:
            return
        async with get_sessionmaker()() as session:
            try:
                await TaskService(SqlTaskRepo(session)).finish(
                    task_id=self._task_id,
                    tenant_id=str(fresh["tenant_id"]),
                    status=status,
                    fail_reason=reason,
                )
                await session.commit()
            except TaskInvalidTransition as e:
                # 非法迁移的审计事件已入事务:提交留痕(与公开端点同口径)
                await session.commit()
                log.info(
                    "任务终态收口被白名单拒绝(审计已留痕): task_id=%s detail=%s",
                    self._task_id,
                    e,
                )
            except TaskConflict as e:
                await session.rollback()
                log.info(
                    "任务终态收口被拒(状态已推进,保持现态): task_id=%s detail=%s",
                    self._task_id,
                    e,
                )
