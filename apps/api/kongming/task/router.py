"""任务路由提供用户端任务操作和 Agent 内部回调。

用户端按本人和租户隔离；内部端点由 X-Internal-Token 保护。
创建、有效恢复和审批决策会触发 Agent 执行。
"""

import uuid as _uuid
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.deps import current_user, require_perm
from kongming.config import get_settings
from kongming.db import get_session, uuid7
from kongming.task import models as m
from kongming.task.dependencies import get_collab_ledger_service, get_task_service
from kongming.task.dispatch import bump_and_schedule
from kongming.task.repository import SqlTaskRepo
from kongming.task.schemas import (
    ApproveIn,
    CollabFinishIn,
    CollabRunOut,
    CollabRunsOut,
    CollabStartIn,
    CollabTopologyIn,
    CollabTurnIn,
    CollabTurnOut,
    CollabWorkerOut,
    CollabWorkerResultIn,
    CollabWorkerStartIn,
    CreateIn,
    FinishIn,
    GraphCheckpointDeleteIn,
    GraphCheckpointIn,
    StepResultIn,
    StepStartIn,
    StepSuspendedIn,
    TaskCreateOut,
    TaskDetailOut,
    TaskMutateOut,
    TaskPageOut,
    TaskPlanIn,
    iso_or_none,
    to_task_detail_out,
    to_task_item,
)
from kongming.task.service import (
    TaskConflict,
    TaskInvalidTransition,
    TaskNotFound,
    TopologyBotNotFound,
)
from kongming.trace import ensure_trace_id

router = APIRouter(prefix="/tasks", tags=["task"])
internal_router = APIRouter(prefix="/internal/task", tags=["internal"])


async def _tenant_id(session: AsyncSession, user_id: str, tenant_key: str | None) -> str:
    from kongming.tenant import resolve_tenant

    resolved = await resolve_tenant(session, user_id=user_id, tenant_key=tenant_key)
    if resolved is None:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None
    return resolved[0]


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
    svc = get_task_service(session)
    s = get_settings()
    await svc.create(
        task_id=task_id,
        tenant_id=tid,
        user_id=uid,
        goal=body.goal,
        datasource_key=body.datasource_key,
        session_id=body.session_id,
        trace_root=trace_root,
        budget=body.budget,
        # 部署级上限钳制(spec「任务级配置 + 部署级上限」)
        budget_caps={
            "max_steps": s.task_budget_max_steps,
            "max_tokens": s.task_budget_max_tokens,
            "max_seconds": s.task_budget_max_seconds,
        },
    )
    await session.commit()
    await bump_and_schedule(task_id)
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
    status: str | None = None,
) -> TaskPageOut:
    tid = await _tenant_id(session, str(user["sub"]), x_tenant_id)
    if status is not None and status not in m.TASK_TRANSITIONS:
        raise HTTPException(status_code=422, detail="INVALID_STATUS") from None
    svc = get_task_service(session)
    p, s = max(1, page), min(100, max(1, size))
    items, total = await svc.list_tasks(
        tenant_id=tid, user_id=str(user["sub"]), page=p, size=s, status=status
    )
    return TaskPageOut(items=[to_task_item(r) for r in items], total=total, page=p, size=s)


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
    svc = get_task_service(session)
    try:
        detail = await svc.get_detail(task_id=task_id, tenant_id=tid, user_id=str(user["sub"]))
    except TaskNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    return to_task_detail_out(detail)


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
        await bump_and_schedule(task_id)  # 同态幂等(已 RUNNING)不二次派发,防双执行循环
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
    svc = get_task_service(session)
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
    await bump_and_schedule(task_id)
    return TaskMutateOut(task_id=task_id, status=str(detail["task"]["status"]))


