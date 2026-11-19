"""task 仓储:sys_agent_task / sys_agent_task_step SQL。

隔离铁律:一切读写强制 tenant_id+user_id 双过滤(他人任务 404 同形);
状态迁移由乐观锁版本 + 白名单条件 UPDATE 兜底(并发操作竞态拒绝);
事件时间线为任务行内 jsonb 数组(行锁内追加,版本随迁)。
"""

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.db import uuid7
from kongming.task import models as m


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


class SqlTaskRepo:
    def __init__(self, s: AsyncSession) -> None:
        self._s = s

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
    ) -> str:
        await self._s.execute(
            text(
                "INSERT INTO public.sys_agent_task"
                " (id, tenant_id, user_id, session_id, goal, status,"
                "  budget_config, datasource_key, trace_root)"
                " VALUES (:i, CAST(:t AS uuid), CAST(:u AS uuid), CAST(:s AS uuid),"
                "         :g, 'PLANNING', CAST(:b AS jsonb), :ds, :tr)"
            ),
            {
                "i": task_id,
                "t": tenant_id,
                "u": user_id,
                "s": session_id,
                "g": goal,
                "b": _dump(budget_config),
                "ds": datasource_key,
                "tr": trace_root,
            },
        )
        return task_id

    async def get_task(
        self, *, task_id: str, tenant_id: str, user_id: str
    ) -> dict[str, Any] | None:
        row = (
            (
                await self._s.execute(
                    text(
                        "SELECT id, tenant_id, user_id, session_id, goal, status, version,"
                        "       budget_config, budget_used, datasource_key, trace_root,"
                        "       events, fail_reason, dispatch_epoch, created_at, updated_at"
                        " FROM public.sys_agent_task"
                        " WHERE id = CAST(:i AS uuid) AND tenant_id = CAST(:t AS uuid)"
                        "   AND user_id = CAST(:u AS uuid)"
                    ),
                    {"i": task_id, "t": tenant_id, "u": user_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        return dict(row) if row else None

    async def get_task_system(self, *, task_id: str, tenant_id: str) -> dict[str, Any] | None:
        """内部回调专用(编排回写;X-Internal-Token 闸 + 租户过滤,无用户维度)。"""
        row = (
            (
                await self._s.execute(
                    text(
                        "SELECT id, tenant_id, user_id, session_id, goal, status, version,"
                        "       budget_config, budget_used, datasource_key, trace_root,"
                        "       events, fail_reason, dispatch_epoch, created_at, updated_at"
                        " FROM public.sys_agent_task"
                        " WHERE id = CAST(:i AS uuid) AND tenant_id = CAST(:t AS uuid)"
                    ),
                    {"i": task_id, "t": tenant_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        return dict(row) if row else None

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
    ) -> bool:
        """内部回调的状态迁移(编排驱动;白名单校验在 service 层)。
        expect_epoch:派发代际写入谓词——执行侧写任务态必须带代数入
        WHERE,否则「读后判断→写」之间并发 bump 会放行旧执行者(读检查与
        CAS 写不在同一原子操作,仅读侧判失配存在 TOCTOU 窗口)。"""
        sets = ["status = :to", "version = version + 1", "updated_at = now()"]
        params: dict[str, Any] = {
            "i": task_id,
            "t": tenant_id,
            "from": from_status,
            "to": to_status,
        }
        epoch_guard = ""
        if expect_epoch is not None:
            epoch_guard = "   AND dispatch_epoch = :ep"
            params["ep"] = expect_epoch
        if fail_reason is not None:
            sets.append("fail_reason = :fr")
            params["fr"] = fail_reason
        if event is not None:
            sets.append("events = events || CAST(:ev AS jsonb)")
            params["ev"] = _dump([event])
        hit = (
            await self._s.execute(
                text(
                    "UPDATE public.sys_agent_task SET "  # noqa: S608 —— sets 为受控常量片段
                    + ", ".join(sets)
                    + " WHERE id = CAST(:i AS uuid) AND tenant_id = CAST(:t AS uuid)"
                    "   AND status = :from" + epoch_guard + " RETURNING id"
                ),
                params,
            )
        ).scalar_one_or_none()
        return hit is not None

    async def list_tasks(
        self,
        *,
        tenant_id: str,
        user_id: str,
        page: int,
        size: int,
        status: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        # 全量本人本租户(含终态),按创建时间倒序;status 合法值由 router 校验
        where = " WHERE tenant_id = CAST(:t AS uuid) AND user_id = CAST(:u AS uuid)"
        params: dict[str, Any] = {"t": tenant_id, "u": user_id}
        if status is not None:
            where += " AND status = :st"
            params["st"] = status
        total = int(
            (
                await self._s.execute(
                    text(f"SELECT count(*) FROM public.sys_agent_task{where}"),  # noqa: S608
                    params,
                )
            ).scalar_one()
        )
        rows = (
            (
                await self._s.execute(
                    text(
                        "SELECT id, goal, status, version, budget_used, datasource_key,"  # noqa: S608 —— where 为常量片段
                        "       fail_reason, created_at, updated_at"
                        " FROM public.sys_agent_task"
                        f"{where}"  # noqa: S608
                        " ORDER BY created_at DESC LIMIT :lim OFFSET :off"
                    ),
                    {**params, "lim": size, "off": (page - 1) * size},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows], total

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
    ) -> dict[str, Any] | None:
        """状态迁移(CAS):白名单 + 版本乐观锁 + 事件追加,单语句行锁内原子。
        返回迁移后任务行;条件不满足(版本漂移/状态漂移)返回 None。"""
        sets = ["status = :to", "version = version + 1", "updated_at = now()"]
        params: dict[str, Any] = {
            "i": task_id,
            "t": tenant_id,
            "u": user_id,
            "from": from_status,
            "to": to_status,
        }
        if fail_reason is not None:
            sets.append("fail_reason = :fr")
            params["fr"] = fail_reason
        if budget_used is not None:
            sets.append("budget_used = CAST(:bu AS jsonb)")
            params["bu"] = _dump(budget_used)
        if event is not None:
            sets.append("events = events || CAST(:ev AS jsonb)")
            params["ev"] = _dump([event])
        # 真乐观锁:期望版本入 WHERE(不命中即版本漂移),SET 统一走 version+1
        version_guard = ""
        if expect_version is not None:
            version_guard = "   AND version = :evv"
            params["evv"] = expect_version
        row = (
            (
                await self._s.execute(
                    text(  # noqa: S608 —— sets 为受控常量片段
                        "UPDATE public.sys_agent_task SET "  # noqa: S608 —— sets 为受控常量片段
                        + ", ".join(sets)
                        + " WHERE id = CAST(:i AS uuid) AND tenant_id = CAST(:t AS uuid)"
                        "   AND user_id = CAST(:u AS uuid) AND status = :from"
                        + version_guard
                        + " RETURNING id, status, version, budget_used"
                    ),
                    params,
                )
            )
            .mappings()
            .one_or_none()
        )
        return dict(row) if row else None

    async def insert_steps(
        self, *, task_id: str, plans: list[m.StepPlan], start_ord: int
    ) -> list[dict[str, Any]]:
        """插入计划步骤(PENDING);返回 id/ord/goal/require_confirm 清单。"""
        out: list[dict[str, Any]] = []
        for i, plan in enumerate(plans):
            sid = str(uuid7())
            await self._s.execute(
                text(
                    "INSERT INTO public.sys_agent_task_step"
                    " (id, task_id, ord, goal, status, require_confirm)"
                    " VALUES (:i, CAST(:t AS uuid), :o, :g, 'PENDING', :rc)"
                ),
                {
                    "i": sid,
                    "t": task_id,
                    "o": start_ord + i,
                    "g": plan.goal,
                    "rc": plan.require_confirm,
                },
            )
            out.append(
                {
                    "id": sid,
                    "ord": start_ord + i,
                    "goal": plan.goal,
                    "require_confirm": plan.require_confirm,
                }
            )
        return out

    async def get_steps(self, *, task_id: str) -> list[dict[str, Any]]:
        rows = (
            (
                await self._s.execute(
                    text(
                        "SELECT id, task_id, ord, goal, status, require_confirm,"
                        "       evidence, fail_reason, checkpoint, collab_topology, updated_at"
                        " FROM public.sys_agent_task_step"
                        " WHERE task_id = CAST(:t AS uuid) ORDER BY ord"
                    ),
                    {"t": task_id},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

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
    ) -> bool:
        """步骤状态迁移(CAS,已终态不可改写);命中返回 True。
        clear_confirm:审批通过时一并清除确认标记(一次性闸门,防再挂死循环)。
        mark_started:起跑时刻落 started_at(墙钟预算的分步计量基准)。
        expect_epoch:派发代际写入谓词(经任务行子查询原子复核)——
        执行侧写步骤态必须带代数,防「读后判断→写」窗口内 resume 回队后
        旧循环把回队步骤重新占走。"""
        sets = ["status = :to", "updated_at = now()"]
        if clear_confirm:
            sets.append("require_confirm = false")
        if mark_started:
            sets.append("started_at = now()")
        params: dict[str, Any] = {"i": step_id, "t": task_id, "from": from_status, "to": to_status}
        epoch_guard = ""
        if expect_epoch is not None:
            epoch_guard = "   AND :ep = (SELECT dispatch_epoch"
            epoch_guard += " FROM public.sys_agent_task WHERE id = CAST(:t AS uuid))"
            params["ep"] = expect_epoch
        if evidence is not None:
            sets.append("evidence = CAST(:ev AS jsonb)")
            params["ev"] = _dump(evidence)
        if fail_reason is not None:
            sets.append("fail_reason = :fr")
            params["fr"] = fail_reason
        if checkpoint is not None:
            sets.append("checkpoint = CAST(:cp AS jsonb)")
            params["cp"] = _dump(checkpoint)
        hit = (
            await self._s.execute(
                text(  # noqa: S608 —— sets 为受控常量片段
                    "UPDATE public.sys_agent_task_step SET "  # noqa: S608 —— sets 为受控常量片段
                    + ", ".join(sets)
                    + " WHERE id = CAST(:i AS uuid) AND task_id = CAST(:t AS uuid)"
                    "   AND status = :from" + epoch_guard + " RETURNING id"
                ),
                params,
            )
        ).scalar_one_or_none()
        return hit is not None

    async def first_executable_step(self, *, task_id: str) -> dict[str, Any] | None:
        """最靠前的可执行步骤(PENDING);无则 None。"""
        row = (
            (
                await self._s.execute(
                    text(
                        "SELECT id, ord, goal, status, require_confirm"
                        " FROM public.sys_agent_task_step"
                        " WHERE task_id = CAST(:t AS uuid) AND status = 'PENDING'"
                        " ORDER BY ord LIMIT 1"
                    ),
                    {"t": task_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        return dict(row) if row else None

    async def budget_accumulate(
        self, *, task_id: str, steps: int = 0, tokens: int = 0, elapsed_ms: int = 0
    ) -> dict[str, Any] | None:
        """预算消耗原子累加(jsonb 单语句;行锁内)。返回累加后 budget_used。"""
        row = (
            (
                await self._s.execute(
                    text(
                        "UPDATE public.sys_agent_task"
                        " SET budget_used = jsonb_set("
                        "       jsonb_set(jsonb_set(budget_used,"
                        "         '{steps}',"
                        "         (COALESCE(budget_used->>'steps','0')::int"
                        "         + :st)::text::jsonb),"
                        "       '{total_tokens}',"
                        "         (COALESCE(budget_used->>'total_tokens','0')"
                        "         ::int + :tk)::text::jsonb),"
                        "     '{elapsed_ms}',"
                        "         (COALESCE(budget_used->>'elapsed_ms','0')"
                        "         ::int + :el)::text::jsonb),"
                        "     updated_at = now()"
                        " WHERE id = CAST(:i AS uuid)"
                        " RETURNING budget_used"
                    ),
                    {"i": task_id, "st": steps, "tk": tokens, "el": elapsed_ms},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        from typing import cast as _cast

        value: object = row["budget_used"]
        if isinstance(value, str):
            value = json.loads(value)
        return _cast("dict[str, object]", value) if isinstance(value, dict) else {}

    async def append_task_event(self, *, task_id: str, event: dict[str, object]) -> None:
        """任务事件追加(不改状态不 bump 版本;步骤级节奏入时间线用)。"""
        await self._s.execute(
            text(
                "UPDATE public.sys_agent_task"
                " SET events = events || CAST(:ev AS jsonb), updated_at = now()"
                " WHERE id = CAST(:i AS uuid)"
            ),
            {"i": task_id, "ev": _dump([event])},
        )

    async def lock_task(self, *, task_id: str) -> bool:
        """任务行锁(规划/插步等复合写的串行化闸;事务结束释放)。"""
        row = (
            await self._s.execute(
                text("SELECT id FROM public.sys_agent_task WHERE id = CAST(:i AS uuid) FOR UPDATE"),
                {"i": task_id},
            )
        ).scalar_one_or_none()
        return row is not None

    async def get_step(self, *, task_id: str, step_id: str) -> dict[str, Any] | None:
        row = (
            (
                await self._s.execute(
                    text(
                        "SELECT id, task_id, ord, goal, status, require_confirm,"
                        "       evidence, fail_reason, checkpoint, collab_topology,"
                        "       started_at, updated_at"
                        " FROM public.sys_agent_task_step"
                        " WHERE id = CAST(:s AS uuid) AND task_id = CAST(:t AS uuid)"
                    ),
                    {"s": step_id, "t": task_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        return dict(row) if row else None

    async def requeue_running_steps(self, *, task_id: str) -> int:
        """显式恢复时把 RUNNING 孤儿步骤回队 PENDING(恢复语义=重跑被中断步)。
        仅限 resume 主链调用;返回回队步骤数。"""
        result = await self._s.execute(
            text(
                "UPDATE public.sys_agent_task_step"
                " SET status = 'PENDING', updated_at = now()"
                " WHERE task_id = CAST(:t AS uuid) AND status = 'RUNNING'"
                " RETURNING id"
            ),
            {"t": task_id},
        )
        return len(result.fetchall())

    async def cancel_open_steps(self, *, task_id: str) -> int:
        """任务取消时批量关闭非终态步骤(账本收敛:终态任务无执行者,
        残留 PENDING/RUNNING/AWAITING 属失真;已终态步骤的证据不动)。"""
        result = await self._s.execute(
            text(
                "UPDATE public.sys_agent_task_step"
                " SET status = 'CANCELLED', updated_at = now()"
                " WHERE task_id = CAST(:t AS uuid)"
                "   AND status NOT IN ('DONE', 'FAILED', 'SKIPPED', 'CANCELLED')"
                " RETURNING id"
            ),
            {"t": task_id},
        )
        return len(result.fetchall())

    async def pending_confirm_step(self, *, task_id: str, step_id: str) -> dict[str, Any] | None:
        """待审步骤(审批读取侧同步施行 TTL:过期审批不可再决)。"""
        row = (
            (
                await self._s.execute(
                    text(
                        "SELECT id, ord, goal, status, require_confirm"
                        " FROM public.sys_agent_task_step"
                        " WHERE id = CAST(:s AS uuid) AND task_id = CAST(:t AS uuid)"
                        "   AND status = 'AWAITING_APPROVAL' AND require_confirm = true"
                        "   AND updated_at >= now() - make_interval(hours => :ttl)"
                    ),
                    {"s": step_id, "t": task_id, "ttl": m.APPROVAL_TTL_HOURS},
                )
            )
            .mappings()
            .one_or_none()
        )
        return dict(row) if row else None

    async def cancel_open_collab(self, *, task_id: str) -> int:
        """任务取消时收敛开放协作账(在途运行 CANCELLED、在途 worker SKIPPED)。"""
        runs = (
            await self._s.execute(
                text(
                    "UPDATE public.sys_agent_collab_run"
                    " SET status = 'CANCELLED', updated_at = now()"
                    " WHERE task_id = CAST(:t AS uuid) AND status = 'RUNNING' RETURNING id"
                ),
                {"t": task_id},
            )
        ).fetchall()
        if runs:
            await self._s.execute(
                text(
                    "UPDATE public.sys_agent_collab_worker w"
                    " SET status = 'SKIPPED', updated_at = now()"
                    " FROM public.sys_agent_collab_run r"
                    " WHERE w.run_id = r.id AND r.task_id = CAST(:t AS uuid)"
                    "   AND w.status IN ('PENDING','RUNNING')"
                ),
                {"t": task_id},
            )
        return len(runs)
