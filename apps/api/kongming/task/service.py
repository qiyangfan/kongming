"""task 用例入口:按用例簇组合生命周期、编排回调、图级确认与协作拓扑子服务。

原则:状态迁移全部经白名单 + CAS(乐观锁);非法迁移拒绝并留事件审计;
审批归属=任务归属人;预算超限 PAUSED 诚实收口。
子服务各自持有同一仓储与端口;组合根与调用方仍只依赖本类。
"""

from typing import Any

from kongming.task import models as m
from kongming.task.collab_topology import TaskTopologyService
from kongming.task.errors import (
    TaskConflict,
    TaskInvalidTransition,
    TaskNotFound,
    TopologyBotNotFound,
)
from kongming.task.ports import (
    CollabBotResolver,
    GraphConfirmRepo,
    TaskCollabRepo,
)
from kongming.task.service_graph import TaskGraphService
from kongming.task.service_lifecycle import TaskLifecycleService
from kongming.task.service_run import TaskRunService

__all__ = [
    "TaskConflict",
    "TaskInvalidTransition",
    "TaskNotFound",
    "TaskService",
    "TopologyBotNotFound",
]


class TaskService:
    def __init__(
        self,
        repo: TaskCollabRepo,
        *,
        bot_resolver: CollabBotResolver | None = None,
        graph_repo: GraphConfirmRepo | None = None,
    ) -> None:
        self._repo = repo
        self._lifecycle = TaskLifecycleService(repo, graph_repo=graph_repo)
        self._topology = TaskTopologyService(repo, bot_resolver=bot_resolver)
        self._run = TaskRunService(repo, topology=self._topology, graph_repo=graph_repo)
        # 图内确认能力未注入=不可用(抛 TaskConflict,与既有「能力未启用」口径一致)
        self._graph = TaskGraphService(repo, graph_repo=graph_repo) if graph_repo else None

    # ── 生命周期(用户面)──

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
        return await self._lifecycle.create(
            task_id=task_id,
            tenant_id=tenant_id,
            user_id=user_id,
            goal=goal,
            datasource_key=datasource_key,
            session_id=session_id,
            trace_root=trace_root,
            budget=budget,
            budget_caps=budget_caps,
        )

    async def get_detail(self, *, task_id: str, tenant_id: str, user_id: str) -> dict[str, Any]:
        return await self._lifecycle.get_detail(
            task_id=task_id, tenant_id=tenant_id, user_id=user_id
        )

    async def list_tasks(
        self, *, tenant_id: str, user_id: str, page: int, size: int, status: str | None = None
    ) -> tuple[list[dict[str, Any]], int]:
        return await self._lifecycle.list_tasks(
            tenant_id=tenant_id, user_id=user_id, page=page, size=size, status=status
        )

    async def pause(
        self, *, task_id: str, tenant_id: str, user_id: str, version: int | None = None
    ) -> str:
        return await self._lifecycle.pause(
            task_id=task_id, tenant_id=tenant_id, user_id=user_id, version=version
        )

    async def resume(self, *, task_id: str, tenant_id: str, user_id: str) -> tuple[str, bool]:
        return await self._lifecycle.resume(task_id=task_id, tenant_id=tenant_id, user_id=user_id)

    async def cancel(
        self, *, task_id: str, tenant_id: str, user_id: str, reason: str = "用户取消"
    ) -> str:
        return await self._lifecycle.cancel(
            task_id=task_id, tenant_id=tenant_id, user_id=user_id, reason=reason
        )

    async def approve_step(
        self, *, task_id: str, step_id: str, tenant_id: str, user_id: str, approve: bool
    ) -> str:
        return await self._lifecycle.approve_step(
            task_id=task_id, step_id=step_id, tenant_id=tenant_id, user_id=user_id, approve=approve
        )

    async def mark_interrupted_stale(self) -> int:
        return await self._lifecycle.mark_interrupted_stale()

    # ── 编排回调(内部端点)──

    async def apply_plan(
        self, *, task_id: str, tenant_id: str, steps: list[m.StepPlan]
    ) -> list[dict[str, Any]]:
        return await self._run.apply_plan(task_id=task_id, tenant_id=tenant_id, steps=steps)

    async def begin_step(
        self, *, task_id: str, tenant_id: str, step_id: str, epoch: int | None = None
    ) -> dict[str, object]:
        return await self._run.begin_step(
            task_id=task_id, tenant_id=tenant_id, step_id=step_id, epoch=epoch
        )

    async def finish_step(
        self, *, task_id: str, tenant_id: str, result: m.StepResult, epoch: int | None = None
    ) -> str:
        return await self._run.finish_step(
            task_id=task_id, tenant_id=tenant_id, result=result, epoch=epoch
        )

    async def finish(
        self,
        *,
        task_id: str,
        tenant_id: str,
        status: str,
        fail_reason: str | None,
        epoch: int | None = None,
    ) -> str:
        """编排终态回写(白名单内收口):同态幂等;DONE 前置全部步骤终态校验;
        派发代际失配拒绝(终态归属在位派发)。"""
        from kongming.task.service_common import now_iso

        task = await self._repo.get_task_system(task_id=task_id, tenant_id=tenant_id)
        if task is None:
            raise TaskNotFound
        if epoch is not None and epoch != int(task.get("dispatch_epoch") or 0):
            raise TaskConflict("派发代际失配,终态收口由在位派发负责")
        current = str(task["status"])
        if current == status:
            return current
        try:
            m.transition(current, status)
        except m.InvalidTransition as e:
            await self._repo.append_task_event(
                task_id=task_id,
                event={"at": now_iso(), "kind": "invalid_transition", "detail": str(e)},
            )
            raise TaskInvalidTransition(f"非法状态迁移: {e}") from e
        if status == m.T_DONE:
            steps = await self._repo.get_steps(task_id=task_id)
            open_steps = [s for s in steps if s["status"] != m.S_DONE]
            if open_steps:
                states = ",".join(sorted({str(s["status"]) for s in open_steps}))
                raise TaskConflict(f"存在未完成步骤({states}),不可 DONE")
        ok = await self._repo.transition_task_system(
            task_id=task_id,
            tenant_id=tenant_id,
            from_status=current,
            to_status=status,
            fail_reason=fail_reason,
            event={"at": now_iso(), "kind": "finished", "to": status},
            expect_epoch=epoch,
        )
        if not ok:
            raise TaskConflict("任务状态/派发代数已推进,终态收口未落地")
        if status in m.TASK_TERMINAL:
            await self._lifecycle.purge_graph_threads(task_id=task_id, tenant_id=tenant_id)
        return status

    # ── 图级检查点与图内挂起 ──

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
        if self._graph is None:
            raise TaskConflict("图内确认能力未启用")
        return await self._graph.suspend_on_graph_confirm(
            task_id=task_id,
            tenant_id=tenant_id,
            step_id=step_id,
            epoch=epoch,
            confirm_id=confirm_id,
            tool_name=tool_name,
            summary=summary,
            thread_id=thread_id,
        )

    async def decide_graph_confirm(
        self,
        *,
        task_id: str,
        tenant_id: str,
        user_id: str,
        confirm_id: str,
        approve: bool,
    ) -> dict[str, object]:
        if self._graph is None:
            raise TaskConflict("图内确认能力未启用")
        return await self._graph.decide_graph_confirm(
            task_id=task_id,
            tenant_id=tenant_id,
            user_id=user_id,
            confirm_id=confirm_id,
            approve=approve,
        )

    # ── 协作拓扑配置面与协作账本(查询挂 task:* 权限体系)──

    async def set_step_topology(
        self,
        *,
        task_id: str,
        step_id: str,
        tenant_id: str,
        user_id: str,
        raw_topology: dict[str, Any],
        max_workers_cap: int,
    ) -> dict[str, object]:
        return await self._topology.set_step_topology(
            task_id=task_id,
            step_id=step_id,
            tenant_id=tenant_id,
            user_id=user_id,
            raw_topology=raw_topology,
            max_workers_cap=max_workers_cap,
        )

    async def list_collab_runs(
        self, *, task_id: str, tenant_id: str, user_id: str
    ) -> list[dict[str, Any]]:
        """协作运行清单(任务维度,含 worker 行;归属走任务口径)。"""
        await self._lifecycle.require_task(task_id=task_id, tenant_id=tenant_id, user_id=user_id)
        return await self._repo.list_collab_runs(task_id=task_id)
