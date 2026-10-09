"""task 用例(090):创建/迁移/审批/预算/事件(用例层;编排回调经内部端点)。

原则:状态迁移全部经白名单 + CAS(乐观锁);非法迁移拒绝并留事件审计;
审批归属=任务归属人;预算超限 PAUSED 诚实收口。
"""

import logging
from datetime import UTC, datetime
from typing import Any, cast

from kongming.task import models as m
from kongming.task.ports import TaskRepo

log = logging.getLogger(__name__)


class TaskNotFound(Exception):
    """不存在/越权(404 同形)。"""


class TaskConflict(Exception):
    """状态/版本冲突(409)。"""


class TaskInvalidTransition(TaskConflict):
    """非法状态迁移(白名单拒绝;审计事件已落,端点需先 commit 再 409)。"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


class TaskService:
    def __init__(self, repo: TaskRepo) -> None:
        self._repo = repo

    async def create(
        self,
        *,
        task_id: str,
        tenant_id: str,
        user_id: str,
        goal: str,
        datasource_key: str,
        session_id: str | None,
        trace_root: str | None,
        budget: dict[str, Any] | None,
    ) -> str:
        return await self._repo.insert_task(
            task_id=task_id,
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=session_id,
            goal=goal,
            datasource_key=datasource_key,
            budget_config=m.normalize_budget(budget),
            trace_root=trace_root,
        )

    async def get_detail(self, *, task_id: str, tenant_id: str, user_id: str) -> dict[str, Any]:
        task = await self._require(task_id=task_id, tenant_id=tenant_id, user_id=user_id)
        steps = await self._repo.get_steps(task_id=task_id)
        return {"task": task, "steps": steps}

    async def list_tasks(
        self, *, tenant_id: str, user_id: str, page: int, size: int
    ) -> tuple[list[dict[str, Any]], int]:
        return await self._repo.list_tasks(
            tenant_id=tenant_id, user_id=user_id, page=page, size=size
        )

    async def pause(
        self, *, task_id: str, tenant_id: str, user_id: str, version: int | None = None
    ) -> str:
        task = await self._require(task_id=task_id, tenant_id=tenant_id, user_id=user_id)
        return await self._move(
            task_id=task_id,
            tenant_id=tenant_id,
            user_id=user_id,
            task=task,
            to=m.T_PAUSED,
            expect_version=version,
        )

    async def resume(self, *, task_id: str, tenant_id: str, user_id: str) -> tuple[str, bool]:
        """显式恢复:存在待审批步骤一律拒绝(审批闸不可经 resume 旁路;
        步骤已决/过期的任务级 AWAITING 放行——审批过期后唯一的续跑口);
        PLANNING 拒绝(规划循环在驱动,二次派发即双执行者);RUNNING 孤儿
        步骤(暂停/中断时在途)回队 PENDING,恢复即重跑。
        返回(迁移后状态, 是否真实迁移):同态幂等返回 moved=False,
        调用方不得据此再派发编排(防双执行循环)。"""
        task = await self._require(task_id=task_id, tenant_id=tenant_id, user_id=user_id)
        if task["status"] == m.T_PLANNING:
            raise TaskConflict("任务规划中,不可恢复(规划循环驱动中)")
        steps = await self._repo.get_steps(task_id=task_id)
        if any(s["status"] == m.S_AWAITING_APPROVAL for s in steps):
            raise TaskConflict("存在待审批步骤,请先完成审批")
        if task["status"] == m.T_RUNNING:
            return m.T_RUNNING, False  # 幂等收敛:已运行,不回队不再派发
        to = await self._move(
            task_id=task_id, tenant_id=tenant_id, user_id=user_id, task=task, to=m.T_RUNNING
        )
        requeued = await self._repo.requeue_running_steps(task_id=task_id)
        if requeued:
            await self._repo.append_task_event(
                task_id=task_id,
                event={"at": _now(), "kind": "steps_requeued", "count": requeued},
            )
        return to, True

    async def cancel(
        self, *, task_id: str, tenant_id: str, user_id: str, reason: str = "用户取消"
    ) -> str:
        task = await self._require(task_id=task_id, tenant_id=tenant_id, user_id=user_id)
        to = await self._move(
            task_id=task_id,
            tenant_id=tenant_id,
            user_id=user_id,
            task=task,
            to=m.T_CANCELLED,
            fail_reason=reason,
        )
        # 账本收敛:非终态步骤一并关闭(终态任务无执行者,残留开放步骤属失真;
        # 迟到的步骤回写此后被步骤 CAS 诚实拒绝)
        closed = await self._repo.cancel_open_steps(task_id=task_id)
        if closed:
            await self._repo.append_task_event(
                task_id=task_id,
                event={"at": _now(), "kind": "steps_cancelled", "count": closed},
            )
        return to

    async def approve_step(
        self, *, task_id: str, step_id: str, tenant_id: str, user_id: str, approve: bool
    ) -> str:
        """步骤审批(归属校验):批准 → 步骤回 RUNNING 任务回 RUNNING;
        拒绝 → 步骤 SKIPPED(原因=审批拒绝),任务回 RUNNING 续跑其余步骤。
        审批期暂停(PAUSED)下决策仍可作出,任务随决策恢复运行。
        任务态预校验先于步骤写(并发取消/完成时拒绝,不留半程写)。"""
        task = await self._require(task_id=task_id, tenant_id=tenant_id, user_id=user_id)
        if task["status"] not in (m.T_AWAITING_APPROVAL, m.T_RUNNING, m.T_PAUSED):
            raise TaskConflict(f"任务状态 {task['status']} 不可审批")
        step = await self._repo.pending_confirm_step(task_id=task_id, step_id=step_id)
        if step is None:
            raise TaskNotFound  # 不存在/非待审状态 404 同形
        target_step = m.S_PENDING if approve else m.S_SKIPPED  # 批准→回队重跑
        if not await self._repo.transition_step(
            step_id=step_id,
            task_id=task_id,
            from_status=m.S_AWAITING_APPROVAL,
            to_status=target_step,
            fail_reason=None if approve else "审批拒绝,步骤跳过",
            clear_confirm=approve,
        ):
            raise TaskConflict("步骤状态已变更")
        return await self._move(
            task_id=task_id,
            tenant_id=tenant_id,
            user_id=user_id,
            task=task,
            to=m.T_RUNNING,
            event_kind="step_approved" if approve else "step_rejected",
        )

    # ── 编排回调(内部端点经 service 收口)──

    async def apply_plan(
        self, *, task_id: str, tenant_id: str, steps: list[m.StepPlan]
    ) -> list[dict[str, Any]]:
        """规划回写:PLANNING → RUNNING + 落步骤清单(事件记录)。
        行锁串行化并发规划;迁移先行且校验命中,失败即拒绝(不产生重复步骤账本)。
        RUNNING 且零步骤(规划期中断后 resume)允许重规划。"""
        if not await self._repo.lock_task(task_id=task_id):
            raise TaskNotFound
        task = await self._repo.get_task_system(task_id=task_id, tenant_id=tenant_id)
        if task is None:
            raise TaskNotFound
        if task["status"] == m.T_PLANNING:
            ok = await self._repo.transition_task_system(
                task_id=task_id,
                tenant_id=tenant_id,
                from_status=m.T_PLANNING,
                to_status=m.T_RUNNING,
                event={"at": _now(), "kind": "planned", "steps": len(steps)},
            )
            if not ok:
                raise TaskConflict("任务状态已变更(并发规划)")
            return await self._repo.insert_steps(task_id=task_id, plans=steps, start_ord=0)
        if task["status"] == m.T_RUNNING and not await self._repo.get_steps(task_id=task_id):
            # 规划期中断 → resume → 重规划(计划未落账,不存在重复步骤)
            await self._repo.append_task_event(
                task_id=task_id, event={"at": _now(), "kind": "replanned", "steps": len(steps)}
            )
            return await self._repo.insert_steps(task_id=task_id, plans=steps, start_ord=0)
        raise TaskConflict("任务不处于规划态")

    async def begin_step(self, *, task_id: str, tenant_id: str, step_id: str) -> dict[str, object]:
        """步骤启动(编排循环每步下发前):PENDING→RUNNING;需确认步骤→
        AWAITING_APPROVAL 且任务挂起;预算超限 → 任务 PAUSED、decision=pause;
        任务非 RUNNING(被暂停/取消/终态)→ pause/abort 决策,执行循环终止
        (终态不复活:用户取消/暂停后,在途编排不得继续执行剩余步骤)。"""
        task = await self._repo.get_task_system(task_id=task_id, tenant_id=tenant_id)
        if task is None:
            raise TaskNotFound
        status = str(task["status"])
        if status == m.T_PAUSED:
            return {"decision": "pause", "reason": "任务已暂停"}
        if status != m.T_RUNNING:
            return {
                "decision": "abort",
                "task_status": status,
                "reason": f"任务已 {status},不再执行剩余步骤",
            }
        used = cast("dict[str, object]", task.get("budget_used") or {})
        config = m.normalize_budget(cast("dict[str, Any] | None", task.get("budget_config")))
        reason = m.over_budget(used, config)
        if reason is not None:
            await self._repo.transition_task_system(
                task_id=task_id,
                tenant_id=tenant_id,
                from_status=m.T_RUNNING,
                to_status=m.T_PAUSED,
                fail_reason=reason,
                event={"at": _now(), "kind": "budget_paused", "reason": reason},
            )
            return {"decision": "pause", "reason": reason}
        steps = await self._repo.get_steps(task_id=task_id)
        # asyncpg 返回 UUID 对象:与 str 形参比较前归一(HANDOFF 教训)
        target = next((s for s in steps if str(s["id"]) == step_id), None)
        if target is None:
            raise TaskNotFound
        if target["status"] != m.S_PENDING:
            return {"decision": "skip", "reason": f"步骤状态 {target['status']} 不可执行"}
        if target.get("require_confirm"):
            if not await self._repo.transition_step(
                step_id=step_id,
                task_id=task_id,
                from_status=m.S_PENDING,
                to_status=m.S_AWAITING_APPROVAL,
            ):
                return {"decision": "skip", "reason": "步骤状态已变更"}
            moved = await self._repo.transition_task_system(
                task_id=task_id,
                tenant_id=tenant_id,
                from_status=m.T_RUNNING,
                to_status=m.T_AWAITING_APPROVAL,
                event={"at": _now(), "kind": "await_approval", "step": step_id},
            )
            if not moved:
                # 任务在步骤挂起瞬间被并发迁移(暂停/取消):回滚步骤态,
                # 按当前任务态给决策——不制造「步骤 AWAITING × 任务终态」死锁
                await self._repo.transition_step(
                    step_id=step_id,
                    task_id=task_id,
                    from_status=m.S_AWAITING_APPROVAL,
                    to_status=m.S_PENDING,
                )
                cur = await self._repo.get_task_system(task_id=task_id, tenant_id=tenant_id)
                st = str(cur["status"]) if cur else m.T_CANCELLED
                if st == m.T_PAUSED:
                    return {"decision": "pause", "reason": "任务已暂停"}
                return {
                    "decision": "abort",
                    "task_status": st,
                    "reason": f"任务已 {st},不再执行剩余步骤",
                }
            return {"decision": "await_approval"}
        if not await self._repo.transition_step(
            step_id=step_id,
            task_id=task_id,
            from_status=m.S_PENDING,
            to_status=m.S_RUNNING,
            mark_started=True,
        ):
            return {"decision": "skip", "reason": "步骤状态已变更"}
        await self._repo.append_task_event(
            task_id=task_id, event={"at": _now(), "kind": "step_started", "step": step_id}
        )
        return {"decision": "execute"}

    async def finish_step(self, *, task_id: str, tenant_id: str, result: m.StepResult) -> str:
        """步骤结果回写(证据驱动);预算累加(含墙钟分步计量)+ 事件;返回编排决策。
        终态任务(失败后)上的迟到回写:保留步骤证据,但不再追加步骤/累加预算
        (终态任务无执行者,账本封账),事件标注迟到。"""
        task = await self._repo.get_task_system(task_id=task_id, tenant_id=tenant_id)
        if task is None:
            raise TaskNotFound
        task_status = str(task["status"])
        evidence = result.evidence if result.status == m.S_DONE else {}
        reject = m.validate_result_status(result.status, evidence)
        if reject:
            return f"reject:{reject}"
        # 墙钟分步计量:起跑时刻 → 本次回写(spec 预算三约束的时长项)
        elapsed_ms = 0
        step = await self._repo.get_step(task_id=task_id, step_id=result.step_id)
        if step is not None and step.get("started_at") is not None:
            delta = datetime.now(UTC) - step["started_at"]
            elapsed_ms = max(0, int(delta.total_seconds() * 1000))
        if not await self._repo.transition_step(
            step_id=result.step_id,
            task_id=task_id,
            from_status=m.S_RUNNING,
            to_status=result.status,
            evidence=evidence or None,
            fail_reason=result.fail_reason,
            checkpoint=result.checkpoint or None,
        ):
            raise TaskConflict("步骤状态已变更")
        await self._repo.append_task_event(
            task_id=task_id,
            event={
                "at": _now(),
                "kind": "step_finished",
                "step": result.step_id,
                "to": result.status,
            },
        )
        if task_status in m.TASK_TERMINAL:
            await self._repo.append_task_event(
                task_id=task_id,
                event={
                    "at": _now(),
                    "kind": "late_step_result",
                    "step": result.step_id,
                    "to": result.status,
                    "task_status": task_status,
                },
            )
            return "continue"
        if result.appended_steps:
            await self._repo.insert_steps(
                task_id=task_id,
                plans=result.appended_steps,
                start_ord=len(await self._repo.get_steps(task_id=task_id)),
            )
        used = await self._repo.budget_accumulate(
            task_id=task_id,
            steps=1,
            tokens=result.tokens_used,
            elapsed_ms=elapsed_ms,
        )
        _ = used
        return "continue"

    async def finish(
        self, *, task_id: str, tenant_id: str, status: str, fail_reason: str | None
    ) -> str:
        """编排终态回写(白名单内收口):DONE 前置「全部步骤终态」校验
        (审批挂起/在途步骤未决时拒绝,防审批闸被 finish 旁路)。"""
        task = await self._repo.get_task_system(task_id=task_id, tenant_id=tenant_id)
        if task is None:
            raise TaskNotFound
        current = str(task["status"])
        try:
            m.transition(current, status)
        except m.InvalidTransition as e:
            await self._repo.append_task_event(
                task_id=task_id,
                event={"at": _now(), "kind": "invalid_transition", "detail": str(e)},
            )
            raise TaskInvalidTransition(f"非法状态迁移: {e}") from e
        if status == m.T_DONE:
            steps = await self._repo.get_steps(task_id=task_id)
            open_steps = [s for s in steps if s["status"] != m.S_DONE]
            if open_steps:
                # spec:全部 DONE 方可 DONE——SKIPPED/FAILED 也是未完成,
                # 不得借终态口径全称完成(收口表述须逐项如实)
                states = ",".join(sorted({str(s["status"]) for s in open_steps}))
                raise TaskConflict(f"存在未完成步骤({states}),不可 DONE")
        ok = await self._repo.transition_task_system(
            task_id=task_id,
            tenant_id=tenant_id,
            from_status=current,
            to_status=status,
            fail_reason=fail_reason,
            event={"at": _now(), "kind": "finished", "to": status},
        )
        _ = ok
        return status

    async def mark_interrupted_stale(self) -> int:
        """启动清扫:RUNNING/PLANNING → INTERRUPTED(不谎称完成)
        + 挂起超 TTL 的审批步骤 SKIPPED(spec 审批过期语义)。"""
        marked = await self._repo.mark_interrupted_stale()
        await self._repo.expire_stale_approvals()
        return marked

    # ── 内部 ──

    async def _require(self, *, task_id: str, tenant_id: str, user_id: str) -> dict[str, Any]:
        task = await self._repo.get_task(task_id=task_id, tenant_id=tenant_id, user_id=user_id)
        if task is None:
            raise TaskNotFound
        return task

    async def _move(
        self,
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
            await self._repo.transition_task(
                task_id=task_id,
                tenant_id=tenant_id,
                user_id=user_id,
                from_status=current,
                to_status=current,
                event={"at": _now(), "kind": "invalid_transition", "detail": str(e)},
            )
            raise TaskInvalidTransition(f"非法状态迁移: {e}") from e
        row = await self._repo.transition_task(
            task_id=task_id,
            tenant_id=tenant_id,
            user_id=user_id,
            from_status=current,
            to_status=to,
            fail_reason=fail_reason,
            event={"at": _now(), "kind": event_kind, "to": to},
            expect_version=expect_version,
        )
        if row is None:
            raise TaskConflict("任务状态/版本已变更,请刷新重试")
        return to
