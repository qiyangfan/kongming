"""task 路由(090/091):公开端点(创建/列表/详情/暂停/恢复/取消/审批
+ 协作拓扑声明 + 协作运行查询)+ 内部编排回调(plan/step-start/step-result/
collab-start/worker-start/worker-result/finish;X-Internal-Token 闸)。

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
    TopologyBotNotFound,
)
from kongming.trace import ensure_trace_id

log = logging.getLogger(__name__)

router = APIRouter(prefix="/tasks", tags=["task"])
internal_router = APIRouter(prefix="/internal/task", tags=["internal"])


class _CollabBotResolver:
    """协作 Bot 运行时解析适配(091;沿用 bot 域运行时口径:租户内存在
    + 启用 + 创建者用户 ACTIVE;人格与工具子集原样带出,工具交集在执行面收)。"""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def resolve(self, *, tenant_id: str, bot_key: str) -> dict[str, object] | None:
        from kongming.bot.repository import SqlBotRepo

        repo = SqlBotRepo(self._s)
        row = await repo.get_by_key(tenant_id=tenant_id, bot_key=bot_key)
        if row is None or not bool(row.get("enabled")):
            return None
        creator = str(row.get("created_by") or "")
        if creator and await repo.find_user_status(creator) != "ACTIVE":
            return None
        tools_raw = row.get("tools")
        tools = (
            [str(t) for t in cast("list[object]", tools_raw)] if isinstance(tools_raw, list) else []
        )
        return {
            "bot_key": bot_key,
            "system_prompt": str(row.get("system_prompt") or ""),
            "tools": tools,
        }


def _get_service(session: AsyncSession) -> TaskService:
    from kongming.task.graph_repo import SqlGraphRepo

    return TaskService(
        SqlTaskRepo(session),
        bot_resolver=_CollabBotResolver(session),
        graph_repo=SqlGraphRepo(session),
    )


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
    collab_topology: dict[str, object] | None = None


class GraphConfirmOut(BaseModel):
    """图内确认行(挂起卡片数据源;add-graph-checkpoint)。"""

    confirm_id: str
    step_id: str
    tool_name: str
    summary: str
    status: str
    expires_at: str
    created_at: str


class TaskDetailOut(BaseModel):
    task: TaskItem
    steps: list[StepOut]
    events: list[dict[str, object]]
    budget_config: dict[str, object]
    budget_used: dict[str, object]
    trace_root: str | None = None
    graph_confirms: list[GraphConfirmOut] = Field(default_factory=list[GraphConfirmOut])


class CollabWorkerOut(BaseModel):
    worker_no: int
    subgoal: str
    bot_key: str | None
    status: str
    conclusion: dict[str, object] | None
    evidence: dict[str, object] | None
    usage: dict[str, object] | None
    trace_id: str | None
    updated_at: str | None = None


class CollabTurnOut(BaseModel):
    """圆桌轮次转写行(115;黑板观测面,追加只写)。"""

    round_no: int
    worker_no: int
    kind: str
    content: dict[str, object] | None
    evidence: dict[str, object] | None
    usage: dict[str, object] | None
    trace_id: str | None
    created_at: str | None = None


class CollabRunOut(BaseModel):
    id: str
    step_id: str
    topology: dict[str, object]
    status: str
    verdict: str | None
    depth: int
    created_at: str | None = None
    updated_at: str | None = None
    workers: list[CollabWorkerOut] = Field(default_factory=list[CollabWorkerOut])
    turns: list[CollabTurnOut] = Field(default_factory=list[CollabTurnOut])


class CollabRunsOut(BaseModel):
    items: list[CollabRunOut]


class CollabParticipantIn(BaseModel):
    """圆桌参与者声明条目(115):role 取内置章程域,bot_key 可空绑定。"""

    role: str | None = Field(default=None, max_length=32)
    bot_key: str | None = Field(default=None, max_length=m.COLLAB_BOT_KEY_MAX)


class CollabWorkerIn(BaseModel):
    """分工名册成员声明条目(collab-worker-roster):Bot 点名,可选别名
    (身份 = name 缺省 bot_key;声明期锁定,运行期不可增减改绑)。"""

    name: str | None = Field(default=None, max_length=m.COLLAB_BOT_KEY_MAX)
    bot_key: str | None = Field(default=None, max_length=m.COLLAB_BOT_KEY_MAX)


class CollabTopologyIn(BaseModel):
    kind: str | None = None
    max_workers: int | None = Field(default=None, ge=1, le=16)
    critic: bool = False
    dispatch: str | None = None
    bot_key: str | None = Field(default=None, max_length=m.COLLAB_BOT_KEY_MAX)
    # 圆桌(115):轮次上限与参与者名册(名册声明期确定,运行期锁定)
    max_rounds: int | None = Field(default=None, ge=1, le=16)
    participants: list[CollabParticipantIn] | None = Field(default=None, max_length=16)
    # 分工 worker 名册(collab-worker-roster):与顶层 bot_key 互斥(逐成员绑定)
    workers: list[CollabWorkerIn] | None = Field(default=None, max_length=16)


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
        # 部署级上限钳制(090 spec「任务级配置 + 部署级上限」)
        budget_caps={
            "max_steps": s.task_budget_max_steps,
            "max_tokens": s.task_budget_max_tokens,
            "max_seconds": s.task_budget_max_seconds,
        },
    )
    await session.commit()
    await _bump_and_schedule(task_id)
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
    svc = _get_service(session)
    p, s = max(1, page), min(100, max(1, size))
    items, total = await svc.list_tasks(
        tenant_id=tid, user_id=str(user["sub"]), page=p, size=s, status=status
    )
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
        await _bump_and_schedule(task_id)  # 同态幂等(已 RUNNING)不二次派发,防双执行循环
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
    await _bump_and_schedule(task_id)
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
    svc = _get_service(session)
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
    await _bump_and_schedule(task_id)
    return TaskMutateOut(task_id=task_id, status=str(detail["task"]["status"]))


# ── 协作(091):拓扑声明 + 运行查询(挂 task:* 权限体系)──


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
    svc = _get_service(session)
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
    svc = _get_service(session)
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
                updated_at=_iso_or_none(w.get("updated_at")),
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
                created_at=_iso_or_none(t.get("created_at")),
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
                created_at=_iso_or_none(r.get("created_at")),
                updated_at=_iso_or_none(r.get("updated_at")),
                workers=workers,
                turns=turns,
            )
        )
    return CollabRunsOut(items=items)


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

    _uuid_body_or_404(body.task_id)
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


class StepStartIn(BaseModel):
    task_id: str
    tenant_key: str
    step_id: str
    epoch: int | None = None


@internal_router.post("/step-start")
async def task_step_start(
    body: StepStartIn, session: Annotated[AsyncSession, Depends(get_session)]
) -> dict[str, object]:
    _uuid_body_or_404(body.task_id, body.step_id)
    tenant_id = await _tenant_key_to_id(session, body.tenant_key)
    try:
        out = await _get_service(session).begin_step(
            task_id=body.task_id, tenant_id=tenant_id, step_id=body.step_id, epoch=body.epoch
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
    epoch: int | None = None
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
        decision = await _get_service(session).finish_step(
            task_id=body.task_id, tenant_id=tenant_id, result=result, epoch=body.epoch
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
    epoch: int | None = None
    status: str
    fail_reason: str | None = None


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
        await _get_service(session).finish(
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


# ── 内部协作回调(091;agent 面编排回写;拓扑以库内声明为准)──


class CollabStartIn(BaseModel):
    task_id: str
    tenant_key: str
    step_id: str
    depth: int = Field(default=1, ge=1, le=8)


@internal_router.post("/collab-start")
async def collab_start(
    body: CollabStartIn, session: Annotated[AsyncSession, Depends(get_session)]
) -> dict[str, object]:
    _uuid_body_or_404(body.task_id, body.step_id)
    tenant_id = await _tenant_key_to_id(session, body.tenant_key)
    try:
        out = await _get_service(session).collab_start_run(
            task_id=body.task_id, step_id=body.step_id, tenant_id=tenant_id, depth=body.depth
        )
        await session.commit()
    except TaskNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    except TaskConflict as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return out


class CollabWorkerStartIn(BaseModel):
    task_id: str
    tenant_key: str
    run_id: str
    worker_no: int = Field(ge=1)
    subgoal: str = Field(min_length=1, max_length=2000)
    # 与 0040 列宽同口径(bot_key VARCHAR(64));缺限长则畸形载荷裸 500
    bot_key: str | None = Field(default=None, max_length=m.COLLAB_BOT_KEY_MAX)
    trace_id: str = Field(default="", max_length=64)


@internal_router.post("/collab-worker-start", status_code=204)
async def collab_worker_start(
    body: CollabWorkerStartIn, session: Annotated[AsyncSession, Depends(get_session)]
) -> None:
    _uuid_body_or_404(body.task_id, body.run_id)
    tenant_id = await _tenant_key_to_id(session, body.tenant_key)
    try:
        await _get_service(session).collab_worker_start(
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


class CollabWorkerResultIn(BaseModel):
    task_id: str
    tenant_key: str
    run_id: str
    worker_no: int = Field(ge=1)
    status: str
    conclusion: dict[str, object] = Field(default_factory=dict)
    evidence: dict[str, object] = Field(default_factory=dict)
    usage: dict[str, object] = Field(default_factory=dict)
    trace_id: str = Field(default="", max_length=64)


@internal_router.post("/collab-worker-result", status_code=204)
async def collab_worker_result(
    body: CollabWorkerResultIn, session: Annotated[AsyncSession, Depends(get_session)]
) -> None:
    _uuid_body_or_404(body.task_id, body.run_id)
    tenant_id = await _tenant_key_to_id(session, body.tenant_key)
    try:
        await _get_service(session).collab_worker_result(
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


class CollabFinishIn(BaseModel):
    task_id: str
    tenant_key: str
    run_id: str
    verdict: str
    pending_count: int = Field(default=0, ge=0)
    dissent: bool = False
    summary: str = Field(default="", max_length=8000)


@internal_router.post("/collab-finish")
async def collab_finish(
    body: CollabFinishIn, session: Annotated[AsyncSession, Depends(get_session)]
) -> dict[str, object]:
    _uuid_body_or_404(body.task_id, body.run_id)
    tenant_id = await _tenant_key_to_id(session, body.tenant_key)
    try:
        status = await _get_service(session).collab_finish_run(
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


class CollabTurnIn(BaseModel):
    """圆桌轮次上账载荷(115;追加只写,唯一键拒绝重复)。"""

    task_id: str
    tenant_key: str
    run_id: str
    round_no: int = Field(ge=1, le=64)
    worker_no: int = Field(ge=0, le=64)
    kind: str
    content: dict[str, object] = Field(default_factory=dict)
    evidence: dict[str, object] = Field(default_factory=dict)
    usage: dict[str, object] = Field(default_factory=dict)
    trace_id: str = Field(default="", max_length=64)


@internal_router.post("/collab-turn", status_code=204)
async def collab_turn(
    body: CollabTurnIn, session: Annotated[AsyncSession, Depends(get_session)]
) -> None:
    _uuid_body_or_404(body.task_id, body.run_id)
    tenant_id = await _tenant_key_to_id(session, body.tenant_key)
    try:
        await _get_service(session).collab_record_turn(
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


class StepSuspendedIn(BaseModel):
    """图内挂起上账载荷(编排回调;add-graph-checkpoint)。"""

    task_id: str
    tenant_key: str
    step_id: str
    epoch: int | None = None
    confirm_id: str = Field(min_length=1, max_length=64)
    tool_name: str = Field(min_length=1, max_length=128)
    summary: str = ""
    thread_id: str = Field(min_length=1, max_length=200)


@internal_router.post("/step-suspended")
async def task_step_suspended(
    body: StepSuspendedIn, session: Annotated[AsyncSession, Depends(get_session)]
) -> dict[str, object]:
    _uuid_body_or_404(body.task_id, body.step_id)
    tenant_id = await _tenant_key_to_id(session, body.tenant_key)
    try:
        out = await _get_service(session).suspend_on_graph_confirm(
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


class GraphCheckpointIn(BaseModel):
    """检查点写入载荷(blob 对控制面不透明;agent 序列化/反序列化)。"""

    tenant_key: str
    thread_id: str = Field(min_length=1, max_length=200)
    checkpoint_ns: str = ""
    kind: str = Field(default="checkpoint", pattern="^(checkpoint|write)$")
    checkpoint_id: str | None = Field(default=None, max_length=64)
    task_id: str | None = None
    step_id: str | None = None
    payload_type: str = Field(default="", max_length=64)
    payload_b64: str = Field(min_length=1)


async def _thread_task_or_404(session: AsyncSession, tenant_id: str, thread_id: str) -> str:
    """线程→任务归属校验:thread_id 确定性派生(compile D3),任务不存在/
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
    except Exception:
        await session.rollback()
        raise HTTPException(status_code=503, detail="CHECKPOINT_STORE_FAILED") from None
    return {"ok": True}


