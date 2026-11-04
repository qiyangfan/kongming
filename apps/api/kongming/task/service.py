"""task 用例:创建/迁移/审批/预算/事件(用例层;编排回调经内部端点)。

原则:状态迁移全部经白名单 + CAS(乐观锁);非法迁移拒绝并留事件审计;
审批归属=任务归属人;预算超限 PAUSED 诚实收口。
"""

import logging
from datetime import UTC, datetime
from typing import Any, cast

from kongming.task import models as m
from kongming.task.ports import (
    CollabBotResolver,
    GraphConfirmRepo,
    StagnationJudge,
    TaskRepo,
)
from kongming.uuid7 import uuid7  # 直接使用 UUIDv7 纯函数，避免依赖数据库模块。

log = logging.getLogger(__name__)

# 协作 worker 序号硬顶(失控兜底:worker 派发 + 分歧验证 + critic 修订重派
# 全计入;agent 面预算先于此绑死,此处只防畸形客户端灌行)
COLLAB_WORKER_NO_HARD_CAP = 12

# 看门狗事件账本 kinds(巡检去重标记 + 夺权留痕;web 事件流按原始 kind 兜底展示)
EVENT_WATCHDOG_JUDGED = "watchdog_judged"
EVENT_WATCHDOG_TAKEN_OVER = "watchdog_taken_over"


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
    def __init__(
        self,
        repo: TaskRepo,
        *,
        bot_resolver: CollabBotResolver | None = None,
        graph_repo: GraphConfirmRepo | None = None,
    ) -> None:
        self._repo = repo
        self._bot_resolver = bot_resolver
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
        task = await self._require(task_id=task_id, tenant_id=tenant_id, user_id=user_id)
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
        await self._purge_graph_threads(task_id=task_id, tenant_id=tenant_id)
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
        """图内挂起上账(编排回调):任务循环内高危动作到达确认点 → 建/复用
        确认行,步骤 RUNNING→AWAITING_APPROVAL,任务 RUNNING→AWAITING_APPROVAL
        (既有白名单边,状态机不扩)。幂等:同 confirm_id 已存在 → 同态返回
        现状态(编排重入/重启再挂不重复上账);代际失配 → stale_epoch。"""
        if self._graph is None:
            raise TaskConflict("图内确认能力未启用")
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
                "at": _now(),
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
        if self._graph is None:
            raise TaskConflict("图内确认能力未启用")
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
        await self._move(
            task_id=task_id,
            tenant_id=tenant_id,
            user_id=user_id,
            task=task,
            to=m.T_RUNNING,
            event_kind="graph_confirm_approved" if approve else "graph_confirm_rejected",
        )
        return {"confirm_id": confirm_id, "status": to_status, "step_id": step_id}

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
            out = await self._repo.insert_steps(task_id=task_id, plans=steps, start_ord=0)
            await self._apply_declared_topology(
                task_id=task_id,
                tenant_id=tenant_id,
                owner_id=str(task.get("user_id") or ""),
                events=cast("list[object]", task.get("events") or []),
                steps=out,
            )
            return out
        if task["status"] == m.T_RUNNING and not await self._repo.get_steps(task_id=task_id):
            # 规划期中断 → resume → 重规划(计划未落账,不存在重复步骤)
            await self._repo.append_task_event(
                task_id=task_id, event={"at": _now(), "kind": "replanned", "steps": len(steps)}
            )
            out = await self._repo.insert_steps(task_id=task_id, plans=steps, start_ord=0)
            await self._apply_declared_topology(
                task_id=task_id,
                tenant_id=tenant_id,
                owner_id=str(task.get("user_id") or ""),
                events=cast("list[object]", task.get("events") or []),
                steps=out,
            )
            return out
        raise TaskConflict("任务不处于规划态")

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

        try:
            await self.set_step_topology(
                task_id=task_id,
                step_id=str(pending["id"]),
                tenant_id=tenant_id,
                user_id=owner_id,
                raw_topology=declared,
                max_workers_cap=get_settings().collab_max_workers,
            )
        except (TaskConflict, TaskNotFound, TopologyBotNotFound) as e:
            await self._repo.append_task_event(
                task_id=task_id,
                event={
                    "at": _now(),
                    "kind": "trigger_topology_failed",
                    "reason": f"{type(e).__name__}: {str(e)[:200]}",
                },
            )
            return
        await self._repo.append_task_event(
            task_id=task_id,
            event={"at": _now(), "kind": "trigger_topology_applied", "step": str(pending["id"])},
        )

    async def begin_step(
        self, *, task_id: str, tenant_id: str, step_id: str, epoch: int | None = None
    ) -> dict[str, object]:
        """决定步骤是否开始：需确认时挂起，预算超限时暂停；任务已停止或派发代际过期时终止旧循环。"""
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
        # asyncpg 返回 UUID 对象:与 str 形参比较前归一(见 docs/HANDOFF.md)
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
        out: dict[str, object] = {"decision": "execute"}
        # 图内挂起恢复提示:步骤因确认挂起后批准/拒绝
        # 回队重派,决策随 begin_step 下发;agent 以线程快照的在途确认复合校验
        # (提示仅建议,快照不在途即忽略)——过期行不提示(过期=步骤跳过语义)
        if self._graph is not None:
            hint = await self._graph.latest_decided_confirm_for_step(
                task_id=task_id, step_id=step_id
            )
            if hint is not None:
                out["resume_confirm"] = {
                    "confirm_id": str(hint["confirm_id"]),
                    "decision": str(hint["status"]),
                    "tool_name": str(hint["tool_name"] or ""),
                }
        return out

    async def _collab_decision(self, *, tenant_id: str, step: dict[str, Any]) -> dict[str, object]:
        """协作步骤的执行决策载荷:拓扑(以库内归一值为准,再按当前
        部署上限复钳)+ Bot 运行时解析(人格/工具子集;解析失败如实降级为
        无绑定,不虚报人格注入)。圆桌形态逐参与者解析各自 Bot。"""
        from kongming.config import get_settings

        topo = m.normalize_collab_topology(
            cast("dict[str, Any] | None", step.get("collab_topology")),
            max_workers_cap=get_settings().collab_max_workers,
            max_rounds_cap=get_settings().collab_max_rounds,
        )
        if str(topo.get("kind") or "") == m.COLLAB_KIND_ROUNDTABLE:
            parts: list[dict[str, object]] = []
            for p in cast("list[dict[str, object]]", topo.get("participants") or []):
                p_bot: dict[str, object] | None = None
                p_bot_key = p.get("bot_key")
                if p_bot_key and self._bot_resolver is not None:
                    p_bot = await self._bot_resolver.resolve(
                        tenant_id=tenant_id, bot_key=str(p_bot_key)
                    )
                parts.append({**p, "bot": p_bot})
            return {**topo, "participants": parts}
        bot: dict[str, object] | None = None
        bot_key = topo.get("bot_key")
        if bot_key and self._bot_resolver is not None:
            bot = await self._bot_resolver.resolve(tenant_id=tenant_id, bot_key=str(bot_key))
        out: dict[str, object] = {**topo, "bot": bot}
        # 分工名册逐成员运行时解析:失败如实降级
        # None 不虚报人格注入(剔除在执行面 fail-closed 收口,圆桌同口径)
        roster = m.decomposition_roster(topo)
        if roster:
            workers: list[dict[str, object]] = []
            for w in roster:
                w_bot: dict[str, object] | None = None
                w_key = w.get("bot_key")
                if w_key and self._bot_resolver is not None:
                    w_bot = await self._bot_resolver.resolve(
                        tenant_id=tenant_id, bot_key=str(w_key)
                    )
                workers.append({**w, "bot": w_bot})
            out["workers"] = workers
        return out

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
        """步骤声明协作拓扑(配置面):归属校验 → 步骤 PENDING 闸 → Bot 存在性
        校验(绑定口径同 Bot 运行时)→ 归一钳制落库。返回归一后的拓扑。"""
        await self._require(task_id=task_id, tenant_id=tenant_id, user_id=user_id)
        step = await self._repo.get_step(task_id=task_id, step_id=step_id)
        if step is None:
            raise TaskNotFound
        if str(step["status"]) != m.S_PENDING:
            raise TaskConflict("仅 PENDING 步骤可声明协作拓扑")
        from kongming.config import get_settings

        topo = m.normalize_collab_topology(
            raw_topology,
            max_workers_cap=max_workers_cap,
            max_rounds_cap=get_settings().collab_max_rounds,
        )
        if str(topo.get("kind") or "") == m.COLLAB_KIND_ROUNDTABLE and raw_topology.get("bot_key"):
            # 圆桌绑定收口在名册(逐参与者);顶层 bot_key 不生效,拒绝歧义声明。
            # 判原始载荷:归一结果在圆桌分支已剥 bot_key,判归一值恒假(死闸)
            raise TaskConflict("圆桌形态不受理顶层 bot_key(用参与者名册绑定)")
        roster = m.decomposition_roster(topo)
        if roster and raw_topology.get("bot_key"):
            # 分工名册绑定收口在名册(逐成员),
            # 顶层单绑定与名册不共存,拒绝歧义声明(与圆桌闸同式判 raw)
            raise TaskConflict("分工名册与顶层 bot_key 不共存(用名册逐成员绑定)")
        if self._bot_resolver is not None:
            if str(topo.get("kind") or "") == m.COLLAB_KIND_ROUNDTABLE:
                for p in cast("list[dict[str, object]]", topo.get("participants") or []):
                    p_bot_key = p.get("bot_key")
                    if p_bot_key and (
                        await self._bot_resolver.resolve(
                            tenant_id=tenant_id, bot_key=str(p_bot_key)
                        )
                        is None
                    ):
                        raise TopologyBotNotFound(str(p_bot_key))
            else:
                for w in roster:
                    w_bot_key = w.get("bot_key")
                    if w_bot_key and (
                        await self._bot_resolver.resolve(
                            tenant_id=tenant_id, bot_key=str(w_bot_key)
                        )
                        is None
                    ):
                        raise TopologyBotNotFound(str(w_bot_key))
                bot_key = topo.get("bot_key")
                if bot_key and (
                    await self._bot_resolver.resolve(tenant_id=tenant_id, bot_key=str(bot_key))
                    is None
                ):
                    raise TopologyBotNotFound(str(bot_key))
        elif (
            topo.get("bot_key")
            or any(
                p.get("bot_key")
                for p in cast("list[dict[str, object]]", topo.get("participants") or [])
            )
            or any(w.get("bot_key") for w in roster)
        ):
            raise TaskConflict("协作 Bot 绑定未启用")
        if not await self._repo.update_step_topology(
            task_id=task_id, step_id=step_id, topology=topo
        ):
            raise TaskConflict("步骤状态已变更,不可声明协作拓扑")
        await self._repo.append_task_event(
            task_id=task_id,
            # 显式写回事件类型，避免拓扑对象中的 kind 覆盖信封字段。
            event={"at": _now(), "step": step_id, **topo, "kind": "collab_topology_set"},
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
            max_rounds_cap=get_settings().collab_max_rounds,
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
            "topology_kind": str(topo.get("kind") or m.COLLAB_KIND_DECOMPOSITION),
            "max_workers": topo.get("max_workers"),
            "critic": topo.get("critic"),
            "dispatch": topo.get("dispatch"),
            "max_rounds": topo.get("max_rounds"),
            "participants": len(cast("list[object]", topo.get("participants") or [])),
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
        if str(topo.get("kind") or "") == m.COLLAB_KIND_ROUNDTABLE:
            # 圆桌:参与者即 worker 行,名册声明期锁定、无核实/修订重派余量——
            # 上界=名册长度(2x+1 余量式只属分工形态;余量会让超名册行抬高
            # 实派最大序号,名册锁定与「每轮完成」事件判定双双失真)
            declared = len(m.roundtable_roster(topo))
            if worker_no < 1 or worker_no > declared:
                raise TaskConflict("参与者不在圆桌名册(名册声明期锁定)")
        else:
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

    async def collab_record_turn(
        self,
        *,
        task_id: str,
        run_id: str,
        tenant_id: str,
        round_no: int,
        worker_no: int,
        kind: str,
        content: dict[str, object],
        evidence: dict[str, object],
        usage: dict[str, object],
        trace_id: str,
    ) -> None:
        """圆桌轮次上账(内部回调):拓扑形态闸(非圆桌运行不受理,无游离
        轮次通道)+ 名册运行期锁定(非名册序号拒绝,主席判定恒 0 号)+ 轮次
        上限闸。唯一键拒绝重复回写;在途谓词拒绝终态运行上的迟到轮次。
        事件:主席判定行=判定事件;名册末位行=每轮完成事件(开桌/收口沿用
        collab_started/collab_finished)。"""
        run = await self._repo.get_collab_run_system(
            run_id=run_id, task_id=task_id, tenant_id=tenant_id
        )
        if run is None:
            raise TaskNotFound
        if kind not in m.TURN_KINDS:
            raise TaskConflict(f"轮次类型非法: {kind}")
        topo = cast("dict[str, Any]", run.get("topology") or {})
        if str(topo.get("kind") or "") != m.COLLAB_KIND_ROUNDTABLE:
            raise TaskConflict("非圆桌运行不受理轮次上账")
        # 实派名册(参与者行)为锁定基准:有效名册经零工具/绑定剔除后压缩
        # 编号,拓扑名册长度不可作依据;序号须真实派发过(上界内的空洞序号
        # 同样拒绝——名册锁定是存在性语义,不是上界语义)
        dispatched_max = await self._repo.max_collab_worker_no(run_id=run_id)
        if kind == m.TURN_VERDICT:
            if worker_no != m.CHAIR_WORKER_NO:
                raise TaskConflict("主席判定仅可记 0 号位")
            if dispatched_max < 1:
                # 无任何参与者在桌,判定无对象(游离判定通道关闭;编排契约
                # 保证判定恒在发言后,此处为账本侧同口径兜底)
                raise TaskConflict("名册无人,不受理判定行")
        else:
            if worker_no < 1 or worker_no > dispatched_max:
                raise TaskConflict("参与者不在实派名册(名册运行期锁定)")
            if not await self._repo.collab_worker_exists(run_id=run_id, worker_no=worker_no):
                raise TaskConflict("参与者序号未实派(名册空洞拒绝)")
        if round_no < 1 or round_no > int(topo.get("max_rounds") or 0):
            raise TaskConflict("轮次超拓扑上限")
        inserted = await self._repo.insert_collab_turn(
            run_id=run_id,
            round_no=round_no,
            worker_no=worker_no,
            kind=kind,
            content=content,
            evidence=evidence,
            usage=usage,
            trace_id=trace_id[:64],
        )
        if not inserted:
            raise TaskConflict("轮次已记录或运行已不在途")
        if kind == m.TURN_VERDICT:
            await self._repo.append_task_event(
                task_id=task_id,
                event={
                    "at": _now(),
                    "kind": "collab_round_verdict",
                    "run": run_id,
                    "round": round_no,
                    "consensus": bool(content.get("consensus")),
                },
            )
        elif worker_no == dispatched_max:
            # 末位判定按门禁时点的实派最大序号(有效名册经剔除压缩编号,
            # 拓扑名册长度不可作依据;复用门禁读数,避免二次查询
            # 与并发派发下的判定漂移)
            await self._repo.append_task_event(
                task_id=task_id,
                event={
                    "at": _now(),
                    "kind": "collab_round_finished",
                    "run": run_id,
                    "round": round_no,
                },
            )

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
        """编排终态回写(白名单内收口):同态请求幂等收敛(agent 侧已收口,
        段三复收口不重复迁移);DONE 前置「全部步骤终态」校验(审批挂起/
        在途步骤未决时拒绝,防审批闸被 finish 旁路);派发代际失配 →
        409(旧循环不得收口任务终态,由在位派发负责)。"""
        task = await self._repo.get_task_system(task_id=task_id, tenant_id=tenant_id)
        if task is None:
            raise TaskNotFound
        if epoch is not None and epoch != int(task.get("dispatch_epoch") or 0):
            raise TaskConflict("派发代际失配,终态收口由在位派发负责")
        current = str(task["status"])
        if current == status:
            # 同态幂等收敛(agent 侧已终态化,api 编排循环按 final_status 复收口
            # 是常态链路):不重复迁移、不留噪音事件,与 _transition_task_system
            # 的同态口径一致;终态不可复活,跨态请求仍走白名单拒绝
            return current
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
        if status in m.TASK_TERMINAL:
            await self._purge_graph_threads(task_id=task_id, tenant_id=tenant_id)
        return status

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
                        "at": _now(),
                        "kind": "graph_confirm_expired",
                        "confirm": str(r["confirm_id"]),
                    },
                )
        return marked

    # ── 看门狗:在途任务停滞巡检(spec「运行中停滞检测与判读」「停滞任务夺权收口」) ──

    async def patrol_stagnation(self, *, stale_sec: int, judge: StagnationJudge) -> dict[str, int]:
        """巡检一轮:候选(心跳超阈在途任务)两段式决策。

        末事件非判读标记 → 单次判读:「仍进展」落标记事件重置观察窗,
        「停滞」即夺权收口,判读不可用落 unavailable 标记;末事件已是
        判读标记 → 二次超窗机械收口(同一滞留段不再判读)。返回
        候选/判读/收口计数。commit 所有权在巡检循环(整 tick 原子);
        收口谓词未命中(并发迁移先到)即放弃,不抛不重试。"""
        candidates = await self._repo.list_stale_inflight(stale_sec=stale_sec)
        judged = taken = 0
        for row in candidates:
            if str(row.get("last_kind") or "") == EVENT_WATCHDOG_JUDGED:
                # 判读后仍无任何活动且再超阈:机械收口(误判进展/判读不可用的兜底)
                prev = str(row.get("last_verdict") or "")
                prev_label = "判读不可用" if prev == "unavailable" else (prev or "不可用")
                if await self._watchdog_takeover(
                    row, f"看门狗:判读后仍停滞(二次超窗,前判读={prev_label})"
                ):
                    taken += 1
                continue
            verdict = await judge.judge(
                goal=str(row["goal"]),
                events_tail=cast("list[dict[str, object]]", row.get("events_tail") or []),
                stale_sec=stale_sec,
            )
            if verdict is None:
                await self._repo.append_task_event(
                    task_id=str(row["id"]),
                    event={
                        "at": _now(),
                        "kind": EVENT_WATCHDOG_JUDGED,
                        "verdict": "unavailable",
                        "reason": "判读不可用,二次超窗将机械收口",
                    },
                )
                judged += 1
                continue
            if verdict.verdict == "progressing":
                await self._repo.append_task_event(
                    task_id=str(row["id"]),
                    event={
                        "at": _now(),
                        "kind": EVENT_WATCHDOG_JUDGED,
                        "verdict": "progressing",
                        "reason": verdict.reason,
                    },
                )
                judged += 1
                continue
            if await self._watchdog_takeover(row, f"看门狗:判读停滞 —— {verdict.reason}"):
                taken += 1
        return {"candidates": len(candidates), "judged": judged, "taken_over": taken}

    async def _watchdog_takeover(self, row: dict[str, Any], reason: str) -> bool:
        """夺权收口:派发代际自增(旧编排循环回调全失配)+ 条件迁移
        INTERRUPTED(新代数入 CAS 谓词)。谓词未命中返回 False。"""
        task_id = str(row["id"])
        epoch = await self._repo.bump_dispatch_epoch(task_id=task_id)
        if epoch is None:
            return False
        ok = await self._repo.transition_task_system(
            task_id=task_id,
            tenant_id=str(row["tenant_id"]),
            from_status=str(row["status"]),
            to_status=m.T_INTERRUPTED,
            fail_reason=reason,
            event={
                "at": _now(),
                "kind": EVENT_WATCHDOG_TAKEN_OVER,
                "reason": reason,
                "epoch": epoch,
            },
            expect_epoch=epoch,
        )
        if not ok:
            log.info("看门狗收口谓词未命中(任务已被并发迁移): task_id=%s", task_id)
        return ok

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

    async def _purge_graph_threads(self, *, task_id: str, tenant_id: str) -> None:
        """任务终态钩子:该任务全部图级快照清理(spec:快照随任务保留策略处置,
        SHALL NOT 无界增长);恢复价值随终态消失,事件时间线已承载过程审计。"""
        if self._graph is None:
            return
        for s in await self._repo.get_steps(task_id=task_id):
            await self._graph.delete_thread(
                tenant_id=tenant_id,
                thread_id=f"task:{task_id}:step:{s['id']}",
            )

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
