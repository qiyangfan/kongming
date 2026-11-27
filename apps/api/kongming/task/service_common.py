"""task 用例公共小件:时钟与状态迁移辅助(不依赖 Web 框架或数据库)。"""

from datetime import UTC, datetime
from typing import Any

from kongming.task import models as m
from kongming.task.errors import TaskConflict, TaskInvalidTransition
from kongming.task.ports import TaskRepo


def now_iso() -> str:
    """当前 UTC 时间的事件时间戳(ISO 8601)。"""
    return datetime.now(UTC).isoformat()


def epoch_mismatch(task: dict[str, Any], epoch: int | None) -> bool:
    """派发代际是否失配(epoch 缺席=不校验);失配时旧编排循环须退出。"""
    return epoch is not None and epoch != int(task.get("dispatch_epoch") or 0)


async def move_task(
    repo: TaskRepo,
    *,
    task_id: str,
    tenant_id: str,
    user_id: str,
    task: dict[str, Any],
    to: str,
    fail_reason: str | None = None,
    expect_version: int | None = None,
    event_kind: str = "transition",
) -> str:
    """状态迁移(白名单校验 → CAS;非法迁移落事件审计并抛冲突)。
    同态请求幂等收敛(重复 pause/resume/取消竞态返回当前态,不作迁移)。"""
    current = str(task["status"])
    if current == to:
        return to
    try:
        m.transition(current, to)
    except m.InvalidTransition as e:
        await repo.transition_task(
            task_id=task_id,
            tenant_id=tenant_id,
            user_id=user_id,
            from_status=current,
            to_status=current,
            event={"at": now_iso(), "kind": "invalid_transition", "detail": str(e)},
        )
        raise TaskInvalidTransition(f"非法状态迁移: {e}") from e
    row = await repo.transition_task(
        task_id=task_id,
        tenant_id=tenant_id,
        user_id=user_id,
        from_status=current,
        to_status=to,
        fail_reason=fail_reason,
        event={"at": now_iso(), "kind": event_kind, "to": to},
        expect_version=expect_version,
    )
    if row is None:
        raise TaskConflict("任务状态/版本已变更,请刷新重试")
    if to in m.TASK_TERMINAL:
        from kongming.control.events import report_product_event

        report_product_event(
            "task.terminal", tenant_id=tenant_id, user_id=user_id, attributes={"status": to}
        )
    return to