class GraphCheckpointDeleteIn(BaseModel):
    tenant_key: str
    thread_id: str = Field(min_length=1, max_length=200)


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
    """内部回调请求体 UUID 形校验(091 补齐 B3:畸形串 404 同形,
    不再穿透到 DBAPI 裸 500;agent 面按回调失败语义收敛)。"""
    for v in values:
        _uuid_or_404(v)


def _iso_or_none(value: object) -> str | None:
    if value is None:
        return None
    return value.isoformat() if isinstance(value, datetime) else str(value)


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
            collab_topology=cast("dict[str, object] | None", s.get("collab_topology")) or None,
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
        graph_confirms=[
            GraphConfirmOut(
                confirm_id=str(c["confirm_id"]),
                step_id=str(c["step_id"]),
                tool_name=str(c["tool_name"]),
                summary=str(c["summary"]),
                status=str(c["status"]),
                expires_at=str(c["expires_at"]),
                created_at=str(c["created_at"]),
            )
            for c in cast("list[dict[str, object]]", detail.get("graph_confirms") or [])
        ],
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
    "       t.datasource_key, t.trace_root, t.dispatch_epoch"
    " FROM public.sys_agent_task t"
    " JOIN public.sys_tenant tn ON tn.id = t.tenant_id"
    " WHERE t.id = CAST(:i AS uuid)"
)


