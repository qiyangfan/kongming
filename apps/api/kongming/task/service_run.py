"""task 编排回调用例(agent 经内部端点调用):规划回写、步骤开始决策、
步骤结果回写与终态收口。

状态迁移全部经白名单 + CAS;执行侧写同持派发代际,代际失配的旧循环在此退出。
"""

from datetime import UTC, datetime
from typing import Any, cast

from kongming.task import models as m
from kongming.task.collab_topology import TaskTopologyService
from kongming.task.errors import (
    TaskConflict,
    TaskNotFound,
    TopologyBotNotFound,
)
from kongming.task.ports import GraphConfirmRepo, TaskRepo
from kongming.task.service_common import epoch_mismatch, now_iso


def _abort_status(status: str) -> str:
    """非运行态的任务给编排循环的退出原因(保留原中文摘要口径)。"""
    return f"任务已 {status},不再执行剩余步骤"


class TaskRunService:
    def __init__(
        self,
        repo: TaskRepo,
        *,
        topology: TaskTopologyService,
        graph_repo: GraphConfirmRepo | None = None,
    ) -> None:
        self._repo = repo
        self._topology = topology
        self._graph = graph_repo

    # ── 规划回写 ──

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
                event={"at": now_iso(), "kind": "planned", "steps": len(steps)},
            )
            if not ok:
                raise TaskConflict("任务状态已变更(并发规划)")
            return await self._insert_planned(
                task=task, task_id=task_id, tenant_id=tenant_id, steps=steps
            )
        if task["status"] == m.T_RUNNING and not await self._repo.get_steps(task_id=task_id):
            # 规划期中断 → resume → 重规划(计划未落账,不存在重复步骤)
            await self._repo.append_task_event(
                task_id=task_id, event={"at": now_iso(), "kind": "replanned", "steps": len(steps)}
            )
            return await self._insert_planned(
                task=task, task_id=task_id, tenant_id=tenant_id, steps=steps
            )
        raise TaskConflict("任务不处于规划态")

    async def _insert_planned(
        self, *, task: dict[str, Any], task_id: str, tenant_id: str, steps: list[m.StepPlan]
    ) -> list[dict[str, Any]]:
        """落步骤清单并消费触发器模板的拓扑声明。"""
        out = await self._repo.insert_steps(task_id=task_id, plans=steps, start_ord=0)
        await self._apply_declared_topology(
            task_id=task_id,
            tenant_id=tenant_id,
            owner_id=str(task.get("user_id") or ""),
            events=cast("list[object]", task.get("events") or []),
            steps=out,
        )
        return out

    async def _apply_declared_topology(
        self,
        *,
        task_id: str,
        tenant_id: str,
        owner_id: str,
        events: list[object],
        steps: list[dict[str, Any]],
    ) -> None:
        """消费触发器模板的拓扑声明标记(spec「task 立项目标」):计划生成后
        对首个 PENDING 步骤经既有声明路径应用(校验同源);应用失败落
        trigger_topology_failed 事件并以串行形态继续,不静默丢弃、不炸任务。"""
        declared: dict[str, Any] | None = None
        consumed = False
        for e in events:
            if not isinstance(e, dict):
                continue
            e_d = cast("dict[str, Any]", e)
            kind = str(e_d.get("kind") or "")
            if kind == "trigger_topology_declared":
                declared = cast("dict[str, Any]", e_d.get("topology") or {})
                consumed = False
            elif kind in ("trigger_topology_applied", "trigger_topology_failed"):
                consumed = True
        if declared is None or consumed or not steps:
            return
        pending = next((s for s in steps if str(s.get("status")) == m.S_PENDING), None)
        if pending is None:
            return
        from kongming.config import get_settings

        step_id = str(pending["id"])
        try:
            await self._topology.set_step_topology(
                task_id=task_id,
                step_id=step_id,
                tenant_id=tenant_id,
                user_id=owner_id,
                raw_topology=declared,
                max_workers_cap=get_settings().collab_max_workers,
            )
        except (TaskConflict, TaskNotFound, TopologyBotNotFound) as e:
            await self._repo.append_task_event(
                task_id=task_id,
                event={
                    "at": now_iso(),
                    "kind": "trigger_topology_failed",
                    "reason": f"{type(e).__name__}: {str(e)[:200]}",
                },
            )
            return
        await self._repo.append_task_event(
            task_id=task_id,
            event={"at": now_iso(), "kind": "trigger_topology_applied", "step": step_id},
        )

    # ── 步骤开始 ──

    async def begin_step(
        self, *, task_id: str, tenant_id: str, step_id: str, epoch: int | None = None
    ) -> dict[str, object]:
        """决定步骤是否开始：需确认时挂起，预算超限时暂停；任务已停止或派发代际过期时终止旧循环。"""
        task = await self._repo.get_task_system(task_id=task_id, tenant_id=tenant_id)
        if task is None:
            raise TaskNotFound
        if epoch_mismatch(task, epoch):
            return self._epoch_abort(task)
        status = str(task["status"])
        if status == m.T_PAUSED:
            return {"decision": "pause", "reason": "任务已暂停"}
        if status != m.T_RUNNING:
            return {"decision": "abort", "task_status": status, "reason": _abort_status(status)}
        return await self._step_decision(
            task=task, task_id=task_id, tenant_id=tenant_id, step_id=step_id, epoch=epoch
        )

    async def _step_decision(
        self,
        *,
        task: dict[str, Any],
        task_id: str,
        tenant_id: str,
        step_id: str,
        epoch: int | None,
    ) -> dict[str, object]:
        """运行中任务的步骤决策:预算闸→步骤状态→确认挂起或开始执行。"""
        budget = await self._budget_decision(
            task=task, task_id=task_id, tenant_id=tenant_id, epoch=epoch
        )
        if budget is not None:
            return budget
        steps = await self._repo.get_steps(task_id=task_id)
        # asyncpg 返回 UUID 对象:与 str 形参比较前归一(见 docs/HANDOFF.md)
        target = next((s for s in steps if str(s["id"]) == step_id), None)
        if target is None:
            raise TaskNotFound
        if target["status"] != m.S_PENDING:
            return {"decision": "skip", "reason": f"步骤状态 {target['status']} 不可执行"}
        if target.get("require_confirm"):
            return await self._await_approval(
                task_id=task_id, tenant_id=tenant_id, step_id=step_id, epoch=epoch
            )
        return await self._start_running(
            task_id=task_id, tenant_id=tenant_id, step_id=step_id, target=target, epoch=epoch
        )

    async def _await_approval(
        self, *, task_id: str, tenant_id: str, step_id: str, epoch: int | None
    ) -> dict[str, object]:
        """需确认步骤:PENDING→AWAITING_APPROVAL,任务 RUNNING→AWAITING_APPROVAL;
        任务并发迁移时回滚步骤并给退出决策(不制造「步骤 AWAITING × 任务终态」死锁)。"""
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
            event={"at": now_iso(), "kind": "await_approval", "step": step_id},
            expect_epoch=epoch,
        )
        if not moved:
            # 任务在步骤挂起瞬间被并发迁移(暂停/取消):回滚步骤态,
            # 按当前任务态给决策
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
            return {"decision": "abort", "task_status": st, "reason": _abort_status(st)}
        return {"decision": "await_approval"}

    async def _start_running(
        self,
        *,
        task_id: str,
        tenant_id: str,
        step_id: str,
        target: dict[str, Any],
        epoch: int | None,
    ) -> dict[str, object]:
        """PENDING→RUNNING + 事件;协作步骤下发给协作决策载荷,普通步骤附带
        图内确认恢复提示(仅建议,agent 以线程快照在途确认复合校验)。"""
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
            task_id=task_id, event={"at": now_iso(), "kind": "step_started", "step": step_id}
        )
        if m.has_collab_topology(target):
            collab = await self._topology.collab_decision(tenant_id=tenant_id, step=target)
            return {"decision": "execute", "collab": collab}
        out: dict[str, object] = {"decision": "execute"}
        await self._attach_resume_confirm(out, task_id=task_id, step_id=step_id)
        return out

    async def _attach_resume_confirm(
        self, out: dict[str, object], *, task_id: str, step_id: str
    ) -> None:
        """图内挂起恢复提示:步骤因确认挂起后批准/拒绝回队重派,决策随
        begin_step 下发;过期行不提示(过期=步骤跳过语义)。"""
        if self._graph is None:
            return
        hint = await self._graph.latest_decided_confirm_for_step(task_id=task_id, step_id=step_id)
        if hint is not None:
            out["resume_confirm"] = {
                "confirm_id": str(hint["confirm_id"]),
                "decision": str(hint["status"]),
                "tool_name": str(hint["tool_name"] or ""),
            }

    async def _budget_decision(
        self, *, task: dict[str, Any], task_id: str, tenant_id: str, epoch: int | None
    ) -> dict[str, object] | None:
        """预算闸:超限则把任务 PAUSED 收口并留事件,返回 pause 决策。"""
        config = m.normalize_budget(cast("dict[str, Any] | None", task.get("budget_config")))
        used = cast("dict[str, Any]", task.get("budget_used") or {})
        reason = m.over_budget(used, config)
        if reason is None:
            return None
        ok = await self._repo.transition_task_system(
            task_id=task_id,
            tenant_id=tenant_id,
            from_status=m.T_RUNNING,
            to_status=m.T_PAUSED,
            fail_reason=reason,
            event={"at": now_iso(), "kind": "budget_paused", "reason": reason},
            expect_epoch=epoch,
        )
        if not ok:
            # 谓词未命中(状态漂移或代际失配):不得宣称暂停生效,
            # 按当前任务态给决策——旧循环在此退出
            cur = await self._repo.get_task_system(task_id=task_id, tenant_id=tenant_id)
            st = str(cur["status"]) if cur else m.T_CANCELLED
            if st == m.T_RUNNING:
                return {"decision": "pause", "reason": reason}
            return {"decision": "abort", "task_status": st, "reason": _abort_status(st)}
        return {"decision": "pause", "reason": reason}

    def _epoch_abort(self, task: dict[str, Any]) -> dict[str, object]:
        """派发代际失配:旧编排循环退出(新派发已接管)。"""
        return {
            "decision": "abort",
            "task_status": str(task["status"]),
            "reason": "派发代际失配,新派发已接管(旧编排循环退出)",
        }

    async def _step_cas_miss_decision(
        self, *, task_id: str, tenant_id: str, epoch: int | None
    ) -> dict[str, object]:
        """执行侧步骤 CAS 未命中的归因决策:代际失配优先报 abort(旧循环
        即刻退出,不把失配误报为「控制面跳过」多走一步);其余按当前任务态。"""
        cur = await self._repo.get_task_system(task_id=task_id, tenant_id=tenant_id)
        st = str(cur["status"]) if cur else m.T_CANCELLED
        if cur is not None and epoch_mismatch(cur, epoch):
            return self._epoch_abort(cur)
        if st == m.T_PAUSED:
            return {"decision": "pause", "reason": "任务已暂停"}
        if st != m.T_RUNNING:
            return {"decision": "abort", "task_status": st, "reason": _abort_status(st)}
        return {"decision": "skip", "reason": "步骤状态已变更"}

    # ── 步骤结果与终态收口 ──

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
        if epoch_mismatch(task, epoch):
            return "stale_epoch"
        task_status = str(task["status"])
        evidence = result.evidence if result.status == m.S_DONE else {}
        reject = m.validate_result_status(result.status, evidence)
        if reject:
            return f"reject:{reject}"
        # 记录步骤开始到结果回写的墙钟耗时，用于校验任务时限。
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
                "at": now_iso(),
                "kind": "step_finished",
                "step": result.step_id,
                "to": result.status,
            },
        )
        if task_status in m.TASK_TERMINAL:
            await self._repo.append_task_event(
                task_id=task_id,
                event={
                    "at": now_iso(),
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
        await self._repo.budget_accumulate(
            task_id=task_id,
            steps=1,
            tokens=result.tokens_used,
            elapsed_ms=elapsed_ms,
        )
        return "continue"

    async def _assert_all_steps_done(self, *, task_id: str) -> None:
        """DONE 前置:全部步骤须 DONE(spec:SKIPPED/FAILED 也是未完成,
        不得借终态口径全称完成)。"""
        steps = await self._repo.get_steps(task_id=task_id)
        open_steps = [s for s in steps if s["status"] != m.S_DONE]
        if open_steps:
            states = ",".join(sorted({str(s["status"]) for s in open_steps}))
            raise TaskConflict(f"存在未完成步骤({states}),不可 DONE")
