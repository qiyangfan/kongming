"""task 端口(090;宪法 Article II:持久化经接口反转)。"""

from typing import Any, Protocol

from kongming.task.models import StepPlan


class TaskRepo(Protocol):
    async def insert_task(
        self,
        *,
        task_id: str,
        tenant_id: str,
        user_id: str,
        session_id: str | None,
        goal: str,
        datasource_key: str,
        budget_config: dict[str, int],
        trace_root: str | None,
    ) -> str: ...

    async def get_task(
        self, *, task_id: str, tenant_id: str, user_id: str
    ) -> dict[str, Any] | None: ...

    async def list_tasks(
        self, *, tenant_id: str, user_id: str, page: int, size: int, status: str | None = None
    ) -> tuple[list[dict[str, Any]], int]: ...

    async def get_task_system(self, *, task_id: str, tenant_id: str) -> dict[str, Any] | None: ...

    async def transition_task_system(
        self,
        *,
        task_id: str,
        tenant_id: str,
        from_status: str,
        to_status: str,
        fail_reason: str | None = None,
        event: dict[str, object] | None = None,
        expect_epoch: int | None = None,
    ) -> bool: ...

    async def transition_task(
        self,
        *,
        task_id: str,
        tenant_id: str,
        user_id: str,
        from_status: str,
        to_status: str,
        fail_reason: str | None = None,
        budget_used: dict[str, object] | None = None,
        event: dict[str, object] | None = None,
        expect_version: int | None = None,
    ) -> dict[str, Any] | None: ...

    async def insert_steps(
        self, *, task_id: str, plans: list[StepPlan], start_ord: int
    ) -> list[dict[str, Any]]: ...

    async def get_steps(self, *, task_id: str) -> list[dict[str, Any]]: ...

    async def transition_step(
        self,
        *,
        step_id: str,
        task_id: str,
        from_status: str,
        to_status: str,
        evidence: dict[str, object] | None = None,
        fail_reason: str | None = None,
        checkpoint: dict[str, object] | None = None,
        clear_confirm: bool = False,
        mark_started: bool = False,
        expect_epoch: int | None = None,
    ) -> bool: ...

    async def first_executable_step(self, *, task_id: str) -> dict[str, Any] | None: ...

    async def get_step(self, *, task_id: str, step_id: str) -> dict[str, Any] | None: ...

    async def requeue_running_steps(self, *, task_id: str) -> int: ...

    async def cancel_open_steps(self, *, task_id: str) -> int: ...

    async def lock_task(self, *, task_id: str) -> bool: ...

    async def append_task_event(self, *, task_id: str, event: dict[str, object]) -> None: ...

    async def expire_stale_approvals(self) -> int: ...

    async def budget_accumulate(
        self, *, task_id: str, steps: int = 0, tokens: int = 0, elapsed_ms: int = 0
    ) -> dict[str, Any] | None: ...

    async def mark_interrupted_stale(self) -> int: ...

    async def bump_dispatch_epoch(self, *, task_id: str) -> int | None: ...

    async def pending_confirm_step(
        self, *, task_id: str, step_id: str
    ) -> dict[str, Any] | None: ...

    # ── 协作拓扑与协作账本(091)──

    async def update_step_topology(
        self, *, task_id: str, step_id: str, topology: dict[str, object]
    ) -> bool: ...

    async def insert_collab_run(
        self,
        *,
        run_id: str,
        task_id: str,
        step_id: str,
        tenant_id: str,
        topology: dict[str, object],
        depth: int,
    ) -> None: ...

    async def supersede_open_collab_runs(self, *, task_id: str, step_id: str) -> int: ...

    async def cancel_open_collab(self, *, task_id: str) -> int: ...

    async def get_collab_run_system(
        self, *, run_id: str, task_id: str, tenant_id: str
    ) -> dict[str, Any] | None: ...

    async def insert_collab_worker(
        self, *, run_id: str, worker_no: int, subgoal: str, bot_key: str | None, trace_id: str
    ) -> bool: ...

    async def skip_open_collab_workers(self, *, run_id: str) -> int: ...

    async def finish_collab_worker(
        self,
        *,
        run_id: str,
        worker_no: int,
        status: str,
        conclusion: dict[str, object],
        evidence: dict[str, object],
        usage: dict[str, object],
        trace_id: str,
    ) -> bool: ...

    async def finish_collab_run(self, *, run_id: str, status: str, verdict: str | None) -> bool: ...

    async def max_collab_worker_no(self, *, run_id: str) -> int: ...

    async def collab_worker_exists(self, *, run_id: str, worker_no: int) -> bool: ...

    async def insert_collab_turn(
        self,
        *,
        run_id: str,
        round_no: int,
        worker_no: int,
        kind: str,
        content: dict[str, object],
        evidence: dict[str, object],
        usage: dict[str, object],
        trace_id: str,
    ) -> bool: ...

    async def list_collab_runs(self, *, task_id: str) -> list[dict[str, Any]]: ...


class CollabBotResolver(Protocol):
    """协作 Bot 运行时解析(091;组合根注入,task 用例零 bot 域依赖)。
    返回 None = 不存在/停用/创建者不可用(同形拒绝)。"""

    async def resolve(self, *, tenant_id: str, bot_key: str) -> dict[str, object] | None: ...


class TaskRunGateway(Protocol):
    """编排回调网关(agent 面 /internal/task-run);不可达返回 None。"""

    async def run_task(self, payload: dict[str, object]) -> dict[str, object] | None: ...