async def _bump_and_schedule(task_id: str) -> int | None:
    """真实派发前置:派发代际原子自增,携新代数派发(0042 双执行者闸)。
    代数失配会拦下旧循环的全部后续回调(begin abort/回写 stale/终态 409)。"""

    async with get_sessionmaker()() as session:
        epoch = await SqlTaskRepo(session).bump_dispatch_epoch(task_id=task_id)
        await session.commit()
    if epoch is None:
        return None
    _schedule_run(task_id=task_id, epoch=epoch)
    return epoch


def _schedule_run(*, task_id: str, epoch: int | None = None) -> None:
    """后台派发 agent 任务循环(强引用集防 GC);编排回调自持短会话,
    agent 长任务期间不占池;终态收口全部走 service.finish 白名单
    (编排迟到/用户已取消暂停时拒绝越权改写——终态不复活);
    agent 未终态化=链路异常 → INTERRUPTED(可显式恢复),不静默滞留;
    代际失配(STALE)或收口前代数已推进 → 放弃收口(在位派发负责)。"""

    async def _run() -> None:
        agent = AgentClient(get_settings())
        collector = _Collector(agent=agent, task_id=task_id, epoch=epoch)
        try:
            final = await collector.dispatch()
            if final == "STALE":
                log.info("任务编排代际失配退出(新派发接管): task_id=%s", task_id)
                return
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

    def __init__(self, *, agent: AgentClient, task_id: str, epoch: int | None = None) -> None:
        self._agent = agent
        self._task_id = task_id
        self._epoch = epoch

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
            "epoch": self._epoch or 0,
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
        if self._epoch is not None and int(fresh.get("dispatch_epoch") or 0) != self._epoch:
            # 代数已推进(新派发接管):本循环不得收口任务终态
            log.info(
                "任务终态收口放弃(派发代际已推进): task_id=%s epoch=%s",
                self._task_id,
                self._epoch,
            )
            return
        async with get_sessionmaker()() as session:
            try:
                await TaskService(SqlTaskRepo(session)).finish(
                    task_id=self._task_id,
                    tenant_id=str(fresh["tenant_id"]),
                    status=status,
                    fail_reason=reason,
                    # 与内部 finish 端点同口径:终态收口必须持派发代际,
                    # service 内闸 + 写入谓词双闸(复核与收口间仍有并发窗口)
                    epoch=self._epoch,
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
