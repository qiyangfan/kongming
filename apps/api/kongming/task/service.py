"""task 用例(090):创建/迁移/审批/预算/事件(用例层;编排回调经内部端点)。

原则:状态迁移全部经白名单 + CAS(乐观锁);非法迁移拒绝并留事件审计;
审批归属=任务归属人;预算超限 PAUSED 诚实收口。
"""

import logging
from datetime import UTC, datetime
from typing import Any, cast

from kongming.db import uuid7
from kongming.task import models as m
from kongming.task.ports import CollabBotResolver, TaskRepo

log = logging.getLogger(__name__)

# 协作 worker 序号硬顶(失控兜底:worker 派发 + 分歧验证 + critic 修订重派
# 全计入;agent 面预算先于此绑死,此处只防畸形客户端灌行)
COLLAB_WORKER_NO_HARD_CAP = 12


class TaskNotFound(Exception):
    """不存在/越权(404 同形)。"""


class TaskConflict(Exception):
    """状态/版本冲突(409)。"""


class TaskInvalidTransition(TaskConflict):
    """非法状态迁移(白名单拒绝;审计事件已落,端点需先 commit 再 409)。"""


class TopologyBotNotFound(Exception):
    """协作拓扑声明的 Bot 不存在/停用/创建者不可用(422 同族,非归属 404)。"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


class TaskService:
    def __init__(self, repo: TaskRepo, *, bot_resolver: CollabBotResolver | None = None) -> None:
        self._repo = repo
        self._bot_resolver = bot_resolver

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
            # 部署级上限钳制(090 spec「任务级配置 + 部署级上限」)
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
        task = await self._require(task_id=task_id, tenant_id=tenant_id, user_id=user_id)
        steps = await self._repo.get_steps(task_id=task_id)
        return {"task": task, "steps": steps}

    async def list_tasks(
        self, *, tenant_id: str, user_id: str, page: int, size: int, status: str | None = None
    ) -> tuple[list[dict[str, Any]], int]:
        return await self._repo.list_tasks(
            tenant_id=tenant_id, user_id=user_id, page=page, size=size, status=status
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
        # 迟到的步骤回写此后被步骤 CAS 诚实拒绝);在途协作账一并收敛
        closed = await self._repo.cancel_open_steps(task_id=task_id)
        collab_cancelled = await self._repo.cancel_open_collab(task_id=task_id)
        if closed or collab_cancelled:
            await self._repo.append_task_event(
                task_id=task_id,
                event={
                    "at": _now(),
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

    async def begin_step(
        self, *, task_id: str, tenant_id: str, step_id: str, epoch: int | None = None
    ) -> dict[str, object]:
        """步骤启动(编排循环每步下发前):PENDING→RUNNING;需确认步骤→
        AWAITING_APPROVAL 且任务挂起;预算超限 → 任务 PAUSED、decision=pause;
        任务非 RUNNING(被暂停/取消/终态)→ pause/abort 决策,执行循环终止
        (终态不复活:用户取消/暂停后,在途编排不得继续执行剩余步骤);
        派发代际失配(epoch ≠ 任务行当前代数)→ abort 决策——旧循环立即退出,
        消除「pause 不杀在途循环 + resume 立即派发」的双执行者窗口。"""
        task = await self._repo.get_task_system(task_id=task_id, tenant_id=tenant_id)
        if task is None:
            raise TaskNotFound
        if epoch is not None and epoch != int(task.get("dispatch_epoch") or 0):
            return {
                "decision": "abort",
                "task_status": str(task["status"]),
                "reason": "派发代际失配,新派发已接管(旧编排循环退出)",
            }
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
            paused = await self._repo.transition_task_system(
                task_id=task_id,
                tenant_id=tenant_id,
                from_status=m.T_RUNNING,
                to_status=m.T_PAUSED,
                fail_reason=reason,
                event={"at": _now(), "kind": "budget_paused", "reason": reason},
                expect_epoch=epoch,
            )
            if not paused:
                # 谓词未命中(状态漂移或代际失配):不得宣称暂停生效,
                # 按当前任务态给决策——旧循环在此退出
                cur = await self._repo.get_task_system(task_id=task_id, tenant_id=tenant_id)
                st = str(cur["status"]) if cur else m.T_CANCELLED
                if st == m.T_RUNNING:
                    return {"decision": "pause", "reason": reason}
                return {
                    "decision": "abort",
                    "task_status": st,
                    "reason": f"任务已 {st},不再执行剩余步骤",
                }
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
                expect_epoch=epoch,
            ):
                return await self._step_cas_miss_decision(
                    task_id=task_id, tenant_id=tenant_id, epoch=epoch
                )
            moved = await self._repo.transition_task_system(
                task_id=task_id,
                tenant_id=tenant_id,
                from_status=m.T_RUNNING,
                to_status=m.T_AWAITING_APPROVAL,
                event={"at": _now(), "kind": "await_approval", "step": step_id},
                expect_epoch=epoch,
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
            expect_epoch=epoch,
        ):
            return await self._step_cas_miss_decision(
                task_id=task_id, tenant_id=tenant_id, epoch=epoch
            )
        await self._repo.append_task_event(
            task_id=task_id, event={"at": _now(), "kind": "step_started", "step": step_id}
        )
        if m.has_collab_topology(target):
            collab = await self._collab_decision(tenant_id=tenant_id, step=target)
            return {"decision": "execute", "collab": collab}
        return {"decision": "execute"}

    async def _collab_decision(self, *, tenant_id: str, step: dict[str, Any]) -> dict[str, object]:
        """协作步骤的执行决策载荷(091):拓扑(以库内归一值为准,再按当前
        部署上限复钳)+ Bot 运行时解析(人格/工具子集;解析失败如实降级为
        无绑定,不虚报人格注入)。"""
        from kongming.config import get_settings

        topo = m.normalize_collab_topology(
            cast("dict[str, Any] | None", step.get("collab_topology")),
            max_workers_cap=get_settings().collab_max_workers,
        )
        bot: dict[str, object] | None = None
        bot_key = topo.get("bot_key")
        if bot_key and self._bot_resolver is not None:
            bot = await self._bot_resolver.resolve(tenant_id=tenant_id, bot_key=str(bot_key))
        return {**topo, "bot": bot}

    # ── 协作拓扑配置面与协作账本(091;查询挂 task:* 权限体系)──

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
        """步骤声明协作拓扑(配置面):归属校验 → 步骤 PENDING 闸 → Bot 存在性
        校验(绑定口径同 Bot 运行时)→ 归一钳制落库。返回归一后的拓扑。"""
        await self._require(task_id=task_id, tenant_id=tenant_id, user_id=user_id)
        step = await self._repo.get_step(task_id=task_id, step_id=step_id)
        if step is None:
            raise TaskNotFound
        if str(step["status"]) != m.S_PENDING:
            raise TaskConflict("仅 PENDING 步骤可声明协作拓扑")
        topo = m.normalize_collab_topology(raw_topology, max_workers_cap=max_workers_cap)
        bot_key = topo.get("bot_key")
        if bot_key:
            if self._bot_resolver is None:
                raise TaskConflict("协作 Bot 绑定未启用")
            if await self._bot_resolver.resolve(tenant_id=tenant_id, bot_key=str(bot_key)) is None:
                raise TopologyBotNotFound(str(bot_key))
        if not await self._repo.update_step_topology(
            task_id=task_id, step_id=step_id, topology=topo
        ):
            raise TaskConflict("步骤状态已变更,不可声明协作拓扑")
        await self._repo.append_task_event(
            task_id=task_id,
            event={"at": _now(), "kind": "collab_topology_set", "step": step_id, **topo},
        )
        return topo

    async def list_collab_runs(
        self, *, task_id: str, tenant_id: str, user_id: str
    ) -> list[dict[str, Any]]:
        """协作运行清单(任务维度,含 worker 行;归属走任务口径)。"""
        await self._require(task_id=task_id, tenant_id=tenant_id, user_id=user_id)
        return await self._repo.list_collab_runs(task_id=task_id)

    async def collab_start_run(
        self, *, task_id: str, step_id: str, tenant_id: str, depth: int
    ) -> dict[str, object]:
        """协作运行开账(内部回调;拓扑以步骤声明为准,不采信执行面载荷):
        步骤须 RUNNING(宿主语义:协作只由步骤执行发起——无游离协作入口);
        嵌套深度按部署上限拒绝;同 (task, step) 在途运行收敛 CANCELLED 后开新账。
        返回 {run_id, topology}。"""
        if depth < 1 or depth > m.DEFAULT_MAX_DEPTH:
            raise TaskConflict(f"协作深度 {depth} 超部署上限 {m.DEFAULT_MAX_DEPTH}")
        # 任务-租户归属校验(与其他内部回调同口径:get_step 不带租户条件,
        # 缺此闸则同内部令牌可对异租户任务开协作账,账行租户归属错位)
        if await self._repo.get_task_system(task_id=task_id, tenant_id=tenant_id) is None:
            raise TaskNotFound
        step = await self._repo.get_step(task_id=task_id, step_id=step_id)
        if step is None:
            raise TaskNotFound
        if str(step["status"]) != m.S_RUNNING:
            raise TaskConflict("协作仅可由执行中步骤发起")
        if not m.has_collab_topology(step):
            raise TaskConflict("步骤未声明协作拓扑")
        from kongming.config import get_settings

        topo = m.normalize_collab_topology(
            cast("dict[str, Any] | None", step.get("collab_topology")),
            max_workers_cap=get_settings().collab_max_workers,
        )
        superseded = await self._repo.supersede_open_collab_runs(task_id=task_id, step_id=step_id)
        run_id = str(uuid7())
        await self._repo.insert_collab_run(
            run_id=run_id,
            task_id=task_id,
            step_id=step_id,
            tenant_id=tenant_id,
            topology=topo,
            depth=depth,
        )
        event: dict[str, object] = {
            "at": _now(),
            "kind": "collab_started",
            "step": step_id,
            "run": run_id,
            "max_workers": topo.get("max_workers"),
            "critic": topo.get("critic"),
            "dispatch": topo.get("dispatch"),
        }
        if superseded:
            event["superseded"] = superseded
        await self._repo.append_task_event(task_id=task_id, event=event)
        return {"run_id": run_id, "topology": topo}

    async def collab_worker_start(
        self,
        *,
        task_id: str,
        run_id: str,
        tenant_id: str,
        worker_no: int,
        subgoal: str,
        bot_key: str | None,
        trace_id: str,
    ) -> None:
        """worker 派发落账(内部回调;运行须在途,序号受硬顶约束)。"""
        run = await self._repo.get_collab_run_system(
            run_id=run_id, task_id=task_id, tenant_id=tenant_id
        )
        if run is None:
            raise TaskNotFound
        if str(run["status"]) != m.R_RUNNING:
            raise TaskConflict("协作运行已终态,不可派发 worker")
        topo = cast("dict[str, Any]", run.get("topology") or {})
        declared = int(topo.get("max_workers") or m.COLLAB_DEFAULT_WORKERS)
        if worker_no < 1 or worker_no > max(declared * 2 + 1, COLLAB_WORKER_NO_HARD_CAP):
            raise TaskConflict("worker 序号超上限")
        if not await self._repo.insert_collab_worker(
            run_id=run_id,
            worker_no=worker_no,
            subgoal=subgoal[:2000],
            bot_key=bot_key,
            trace_id=trace_id[:64],
        ):
            # 唯一键冲突或运行已不在途(写入谓词未命中):均拒绝落账
            raise TaskConflict("worker 序号已占用或运行已不在途")
        await self._repo.append_task_event(
            task_id=task_id,
            event={
                "at": _now(),
                "kind": "collab_worker_started",
                "run": run_id,
                "worker": worker_no,
            },
        )

    async def collab_worker_result(
        self,
        *,
        task_id: str,
        run_id: str,
        tenant_id: str,
        worker_no: int,
        status: str,
        conclusion: dict[str, object],
        evidence: dict[str, object],
        usage: dict[str, object],
        trace_id: str,
    ) -> None:
        """worker 终态回写(内部回调;CAS 拒绝迟到/重复回写)。"""
        run = await self._repo.get_collab_run_system(
            run_id=run_id, task_id=task_id, tenant_id=tenant_id
        )
        if run is None:
            raise TaskNotFound
        if status not in (m.W_DONE, m.W_FAILED, m.W_SKIPPED):
            raise TaskConflict(f"worker 终态非法: {status}")
        if not await self._repo.finish_collab_worker(
            run_id=run_id,
            worker_no=worker_no,
            status=status,
            conclusion=conclusion,
            evidence=evidence,
            usage=usage,
            trace_id=trace_id[:64],
        ):
            raise TaskConflict("worker 状态已变更(非在途)")
        await self._repo.append_task_event(
            task_id=task_id,
            event={
                "at": _now(),
                "kind": "collab_worker_finished",
                "run": run_id,
                "worker": worker_no,
                "to": status,
            },
        )

    async def collab_finish_run(
        self,
        *,
        task_id: str,
        run_id: str,
        tenant_id: str,
        verdict: str,
        pending_count: int,
        dissent: bool,
        summary: str,
    ) -> str:
        """协作运行终态回写(内部回调):状态按聚合结论诚实映射
        (未派发清单非空=PARTIAL),事件携带裁决摘要。返回运行终态。
        收口同时收敛在途 worker 行(结果回写失败/执行中断的行不得在终态
        运行下永悬 RUNNING——旧账不谎称完成)。"""
        run = await self._repo.get_collab_run_system(
            run_id=run_id, task_id=task_id, tenant_id=tenant_id
        )
        if run is None:
            raise TaskNotFound
        status = m.collab_run_status(verdict=verdict, pending_count=max(0, pending_count))
        if not await self._repo.finish_collab_run(run_id=run_id, status=status, verdict=verdict):
            raise TaskConflict("协作运行已终态")
        converged = await self._repo.skip_open_collab_workers(run_id=run_id)
        event: dict[str, object] = {
            "at": _now(),
            "kind": "collab_finished",
            "run": run_id,
            "verdict": verdict,
            "to": status,
            "dissent": dissent,
            "pending": max(0, pending_count),
            "summary": summary[:500],
        }
        if converged:
            event["workers_skipped"] = converged
        await self._repo.append_task_event(task_id=task_id, event=event)
        return status

    async def finish_step(
        self, *, task_id: str, tenant_id: str, result: m.StepResult, epoch: int | None = None
    ) -> str:
        """步骤结果回写(证据驱动);预算累加(含墙钟分步计量)+ 事件;返回编排决策。
        终态任务(失败后)上的迟到回写:保留步骤证据,但不再追加步骤/累加预算
        (终态任务无执行者,账本封账),事件标注迟到。
        派发代际失配 → "stale_epoch":旧循环的执行属并发期产物,新派发已把在途
        步回队重跑(诚实语义=丢弃旧账,不写步骤不累加预算不留事件)。"""
        task = await self._repo.get_task_system(task_id=task_id, tenant_id=tenant_id)
        if task is None:
            raise TaskNotFound
        if epoch is not None and epoch != int(task.get("dispatch_epoch") or 0):
            return "stale_epoch"
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
            # 执行侧终态写同持代际:读检与 CAS 之间若 resume 回队+新派发重占,
            # 无谓词的 DONE 写会盖到在位执行者的在途步骤上
            expect_epoch=epoch,
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
        self,
        *,
        task_id: str,
        tenant_id: str,
        status: str,
        fail_reason: str | None,
        epoch: int | None = None,
    ) -> str:
        """编排终态回写(白名单内收口):DONE 前置「全部步骤终态」校验
        (审批挂起/在途步骤未决时拒绝,防审批闸被 finish 旁路);
        派发代际失配 → 409(旧循环不得收口任务终态,由在位派发负责)。"""
        task = await self._repo.get_task_system(task_id=task_id, tenant_id=tenant_id)
        if task is None:
            raise TaskNotFound
        if epoch is not None and epoch != int(task.get("dispatch_epoch") or 0):
            raise TaskConflict("派发代际失配,终态收口由在位派发负责")
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
            expect_epoch=epoch,
        )
        if not ok:
            # 谓词未命中(状态漂移/并发 bump):不得向执行面谎报收口成功,
            # 任务终态归属在位执行者(重启清扫/在位派发兜底)
            raise TaskConflict("任务状态/派发代数已推进,终态收口未落地")
        return status

    async def mark_interrupted_stale(self) -> int:
        """启动清扫:RUNNING/PLANNING → INTERRUPTED(不谎称完成)
        + 挂起超 TTL 的审批步骤 SKIPPED(spec 审批过期语义)。"""
        marked = await self._repo.mark_interrupted_stale()
        await self._repo.expire_stale_approvals()
        return marked

    # ── 内部 ──

    async def _step_cas_miss_decision(
        self, *, task_id: str, tenant_id: str, epoch: int | None
    ) -> dict[str, object]:
        """执行侧步骤 CAS 未命中的归因决策:代际失配优先报 abort(旧循环
        即刻退出,不把失配误报为「控制面跳过」多走一步);其余按当前任务态。"""
        cur = await self._repo.get_task_system(task_id=task_id, tenant_id=tenant_id)
        st = str(cur["status"]) if cur else m.T_CANCELLED
        if cur is not None and epoch is not None and epoch != int(cur.get("dispatch_epoch") or 0):
            return {
                "decision": "abort",
                "task_status": st,
                "reason": "派发代际失配,新派发已接管(旧编排循环退出)",
            }
        if st == m.T_PAUSED:
            return {"decision": "pause", "reason": "任务已暂停"}
        if st != m.T_RUNNING:
            return {
                "decision": "abort",
                "task_status": st,
                "reason": f"任务已 {st},不再执行剩余步骤",
            }
        return {"decision": "skip", "reason": "步骤状态已变更"}

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
