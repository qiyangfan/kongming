"""task 协作账本仓储:协作运行/worker/圆桌轮次 SQL(spec 0040/0054)。

协作表由协作用例簇(CollabLedgerService)专用;任务取消时的开放协作账
收敛属任务主链(cancel_open_collab),留在 SqlTaskRepo。状态迁移同样以条件 UPDATE 谓词兜底,
收敛不追认迟到写入。
"""

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.db import uuid7
from kongming.task import models as m
from kongming.task.repository import SqlTaskRepo
from kongming.task.system_repo import SqlTaskSystemRepo


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


class SqlCollabRepo:
    def __init__(self, s: AsyncSession) -> None:
        self._s = s

    async def update_step_topology(
        self, *, task_id: str, step_id: str, topology: dict[str, object]
    ) -> bool:
        """步骤协作拓扑声明(CAS:仅 PENDING 步骤可声明,执行中/终态拒绝)。"""
        hit = (
            await self._s.execute(
                text(
                    "UPDATE public.sys_agent_task_step"
                    " SET collab_topology = CAST(:tp AS jsonb), updated_at = now()"
                    " WHERE id = CAST(:s AS uuid) AND task_id = CAST(:t AS uuid)"
                    "   AND status = 'PENDING' RETURNING id"
                ),
                {"s": step_id, "t": task_id, "tp": _dump(topology)},
            )
        ).scalar_one_or_none()
        return hit is not None

    async def insert_collab_run(
        self,
        *,
        run_id: str,
        task_id: str,
        step_id: str,
        tenant_id: str,
        topology: dict[str, object],
        depth: int,
    ) -> None:
        await self._s.execute(
            text(
                "INSERT INTO public.sys_agent_collab_run"
                " (id, task_id, step_id, tenant_id, topology, status, depth)"
                " VALUES (:i, CAST(:t AS uuid), CAST(:s AS uuid), CAST(:tn AS uuid),"
                "         CAST(:tp AS jsonb), 'RUNNING', :d)"
            ),
            {
                "i": run_id,
                "t": task_id,
                "s": step_id,
                "tn": tenant_id,
                "tp": _dump(topology),
                "d": depth,
            },
        )

    async def supersede_open_collab_runs(self, *, task_id: str, step_id: str) -> int:
        """同一 (task, step) 的在途协作运行收敛为 CANCELLED(重跑前清账;
        在途 worker 一并 SKIPPED——旧账不谎称完成)。返回收敛运行数。"""
        runs = (
            await self._s.execute(
                text(
                    "UPDATE public.sys_agent_collab_run"
                    " SET status = 'CANCELLED', verdict = :sup, updated_at = now()"
                    " WHERE task_id = CAST(:t AS uuid) AND step_id = CAST(:s AS uuid)"
                    "   AND status = 'RUNNING' RETURNING id"
                ),
                {"t": task_id, "s": step_id, "sup": m.VERDICT_SUPERSEDED},
            )
        ).fetchall()
        if runs:
            await self._s.execute(
                text(
                    "UPDATE public.sys_agent_collab_worker w"
                    " SET status = 'SKIPPED', updated_at = now()"
                    " FROM public.sys_agent_collab_run r"
                    " WHERE w.run_id = r.id AND r.task_id = CAST(:t AS uuid)"
                    "   AND r.step_id = CAST(:s AS uuid)"
                    "   AND w.status IN ('PENDING','RUNNING')"
                ),
                {"t": task_id, "s": step_id},
            )
        return len(runs)

    async def get_collab_run_system(
        self, *, run_id: str, task_id: str, tenant_id: str
    ) -> dict[str, Any] | None:
        """内部回调专用读取(租户过滤;step 归属经 task_id 冗余校验)。"""
        row = (
            (
                await self._s.execute(
                    text(
                        "SELECT id, task_id, step_id, tenant_id, topology, status, verdict, depth"
                        " FROM public.sys_agent_collab_run"
                        " WHERE id = CAST(:r AS uuid) AND task_id = CAST(:t AS uuid)"
                        "   AND tenant_id = CAST(:tn AS uuid)"
                    ),
                    {"r": run_id, "t": task_id, "tn": tenant_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        return dict(row) if row else None

    async def insert_collab_worker(
        self,
        *,
        run_id: str,
        worker_no: int,
        subgoal: str,
        bot_key: str | None,
        trace_id: str,
    ) -> bool:
        """worker 账落账(唯一键防重派发双行)。运行在途条件并入写入谓词
        (CAS 风格):先读后写在并发下可把 worker 行挂到刚被收敛的 CANCELLED
        运行上成幽灵账——收敛不追认迟到的派发。"""
        hit = (
            await self._s.execute(
                text(
                    "INSERT INTO public.sys_agent_collab_worker"
                    " (id, run_id, worker_no, subgoal, bot_key, status, trace_id)"
                    " SELECT :i, r.id, :n, :g, :b, 'RUNNING', :tr"
                    " FROM public.sys_agent_collab_run r"
                    " WHERE r.id = CAST(:r AS uuid) AND r.status = 'RUNNING'"
                    " FOR UPDATE OF r"
                    " ON CONFLICT (run_id, worker_no) DO NOTHING RETURNING id"
                ),
                {
                    "i": str(uuid7()),
                    "r": run_id,
                    "n": worker_no,
                    "g": subgoal,
                    "b": bot_key,
                    "tr": trace_id,
                },
            )
        ).scalar_one_or_none()
        return hit is not None

    async def skip_open_collab_workers(self, *, run_id: str) -> int:
        """运行终态收口时收敛在途 worker(结果回写失败/执行中断的行不得
        永悬 RUNNING——与 supersede/cancel「旧账不谎称完成」同口径)。
        返回收敛行数。"""
        rows = (
            await self._s.execute(
                text(
                    "UPDATE public.sys_agent_collab_worker"
                    " SET status = 'SKIPPED', updated_at = now()"
                    " WHERE run_id = CAST(:r AS uuid)"
                    "   AND status IN ('PENDING','RUNNING') RETURNING id"
                ),
                {"r": run_id},
            )
        ).fetchall()
        return len(rows)

    async def max_collab_worker_no(self, *, run_id: str) -> int:
        """该运行实际派发的最大参与者序号(圆桌「每轮完成」判定基准:
        参与者按剔除零工具后的有效名册压缩编号,拓扑名册长度不可作依据)。"""
        row = await self._s.scalar(
            text(
                "SELECT max(worker_no) FROM public.sys_agent_collab_worker"
                " WHERE run_id = CAST(:r AS uuid)"
            ),
            {"r": run_id},
        )
        return int(row) if row is not None else 0

    async def collab_worker_exists(self, *, run_id: str, worker_no: int) -> bool:
        """参与者序号是否实派(名册运行期锁定的存在性闸:上界内的空洞序号
        同样拒绝,轮次通道只对真实派发过的参与者开放)。"""
        hit = await self._s.scalar(
            text(
                "SELECT 1 FROM public.sys_agent_collab_worker"
                " WHERE run_id = CAST(:r AS uuid) AND worker_no = :n"
            ),
            {"r": run_id, "n": worker_no},
        )
        return hit is not None

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
    ) -> bool:
        """worker 终态回写(CAS:RUNNING → 终态;迟到回写拒绝)。"""
        hit = (
            await self._s.execute(
                text(
                    "UPDATE public.sys_agent_collab_worker"
                    " SET status = :st, conclusion = CAST(:c AS jsonb),"
                    "     evidence = CAST(:e AS jsonb), usage = CAST(:u AS jsonb),"
                    "     trace_id = :tr, updated_at = now()"
                    " WHERE run_id = CAST(:r AS uuid) AND worker_no = :n"
                    "   AND status = 'RUNNING' RETURNING id"
                ),
                {
                    "r": run_id,
                    "n": worker_no,
                    "st": status,
                    "c": _dump(conclusion),
                    "e": _dump(evidence),
                    "u": _dump(usage),
                    "tr": trace_id,
                },
            )
        ).scalar_one_or_none()
        return hit is not None

    async def finish_collab_run(self, *, run_id: str, status: str, verdict: str | None) -> bool:
        """协作运行终态回写(CAS:RUNNING → 终态)。"""
        hit = (
            await self._s.execute(
                text(
                    "UPDATE public.sys_agent_collab_run"
                    " SET status = :st, verdict = :v, updated_at = now()"
                    " WHERE id = CAST(:r AS uuid) AND status = 'RUNNING' RETURNING id"
                ),
                {"r": run_id, "st": status, "v": verdict},
            )
        ).scalar_one_or_none()
        return hit is not None

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
    ) -> bool:
        """圆桌轮次上账(追加只写)。运行在途条件并入写入谓词(与 worker
        行同范式:收敛不追认迟到轮次);(run, round, worker) 唯一键防重复回写
        ——主席判定占 worker_no=0,一轮一判定。"""
        hit = (
            await self._s.execute(
                text(
                    "INSERT INTO public.sys_agent_collab_turn"
                    " (id, run_id, round_no, worker_no, kind, content, evidence, usage, trace_id)"
                    " SELECT :i, r.id, :rd, :n, :k, CAST(:c AS jsonb), CAST(:e AS jsonb),"
                    "        CAST(:u AS jsonb), :tr"
                    " FROM public.sys_agent_collab_run r"
                    " WHERE r.id = CAST(:r AS uuid) AND r.status = 'RUNNING'"
                    " FOR UPDATE OF r"
                    " ON CONFLICT (run_id, round_no, worker_no) DO NOTHING RETURNING id"
                ),
                {
                    "i": str(uuid7()),
                    "r": run_id,
                    "rd": round_no,
                    "n": worker_no,
                    "k": kind,
                    "c": _dump(content),
                    "e": _dump(evidence),
                    "u": _dump(usage),
                    "tr": trace_id,
                },
            )
        ).scalar_one_or_none()
        return hit is not None

    async def list_collab_runs(self, *, task_id: str) -> list[dict[str, Any]]:
        """协作运行清单(任务维度,含 worker 行与圆桌轮次转写;查询面单源)。"""
        runs = (
            (
                await self._s.execute(
                    text(
                        "SELECT id, task_id, step_id, topology, status, verdict, depth,"
                        "       created_at, updated_at"
                        " FROM public.sys_agent_collab_run"
                        " WHERE task_id = CAST(:t AS uuid) ORDER BY created_at"
                    ),
                    {"t": task_id},
                )
            )
            .mappings()
            .all()
        )
        workers = (
            (
                await self._s.execute(
                    text(
                        "SELECT w.id, w.run_id, w.worker_no, w.subgoal, w.bot_key, w.status,"
                        "       w.conclusion, w.evidence, w.usage, w.trace_id, w.updated_at"
                        " FROM public.sys_agent_collab_worker w"
                        " JOIN public.sys_agent_collab_run r ON r.id = w.run_id"
                        " WHERE r.task_id = CAST(:t AS uuid)"
                        " ORDER BY w.run_id, w.worker_no"
                    ),
                    {"t": task_id},
                )
            )
            .mappings()
            .all()
        )
        turns = (
            (
                await self._s.execute(
                    text(
                        "SELECT tn.run_id, tn.round_no, tn.worker_no, tn.kind, tn.content,"
                        "       tn.evidence, tn.usage, tn.trace_id, tn.created_at"
                        " FROM public.sys_agent_collab_turn tn"
                        " JOIN public.sys_agent_collab_run r ON r.id = tn.run_id"
                        " WHERE r.task_id = CAST(:t AS uuid)"
                        # created_at 微秒精度且轮次行逐回调独立事务提交,
                        # 可分辨写入序(判定行在名册发言之后);id 兜底同序
                        " ORDER BY tn.run_id, tn.round_no, tn.created_at, tn.id"
                    ),
                    {"t": task_id},
                )
            )
            .mappings()
            .all()
        )
        by_run_workers: dict[str, list[dict[str, Any]]] = {}
        for w in workers:
            by_run_workers.setdefault(str(w["run_id"]), []).append(dict(w))
        by_run_turns: dict[str, list[dict[str, Any]]] = {}
        for tn in turns:
            by_run_turns.setdefault(str(tn["run_id"]), []).append(dict(tn))
        out: list[dict[str, Any]] = []
        for r in runs:
            row = dict(r)
            row["workers"] = by_run_workers.get(str(row["id"]), [])
            row["turns"] = by_run_turns.get(str(row["id"]), [])
            out.append(row)
        return out


class SqlCollabLedgerRepo(SqlTaskRepo, SqlTaskSystemRepo, SqlCollabRepo):
    """协作账本组合仓储:任务主链读写 + 协作账本读写共用同一会话与事务。"""

    def __init__(self, s: AsyncSession) -> None:
        super().__init__(s)