@router.post(
    "/{task_id}/confirms/{confirm_id}",
    response_model=TaskMutateOut,
    dependencies=[Depends(require_perm("task:manage"))],
)
async def decide_graph_confirm(
    task_id: str,
    confirm_id: str,
    body: ApproveIn,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> TaskMutateOut:
    """图内确认处置(归属人-only):批准=动作恢复执行;拒绝=动作跳过观察喂回。
    处置后步骤回队并重派编排(图从挂起点精确恢复,非整步重跑)。"""
    uid = str(user["sub"])
    _uuid_or_404(task_id)
    tid = await _tenant_id(session, uid, x_tenant_id)
    svc = get_task_service(session)
    try:
        await svc.decide_graph_confirm(
            task_id=task_id,
            tenant_id=tid,
            user_id=uid,
            confirm_id=confirm_id,
            approve=body.approve,
        )
        await session.commit()
    except TaskNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    except TaskInvalidTransition as e:
        await session.commit()
        raise HTTPException(status_code=409, detail=str(e)) from e
    except TaskConflict as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    detail = await svc.get_detail(task_id=task_id, tenant_id=tid, user_id=uid)
    # 批准/拒绝都要派发:图从挂起点恢复是循环唯一的再启动口(同 approve_step)
    await bump_and_schedule(task_id)
    return TaskMutateOut(task_id=task_id, status=str(detail["task"]["status"]))


# ── 协作:拓扑声明 + 运行查询(挂 task:* 权限体系)──


@router.put(
    "/{task_id}/steps/{step_id}/collab-topology",
    response_model=dict[str, object],
    dependencies=[Depends(require_perm("task:manage"))],
)
async def set_collab_topology(
    task_id: str,
    step_id: str,
    body: CollabTopologyIn,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    """步骤声明协作拓扑(PENDING 步骤;归一+部署级钳制后落库,声明后该步
    执行即协作)。Bot 绑定校验沿用 Bot 运行时口径(存在/启用/创建者 ACTIVE)。"""
    uid = str(user["sub"])
    _uuid_or_404(task_id)
    _uuid_or_404(step_id)
    tid = await _tenant_id(session, uid, x_tenant_id)
    svc = get_task_service(session)
    try:
        topo = await svc.set_step_topology(
            task_id=task_id,
            step_id=step_id,
            tenant_id=tid,
            user_id=uid,
            raw_topology=body.model_dump(exclude_none=True),
            max_workers_cap=get_settings().collab_max_workers,
        )
        await session.commit()
    except TaskNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    except TopologyBotNotFound as e:
        raise HTTPException(status_code=422, detail=f"INVALID_BOT: {e}") from e
    except TaskConflict as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return topo


@router.get(
    "/{task_id}/collab-runs",
    response_model=CollabRunsOut,
    dependencies=[Depends(require_perm("task:read"))],
)
async def list_collab_runs(
    task_id: str,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> CollabRunsOut:
    """协作运行清单(任务维度,含 worker 账本;协作观测面单源)。"""
    uid = str(user["sub"])
    _uuid_or_404(task_id)
    tid = await _tenant_id(session, uid, x_tenant_id)
    svc = get_task_service(session)
    try:
        runs = await svc.list_collab_runs(task_id=task_id, tenant_id=tid, user_id=uid)
    except TaskNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    items: list[CollabRunOut] = []
    for r in runs:
        workers = [
            CollabWorkerOut(
                worker_no=int(str(w.get("worker_no") or 0)),
                subgoal=str(w.get("subgoal") or ""),
                bot_key=cast("str | None", w.get("bot_key")),
                status=str(w.get("status") or ""),
                conclusion=cast("dict[str, object] | None", w.get("conclusion")),
                evidence=cast("dict[str, object] | None", w.get("evidence")),
                usage=cast("dict[str, object] | None", w.get("usage")),
                trace_id=cast("str | None", w.get("trace_id")),
                updated_at=iso_or_none(w.get("updated_at")),
            )
            for w in cast("list[dict[str, Any]]", r.get("workers") or [])
        ]
        turns = [
            CollabTurnOut(
                round_no=int(str(t.get("round_no") or 0)),
                worker_no=int(str(t.get("worker_no") or 0)),
                kind=str(t.get("kind") or ""),
                content=cast("dict[str, object] | None", t.get("content")),
                evidence=cast("dict[str, object] | None", t.get("evidence")),
                usage=cast("dict[str, object] | None", t.get("usage")),
                trace_id=cast("str | None", t.get("trace_id")),
                created_at=iso_or_none(t.get("created_at")),
            )
            for t in cast("list[dict[str, Any]]", r.get("turns") or [])
        ]
        items.append(
            CollabRunOut(
                id=str(r["id"]),
                step_id=str(r["step_id"]),
                topology=cast("dict[str, object]", r.get("topology") or {}),
                status=str(r.get("status") or ""),
                verdict=cast("str | None", r.get("verdict")),
                depth=int(str(r.get("depth") or 1)),
                created_at=iso_or_none(r.get("created_at")),
                updated_at=iso_or_none(r.get("updated_at")),
                workers=workers,
                turns=turns,
            )
        )
    return CollabRunsOut(items=items)


# ── 内部编排回调(agent 面任务循环回写)──


@internal_router.post("/plan")
async def task_plan(
    body: TaskPlanIn, session: Annotated[AsyncSession, Depends(get_session)]
) -> dict[str, object]:
    from kongming.task.models import StepPlan

    _uuid_body_or_404(body.task_id)
    plans = [StepPlan(goal=s.goal, require_confirm=s.require_confirm) for s in body.steps]
    if not plans:
        raise HTTPException(status_code=422, detail="EMPTY_PLAN")
    tenant_id = await _tenant_key_to_id(session, body.tenant_key)
    try:
        created = await get_task_service(session).apply_plan(
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
    _uuid_body_or_404(task_id)
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


@internal_router.get("/context")
async def task_context(
    task_id: str,
    tenant_key: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    """worker 续读入口(spec「派发与上下文受控传递」):自身任务的目标全文
    与各步骤状态/结论摘要。截断尾提示的按需读取端点;载荷整体钳制 8KB
    (续读是按需补救,不是整份下传的旁路);跨租户 404 同形。"""
    import json as _json

    _uuid_body_or_404(task_id)
    tenant_id = await _tenant_key_to_id(session, tenant_key)
    task = await SqlTaskRepo(session).get_task_system(task_id=task_id, tenant_id=tenant_id)
    if task is None:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    steps = await SqlTaskRepo(session).get_steps(task_id=task_id)
    step_items = [
        {
            "ord": int(str(s["ord"])),
            "goal": str(s["goal"]),
            "status": str(s["status"]),
            "evidence": s.get("evidence"),
            "fail_reason": s.get("fail_reason"),
        }
        for s in steps
    ]
    payload: dict[str, object] = {"goal": str(task.get("goal") or ""), "steps": step_items}
    encoded = _json.dumps(payload, ensure_ascii=False, default=str)
    if len(encoded.encode()) > 8 * 1024:
        # 超限先裁步骤明细保留目标全文;目标全文自身也可能超限(创建口径
        # ≤4000 字,CJK 可至 ~12KB),按字节截断保留指令头部(6KB 留出
        # JSON 结构余量,decode ignore 守住多字节字符边界),截断如实标注
        payload["steps"] = []
        payload["truncated"] = True
        if len(_json.dumps(payload, ensure_ascii=False, default=str).encode()) > 8 * 1024:
            keep = str(payload["goal"]).encode("utf-8")[: 6 * 1024].decode("utf-8", "ignore")
            payload["goal"] = keep
        encoded = _json.dumps(payload, ensure_ascii=False, default=str)
    else:
        payload["truncated"] = False
    return payload


@internal_router.post("/step-start")
async def task_step_start(
    body: StepStartIn, session: Annotated[AsyncSession, Depends(get_session)]
) -> dict[str, object]:
    _uuid_body_or_404(body.task_id, body.step_id)
    tenant_id = await _tenant_key_to_id(session, body.tenant_key)
    try:
        out = await get_task_service(session).begin_step(
            task_id=body.task_id, tenant_id=tenant_id, step_id=body.step_id, epoch=body.epoch
        )
        await session.commit()
    except TaskNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    except TaskConflict as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return out


@internal_router.post("/step-result")
async def task_step_result(
    body: StepResultIn, session: Annotated[AsyncSession, Depends(get_session)]
) -> dict[str, object]:
    from kongming.task.models import StepPlan, StepResult

    _uuid_body_or_404(body.task_id, body.step_id)
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
        decision = await get_task_service(session).finish_step(
            task_id=body.task_id, tenant_id=tenant_id, result=result, epoch=body.epoch
        )
        await session.commit()
    except TaskNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    except TaskConflict as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    # 证据校验拒绝属于业务决策，使用 200 保留原始 decision；422 会被客户端归一为失败并丢失拒绝原因。
    return {"decision": decision}


@internal_router.post("/finish", status_code=204)
async def task_finish(
    body: FinishIn, session: Annotated[AsyncSession, Depends(get_session)]
) -> None:
    from kongming.task.models import T_DONE, T_FAILED

    _uuid_body_or_404(body.task_id)
    if body.status not in (T_DONE, T_FAILED):
        raise HTTPException(status_code=422, detail="INVALID_FINAL_STATUS")
    tenant_id = await _tenant_key_to_id(session, body.tenant_key)
    try:
        await get_task_service(session).finish(
            task_id=body.task_id,
            tenant_id=tenant_id,
            status=body.status,
            fail_reason=body.fail_reason,
            epoch=body.epoch,
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


# ── 内部协作回调(agent 面编排回写;拓扑以库内声明为准)──


@internal_router.post("/collab-start")
async def collab_start(
    body: CollabStartIn, session: Annotated[AsyncSession, Depends(get_session)]
) -> dict[str, object]:
    _uuid_body_or_404(body.task_id, body.step_id)
    tenant_id = await _tenant_key_to_id(session, body.tenant_key)
    try:
        out = await get_collab_ledger_service(session).collab_start_run(
            task_id=body.task_id, step_id=body.step_id, tenant_id=tenant_id, depth=body.depth
        )
        await session.commit()
    except TaskNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    except TaskConflict as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return out


@internal_router.post("/collab-worker-start", status_code=204)
async def collab_worker_start(
    body: CollabWorkerStartIn, session: Annotated[AsyncSession, Depends(get_session)]
) -> None:
    _uuid_body_or_404(body.task_id, body.run_id)
    tenant_id = await _tenant_key_to_id(session, body.tenant_key)
    try:
        await get_collab_ledger_service(session).collab_worker_start(
            task_id=body.task_id,
            run_id=body.run_id,
            tenant_id=tenant_id,
            worker_no=body.worker_no,
            subgoal=body.subgoal,
            bot_key=body.bot_key,
            trace_id=body.trace_id,
        )
        await session.commit()
    except TaskNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    except TaskConflict as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@internal_router.post("/collab-worker-result", status_code=204)
async def collab_worker_result(
    body: CollabWorkerResultIn, session: Annotated[AsyncSession, Depends(get_session)]
) -> None:
    _uuid_body_or_404(body.task_id, body.run_id)
    tenant_id = await _tenant_key_to_id(session, body.tenant_key)
    try:
        await get_collab_ledger_service(session).collab_worker_result(
            task_id=body.task_id,
            run_id=body.run_id,
            tenant_id=tenant_id,
            worker_no=body.worker_no,
            status=body.status,
            conclusion=body.conclusion,
            evidence=body.evidence,
            usage=body.usage,
            trace_id=body.trace_id,
        )
        await session.commit()
    except TaskNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    except TaskConflict as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@internal_router.post("/collab-finish")
async def collab_finish(
    body: CollabFinishIn, session: Annotated[AsyncSession, Depends(get_session)]
) -> dict[str, object]:
    _uuid_body_or_404(body.task_id, body.run_id)
    tenant_id = await _tenant_key_to_id(session, body.tenant_key)
    try:
        status = await get_collab_ledger_service(session).collab_finish_run(
            task_id=body.task_id,
            run_id=body.run_id,
            tenant_id=tenant_id,
            verdict=body.verdict,
            pending_count=body.pending_count,
            dissent=body.dissent,
            summary=body.summary,
        )
        await session.commit()
    except TaskNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    except TaskConflict as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return {"status": status}


@internal_router.post("/collab-turn", status_code=204)
async def collab_turn(
    body: CollabTurnIn, session: Annotated[AsyncSession, Depends(get_session)]
) -> None:
    _uuid_body_or_404(body.task_id, body.run_id)
    tenant_id = await _tenant_key_to_id(session, body.tenant_key)
    try:
        await get_collab_ledger_service(session).collab_record_turn(
            task_id=body.task_id,
            run_id=body.run_id,
            tenant_id=tenant_id,
            round_no=body.round_no,
            worker_no=body.worker_no,
            kind=body.kind,
            content=body.content,
            evidence=body.evidence,
            usage=body.usage,
            trace_id=body.trace_id,
        )
        await session.commit()
    except TaskNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    except TaskConflict as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@internal_router.post("/step-suspended")
async def task_step_suspended(
    body: StepSuspendedIn, session: Annotated[AsyncSession, Depends(get_session)]
) -> dict[str, object]:
    _uuid_body_or_404(body.task_id, body.step_id)
    tenant_id = await _tenant_key_to_id(session, body.tenant_key)
    try:
        out = await get_task_service(session).suspend_on_graph_confirm(
            task_id=body.task_id,
            tenant_id=tenant_id,
            step_id=body.step_id,
            epoch=body.epoch,
            confirm_id=body.confirm_id,
            tool_name=body.tool_name,
            summary=body.summary,
            thread_id=body.thread_id,
        )
        await session.commit()
    except TaskNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    except TaskConflict as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return out


async def _thread_task_or_404(session: AsyncSession, tenant_id: str, thread_id: str) -> str:
    """线程→任务归属校验:thread_id 确定性派生,任务不存在/
    跨租户一律 404 同形(存储不可越租户读写)。"""
    from sqlalchemy import text as _text

    task_id = thread_id.split(":", 2)[1] if thread_id.startswith("task:") else ""
    _uuid_or_404(task_id)
    row = (
        await session.execute(
            _text(
                "SELECT id FROM public.sys_agent_task"
                " WHERE id = CAST(:i AS uuid) AND tenant_id = CAST(:t AS uuid)"
            ),
            {"i": task_id, "t": tenant_id},
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    return task_id


@internal_router.put("/graph-checkpoint")
async def graph_checkpoint_put(
    body: GraphCheckpointIn, session: Annotated[AsyncSession, Depends(get_session)]
) -> dict[str, object]:
    import base64

    tenant_id = await _tenant_key_to_id(session, body.tenant_key)
    task_id = await _thread_task_or_404(session, tenant_id, body.thread_id)
    from kongming.task.graph_repo import SqlGraphRepo

    try:
        await SqlGraphRepo(session).put_checkpoint(
            tenant_id=tenant_id,
            thread_id=body.thread_id,
            checkpoint_ns=body.checkpoint_ns,
            kind=body.kind,
            checkpoint_id=body.checkpoint_id,
            task_id=task_id,
            step_id=body.step_id or "",
            payload_type=body.payload_type,
            payload=base64.b64decode(body.payload_b64),
        )
        await session.commit()
    except Exception:  # noqa: BLE001
        await session.rollback()
        raise HTTPException(status_code=503, detail="CHECKPOINT_STORE_FAILED") from None
    return {"ok": True}


@internal_router.get("/graph-checkpoint/latest")
async def graph_checkpoint_latest(
    tenant_key: str,
    thread_id: str,
    checkpoint_ns: str = "",
    session: Annotated[AsyncSession, Depends(get_session)] = None,  # type: ignore[assignment]
) -> dict[str, object]:
    import base64

    tenant_id = await _tenant_key_to_id(session, tenant_key)
    await _thread_task_or_404(session, tenant_id, thread_id)
    from kongming.task.graph_repo import SqlGraphRepo

    row = await SqlGraphRepo(session).latest_checkpoint(
        tenant_id=tenant_id, thread_id=thread_id, checkpoint_ns=checkpoint_ns
    )
    await session.commit()
    if row is None:
        raise HTTPException(status_code=404, detail="NOT_FOUND")
    return {
        "checkpoint_id": row["checkpoint_id"],
        "payload_type": str(row["payload_type"] or ""),
        "payload_b64": base64.b64encode(bytes(row["payload"])).decode("ascii"),
    }


@internal_router.get("/graph-checkpoint/writes")
async def graph_checkpoint_writes(
    tenant_key: str,
    thread_id: str,
    checkpoint_ns: str = "",
    checkpoint_id: str = "",
    session: Annotated[AsyncSession, Depends(get_session)] = None,  # type: ignore[assignment]
) -> dict[str, object]:
    import base64

    tenant_id = await _tenant_key_to_id(session, tenant_key)
    await _thread_task_or_404(session, tenant_id, thread_id)
    from kongming.task.graph_repo import SqlGraphRepo

    rows = await SqlGraphRepo(session).checkpoint_writes(
        tenant_id=tenant_id,
        thread_id=thread_id,
        checkpoint_ns=checkpoint_ns,
        checkpoint_id=checkpoint_id,
    )
    await session.commit()
    return {
        "writes": [
            {
                "write_task_id": str(r["source_task_id"] or ""),
                "payload_type": str(r["payload_type"] or ""),
                "payload_b64": base64.b64encode(bytes(r["payload"])).decode("ascii"),
            }
            for r in rows
        ]
    }


@internal_router.post("/graph-checkpoint/delete")
async def graph_checkpoint_delete_post(
    body: GraphCheckpointDeleteIn, session: Annotated[AsyncSession, Depends(get_session)]
) -> dict[str, object]:
    """线程清理(POST 形态;与 agent 回调通道对称,终态钩子/降级清理用)。"""
    tenant_id = await _tenant_key_to_id(session, body.tenant_key)
    await _thread_task_or_404(session, tenant_id, body.thread_id)
    from kongming.task.graph_repo import SqlGraphRepo

    deleted = await SqlGraphRepo(session).delete_thread(
        tenant_id=tenant_id, thread_id=body.thread_id
    )
    await session.commit()
    return {"deleted": deleted}


@internal_router.delete("/graph-checkpoint")
async def graph_checkpoint_delete(
    tenant_key: str,
    thread_id: str,
    session: Annotated[AsyncSession, Depends(get_session)] = None,  # type: ignore[assignment]
) -> dict[str, object]:
    tenant_id = await _tenant_key_to_id(session, tenant_key)
    await _thread_task_or_404(session, tenant_id, thread_id)
    from kongming.task.graph_repo import SqlGraphRepo

    deleted = await SqlGraphRepo(session).delete_thread(tenant_id=tenant_id, thread_id=thread_id)
    await session.commit()
    return {"deleted": deleted}


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


def _uuid_body_or_404(*values: str) -> None:
    """内部回调请求体 UUID 形校验:畸形串 404 同形,
    不穿透到 DBAPI 裸 500(agent 面按回调失败语义收敛)。"""
    for v in values:
        _uuid_or_404(v)


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
    svc = get_task_service(session)
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
