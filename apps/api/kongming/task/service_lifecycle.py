"""task 生命周期用例:创建、详情、列表、暂停/恢复/取消、步骤审批与启动清扫。

用户面操作按任务归属人过滤;状态迁移全部经白名单 + CAS(非法迁移留事件审计)。
"""

from typing import Any

from kongming.task import models as m
from kongming.task.errors import TaskConflict, TaskNotFound
from kongming.task.ports import GraphConfirmRepo, TaskCollabRepo
from kongming.task.service_common import move_task, now_iso


class TaskLifecycleService:
    def __init__(self, repo: TaskCollabRepo, *, graph_repo: GraphConfirmRepo | None = None) -> None:
        self._repo = repo
        # 图级检查点/图内确认账本;缺席=能力不可用(纯函数单测场景),既有用例不受影响
        self._graph = graph_repo

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
        budget_caps: dict[str, int] | None = None,
    ) -> str:
        normalized = m.normalize_budget(budget)
        if budget_caps is not None:
            # 部署级上限钳制(spec「任务级配置 + 部署级上限」)
            normalized = m.clamp_budget(normalized, budget_caps)
        return await self._repo.insert_task(
            task_id=task_id,
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=session_id,
            goal=goal,
            datasource_key=datasource_key,
            budget_config=normalized,
            trace_root=trace_root,
        )

    async def get_detail(self, *, task_id: str, tenant_id: str, user_id: str) -> dict[str, Any]:
        task = await self.require_task(task_id=task_id, tenant_id=tenant_id, user_id=user_id)
        steps = await self._repo.get_steps(task_id=task_id)
        out: dict[str, Any] = {"task": task, "steps": steps, "graph_confirms": []}
        if self._graph is not None:
            # 图内确认行(挂起卡片数据源);uuid 归一为 str(异步驱动返回对象)
            out["graph_confirms"] = [
                {
                    "confirm_id": str(r["confirm_id"]),
                    "step_id": str(r["step_id"]),
                    "tool_name": str(r["tool_name"] or ""),
                    "summary": str(r["summary"] or ""),
                    "status": str(r["status"]),
                    "expires_at": str(r["expires_at"] or ""),
                    "created_at": str(r["created_at"] or ""),
                }
                for r in await self._graph.pending_confirms_for_task(task_id=task_id)
            ]
        return out

    async def list_tasks(
        self, *, tenant_id: str, user_id: str, page: int, size: int, status: str | None = None
    ) -> tuple[list[dict[str, Any]], int]:
        return await self._repo.list_tasks(
            tenant_id=tenant_id, user_id=user_id, page=page, size=size, status=status
        )

    async def pause(
        self, *, task_id: str, tenant_id: str, user_id: str, version: int | None = None
    ) -> str:
        task = await self.require_task(task_id=task_id, tenant_id=tenant_id, user_id=user_id)
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
        task = await self.require_task(task_id=task_id, tenant_id=tenant_id, user_id=user_id)
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
                event={"at": now_iso(), "kind": "steps_requeued", "count": requeued},
            )
        return to, True

    async def cancel(
        self, *, task_id: str, tenant_id: str, user_id: str, reason: str = "用户取消"
    ) -> str:
        task = await self.require_task(task_id=task_id, tenant_id=tenant_id, user_id=user_id)
        to = await self._move(
            task_id=task_id,
            tenant_id=tenant_id,
            user_id=user_id,
            task=task,
            to=m.T_CANCELLED,
            fail_reason=reason,
        )
        # 账本收敛:非终态步骤一并关闭(终态任务无执行者,残留开放步骤属失真;
        # 迟到的步骤回写此后被步骤 CAS 诚实拒绝);在途协作账一并收敛
        closed = await self._repo.cancel_open_steps(task_id=task_id)
        collab_cancelled = await self._repo.cancel_open_collab(task_id=task_id)
        await self.purge_graph_threads(task_id=task_id, tenant_id=tenant_id)
        if closed or collab_cancelled:
            await self._repo.append_task_event(
                task_id=task_id,
                event={
                    "at": now_iso(),
                    "kind": "steps_cancelled",
                    "count": closed,
                    "collab_runs_cancelled": collab_cancelled,
                },
            )
        return to

    async def approve_step(
        self, *, task_id: str, step_id: str, tenant_id: str, user_id: str, approve: bool
    ) -> str:
        """步骤审批(归属校验):批准 → 步骤回 RUNNING 任务回 RUNNING;
        拒绝 → 步骤 SKIPPED(原因=审批拒绝),任务回 RUNNING 续跑其余步骤。
        审批期暂停(PAUSED)下决策仍可作出,任务随决策恢复运行。
        任务态预校验先于步骤写(并发取消/完成时拒绝,不留半程写)。"""
        task = await self.require_task(task_id=task_id, tenant_id=tenant_id, user_id=user_id)
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

    async def mark_interrupted_stale(self) -> int:
        """启动清扫:RUNNING/PLANNING → INTERRUPTED(不谎称完成)
        + 挂起超 TTL 的审批步骤 SKIPPED(spec 审批过期语义)
        + 图内确认过期(同语义:步骤跳过,确认行置 expired 留痕)。"""
        marked = await self._repo.mark_interrupted_stale()
        await self._repo.expire_stale_approvals()
        if self._graph is not None:
            for r in await self._graph.expire_stale_graph_confirms():
                await self._graph.task_event(
                    task_id=str(r["task_id"]),
                    event={
                        "at": now_iso(),
                        "kind": "graph_confirm_expired",
                        "confirm": str(r["confirm_id"]),
                    },
                )
        return marked

    async def purge_graph_threads(self, *, task_id: str, tenant_id: str) -> None:
        """任务终态钩子:该任务全部图级快照清理(spec:快照随任务保留策略处置,
        SHALL NOT 无界增长);恢复价值随终态消失,事件时间线已承载过程审计。"""
        if self._graph is None:
            return
        for s in await self._repo.get_steps(task_id=task_id):
            await self._graph.delete_thread(
                tenant_id=tenant_id,
                thread_id=f"task:{task_id}:step:{s['id']}",
            )

    # ── 内部 ──

    async def require_task(self, *, task_id: str, tenant_id: str, user_id: str) -> dict[str, Any]:
        """读取归属本人的任务,不存在/越权一律 404 同形。"""
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
        return await move_task(
            self._repo,
            task_id=task_id,
            tenant_id=tenant_id,
            user_id=user_id,
            task=task,
            to=to,
            fail_reason=fail_reason,
            expect_version=expect_version,
            event_kind=event_kind,
        )
