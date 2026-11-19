"""task 请求/响应模型与行投影转换(模型类名冻结:OpenAPI components 单源)。"""

from datetime import datetime
from typing import Any, cast

from pydantic import BaseModel, Field

from kongming.task import models as m


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
    """图内确认行(挂起卡片数据源;graph-checkpoint)。"""

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
    """圆桌轮次转写行(黑板观测面,追加只写)。"""

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
    """圆桌参与者声明条目:role 取内置章程域,bot_key 可空绑定。"""

    role: str | None = Field(default=None, max_length=32)
    bot_key: str | None = Field(default=None, max_length=m.COLLAB_BOT_KEY_MAX)


class CollabWorkerIn(BaseModel):
    """分工名册成员声明条目:Bot 点名,可选别名
    (身份 = name 缺省 bot_key;声明期锁定,运行期不可增减改绑)。"""

    name: str | None = Field(default=None, max_length=m.COLLAB_BOT_KEY_MAX)
    bot_key: str | None = Field(default=None, max_length=m.COLLAB_BOT_KEY_MAX)


class CollabTopologyIn(BaseModel):
    kind: str | None = None
    max_workers: int | None = Field(default=None, ge=1, le=16)
    critic: bool = False
    dispatch: str | None = None
    bot_key: str | None = Field(default=None, max_length=m.COLLAB_BOT_KEY_MAX)
    # 圆桌:轮次上限与参与者名册(名册声明期确定,运行期锁定)
    max_rounds: int | None = Field(default=None, ge=1, le=16)
    participants: list[CollabParticipantIn] | None = Field(default=None, max_length=16)
    # 分工 worker 名册(collab-worker-roster):与顶层 bot_key 互斥(逐成员绑定)
    workers: list[CollabWorkerIn] | None = Field(default=None, max_length=16)


class TaskMutateOut(BaseModel):
    task_id: str
    status: str


class ApproveIn(BaseModel):
    approve: bool = True


class PlanStepIn(BaseModel):
    goal: str = Field(min_length=1, max_length=2000)
    require_confirm: bool = False


class TaskPlanIn(BaseModel):
    task_id: str
    tenant_key: str
    steps: list[PlanStepIn] = Field(min_length=1, max_length=32)


class StepStartIn(BaseModel):
    task_id: str
    tenant_key: str
    step_id: str
    epoch: int | None = None


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


class FinishIn(BaseModel):
    task_id: str
    tenant_key: str
    epoch: int | None = None
    status: str
    fail_reason: str | None = None


class CollabStartIn(BaseModel):
    task_id: str
    tenant_key: str
    step_id: str
    depth: int = Field(default=1, ge=1, le=8)


class CollabWorkerStartIn(BaseModel):
    task_id: str
    tenant_key: str
    run_id: str
    worker_no: int = Field(ge=1)
    subgoal: str = Field(min_length=1, max_length=2000)
    # 限长与库列 bot_key VARCHAR(64) 同口径;缺限长则畸形载荷裸 500
    bot_key: str | None = Field(default=None, max_length=m.COLLAB_BOT_KEY_MAX)
    trace_id: str = Field(default="", max_length=64)


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


class CollabFinishIn(BaseModel):
    task_id: str
    tenant_key: str
    run_id: str
    verdict: str
    pending_count: int = Field(default=0, ge=0)
    dissent: bool = False
    summary: str = Field(default="", max_length=8000)


class CollabTurnIn(BaseModel):
    """圆桌轮次上账载荷(追加只写,唯一键拒绝重复)。"""

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


class StepSuspendedIn(BaseModel):
    """图内挂起上账载荷(编排回调;graph-checkpoint)。"""

    task_id: str
    tenant_key: str
    step_id: str
    epoch: int | None = None
    confirm_id: str = Field(min_length=1, max_length=64)
    tool_name: str = Field(min_length=1, max_length=128)
    summary: str = ""
    thread_id: str = Field(min_length=1, max_length=200)


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


class GraphCheckpointDeleteIn(BaseModel):
    tenant_key: str
    thread_id: str = Field(min_length=1, max_length=200)


def iso_or_none(value: object) -> str | None:
    """datetime → ISO 串;其余原样 str;None 透传。"""
    if value is None:
        return None
    return value.isoformat() if isinstance(value, datetime) else str(value)


def to_task_item(r: dict[str, Any]) -> TaskItem:
    """任务行 → 列表项投影。"""
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


def to_task_detail_out(detail: dict[str, Any]) -> TaskDetailOut:
    """任务详情(行+步骤+事件+图确认) → 响应投影。"""
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
        task=to_task_item(task_row),
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
