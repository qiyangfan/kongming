"""task 图级检查点用例:图内挂起上账与图内确认处置。

挂起沿用既有任务状态白名单边(RUNNING ⇄ AWAITING_APPROVAL),幂等语义:
同 confirm_id 重复挂起同态返回,已决确认不重复迁移。
"""

from typing import Any

from kongming.task import models as m
from kongming.task.errors import TaskConflict, TaskNotFound
from kongming.task.ports import GraphConfirmRepo, TaskRepo
from kongming.task.service_common import move_task, now_iso


class TaskGraphService:
    def __init__(self, repo: TaskRepo, *, graph_repo: GraphConfirmRepo) -> None:
        self._repo = repo
        self._graph = graph_repo

    async def suspend_on_graph_confirm(
        self,
        *,
        task_id: str,
        tenant_id: str,
        step_id: str,
        epoch: int | None,
        confirm_id: str,
        tool_name: str,
        summary: str,
        thread_id: str,
    ) -> dict[str, object]:
        """图内挂起上账(编排回调):任务循环内高危动作到达确认点 → 建/复用
        确认行,步骤 RUNNING→AWAITING_APPROVAL,任务 RUNNING→AWAITING_APPROVAL
        (既有白名单边,状态机不扩)。幂等:同 confirm_id 已存在 → 同态返回
        现状态(编排重入/重启再挂不重复上账);代际失配 → stale_epoch。"""
        existing = await self._graph.get_graph_confirm(tenant_id=tenant_id, confirm_id=confirm_id)
        if existing is not None:
            return {
                "status": str(existing["status"]),
                "confirm_id": confirm_id,
                "reused": True,
            }
        task = await self._repo.get_task_system(task_id=task_id, tenant_id=tenant_id)
        if task is None:
            raise TaskNotFound
        if epoch is not None and epoch != int(task.get("dispatch_epoch") or 0):
            return {"decision": "stale_epoch"}
        if str(task["status"]) != m.T_RUNNING:
            raise TaskConflict(f"任务状态 {task['status']} 不可挂起")
        from kongming.config import get_settings

        await self._graph.insert_graph_confirm(
            tenant_id=tenant_id,
            task_id=task_id,
            step_id=step_id,
            confirm_id=confirm_id,
            tool_name=tool_name,
            summary=summary[:500],
            thread_id=thread_id,
            ttl_minutes=get_settings().graph_confirm_ttl_minutes,
        )
        ok_step = await self._repo.transition_step(
            step_id=step_id,
            task_id=task_id,
            from_status=m.S_RUNNING,
            to_status=m.S_AWAITING_APPROVAL,
            expect_epoch=epoch,
        )
        if not ok_step:
            raise TaskConflict("步骤状态已变更,挂起未落地")
        moved = await self._repo.transition_task_system(
            task_id=task_id,
            tenant_id=tenant_id,
            from_status=m.T_RUNNING,
            to_status=m.T_AWAITING_APPROVAL,
            event={
                "at": now_iso(),
                "kind": "graph_suspended",
                "step": step_id,
                "confirm": confirm_id,
                "tool": tool_name,
                "summary": summary[:300],
            },
            expect_epoch=epoch,
        )
        if not moved:
            # 挂起瞬间任务被并发迁移(暂停/取消):回滚步骤态保持一致
            # (确认行随事务回滚,不留游离挂起账)
            await self._repo.transition_step(
                step_id=step_id,
                task_id=task_id,
                from_status=m.S_AWAITING_APPROVAL,
                to_status=m.S_RUNNING,
            )
            raise TaskConflict("任务状态已变更,挂起未落地")
        return {"status": "pending", "confirm_id": confirm_id}

    async def decide_graph_confirm(
        self,
        *,
        task_id: str,
        tenant_id: str,
        user_id: str,
        confirm_id: str,
        approve: bool,
    ) -> dict[str, object]:
        """图内确认处置(归属人-only,审批门同源纪律):确认行 CAS 单向迁移;
        步骤 AWAITING_APPROVAL→PENDING 回队(决策经 begin_step 恢复提示下发,
        批准→动作执行,拒绝→动作跳过观察喂回);任务回 RUNNING 重派。
        幂等:已决确认同态返回,不重复迁移不重复派发信号。"""
        task = await self._require(task_id=task_id, tenant_id=tenant_id, user_id=user_id)
        if task["status"] not in (m.T_AWAITING_APPROVAL, m.T_RUNNING, m.T_PAUSED):
            raise TaskConflict(f"任务状态 {task['status']} 不可处置确认")
        row = await self._graph.get_graph_confirm(tenant_id=tenant_id, confirm_id=confirm_id)
        # 存在性与任务归属同形 404(不暴露是哪种)
        if row is None or str(row["task_id"]) != task_id:
            raise TaskNotFound
        if str(row["status"]) != "pending":
            # 已决/过期:幂等收敛(与 approve_step 的 pending_confirm_step 同口径)
            return {
                "confirm_id": confirm_id,
                "status": str(row["status"]),
                "step_id": str(row["step_id"]),
                "already_decided": True,
            }
        to_status = "approved" if approve else "denied"
        hit = await self._graph.decide_graph_confirm(
            tenant_id=tenant_id,
            confirm_id=confirm_id,
            to_status=to_status,
            decided_by=user_id,
        )
        if hit is None:
            raise TaskConflict("确认状态已变更")
        step_id = str(hit["step_id"])
        if not await self._repo.transition_step(
            step_id=step_id,
            task_id=task_id,
            from_status=m.S_AWAITING_APPROVAL,
            to_status=m.S_PENDING,
        ):
            raise TaskConflict("步骤状态已变更,决策未落地")
        await self._move_to_running(
            task=task, task_id=task_id, tenant_id=tenant_id, user_id=user_id, approve=approve
        )
        return {"confirm_id": confirm_id, "status": to_status, "step_id": step_id}

    async def _move_to_running(
        self, *, task: dict[str, Any], task_id: str, tenant_id: str, user_id: str, approve: bool
    ) -> None:
        """确认处置后任务回 RUNNING(事件按批准/拒绝分型)。"""
        await move_task(
            self._repo,
            task_id=task_id,
            tenant_id=tenant_id,
            user_id=user_id,
            task=task,
            to=m.T_RUNNING,
            event_kind="graph_confirm_approved" if approve else "graph_confirm_rejected",
        )

    async def _require(self, *, task_id: str, tenant_id: str, user_id: str) -> dict[str, Any]:
        task = await self._repo.get_task(task_id=task_id, tenant_id=tenant_id, user_id=user_id)
        if task is None:
            raise TaskNotFound
        return task
