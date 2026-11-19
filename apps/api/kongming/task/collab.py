"""task 协作账本用例:运行开账/worker 派发与回写/运行收口/圆桌轮次上账。

内部编排回调经 internal_router 端点收口;只消费 CollabLedgerRepo 端口,
与任务生命周期用例(task.service)零方法级互调。
状态迁移全部经 CAS(乐观锁);迟到/重复回写诚实拒绝。
"""

from datetime import UTC, datetime
from typing import Any, cast

from kongming.task import models as m
from kongming.task.errors import TaskConflict, TaskNotFound
from kongming.task.ports import TaskCollabRepo
from kongming.uuid7 import uuid7

# 协作 worker 序号硬顶(失控兜底:worker 派发 + 分歧验证 + critic 修订重派
# 全计入;agent 面预算先于此绑死,此处只防畸形客户端灌行)
COLLAB_WORKER_NO_HARD_CAP = 12


def _now() -> str:
    return datetime.now(UTC).isoformat()


class CollabLedgerService:
    def __init__(self, repo: TaskCollabRepo) -> None:
        self._repo = repo

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
        # 编号,拓扑名册长度不可作依据
        dispatched_max = await self._repo.max_collab_worker_no(run_id=run_id)
        await self._assert_roundtable_worker(
            run_id=run_id, kind=kind, worker_no=worker_no, dispatched_max=dispatched_max
        )
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
        await self._append_turn_event(
            task_id=task_id,
            run_id=run_id,
            round_no=round_no,
            kind=kind,
            worker_no=worker_no,
            dispatched_max=dispatched_max,
            content=content,
        )

    async def _assert_roundtable_worker(
        self, *, run_id: str, kind: str, worker_no: int, dispatched_max: int
    ) -> None:
        """名册运行期锁定:主席判定恒 0 号位且须有在桌参与者;其余轮次须命中
        实派序号(上界内的空洞序号同样拒绝——存在性语义,不是上界语义)。"""
        if kind == m.TURN_VERDICT:
            if worker_no != m.CHAIR_WORKER_NO:
                raise TaskConflict("主席判定仅可记 0 号位")
            if dispatched_max < 1:
                # 无任何参与者在桌,判定无对象(游离判定通道关闭;编排契约
                # 保证判定恒在发言后,此处为账本侧同口径兜底)
                raise TaskConflict("名册无人,不受理判定行")
            return
        if worker_no < 1 or worker_no > dispatched_max:
            raise TaskConflict("参与者不在实派名册(名册运行期锁定)")
        if not await self._repo.collab_worker_exists(run_id=run_id, worker_no=worker_no):
            raise TaskConflict("参与者序号未实派(名册空洞拒绝)")

    async def _append_turn_event(
        self,
        *,
        task_id: str,
        run_id: str,
        round_no: int,
        kind: str,
        worker_no: int,
        dispatched_max: int,
        content: dict[str, object],
    ) -> None:
        """轮次事件:主席判定行记判定事件;末位行记每轮完成事件。"""
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
            return
        if worker_no != dispatched_max:
            return
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
