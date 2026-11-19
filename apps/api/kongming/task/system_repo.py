"""task 系统巡检仓储:停滞候选、启动清扫与派发代际 SQL。

系统级读写(跨租户、无归属过滤),由看门狗、启动扫描与派发入口专用;
与任务主链的用户/租户过滤口径不同,单独成模块以免口径混用。
"""

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.task import models as m


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


class SqlTaskSystemRepo:
    def __init__(self, s: AsyncSession) -> None:
        self._s = s

    async def list_stale_inflight(self, *, stale_sec: int) -> list[dict[str, Any]]:
        """看门狗候选:RUNNING/PLANNING 且心跳(updated_at)超阈的在途任务。

        心跳即 updated_at:事件账本与状态迁移每次写都 bump 它(宽心跳,
        偏向少误杀,语义精度由判读闸补偿)。附末事件 kind/verdict(判读
        去重与二次超窗的判定依据)与最近 8 条事件尾(判读材料,时间升序
        晚在后,与判读 prompt 口径一致);无归属过滤(系统巡检跨租户,
        与会话只读系统口径一致)。"""
        rows = (
            (
                await self._s.execute(
                    text(
                        "SELECT id, tenant_id, goal, status, dispatch_epoch,"
                        "       events->-1->>'kind' AS last_kind,"
                        "       events->-1->>'verdict' AS last_verdict,"
                        "       COALESCE("
                        "         (SELECT jsonb_agg(e ORDER BY ord ASC)"
                        "            FROM jsonb_array_elements(events)"
                        "              WITH ORDINALITY AS t(e, ord)"
                        "           WHERE ord > jsonb_array_length(events) - 8),"
                        "         '[]'::jsonb) AS events_tail"
                        " FROM public.sys_agent_task"
                        " WHERE status IN ('RUNNING','PLANNING')"
                        "   AND updated_at < now() - make_interval(secs => :stale)"
                    ),
                    {"stale": stale_sec},
                )
            )
            .mappings()
            .all()
        )
        out: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            tail = item.get("events_tail")
            if isinstance(tail, str):
                tail = json.loads(tail)
            item["events_tail"] = tail if isinstance(tail, list) else []
            out.append(item)
        return out

    async def bump_dispatch_epoch(self, *, task_id: str) -> int | None:
        """派发代际原子自增(每次真实派发前;返回新代数,任务不存在返回 None)。
        旧编排循环的回调按代数失配被拦——双执行者竞态的结构性闸门。"""
        row = (
            await self._s.execute(
                text(
                    "UPDATE public.sys_agent_task"
                    " SET dispatch_epoch = dispatch_epoch + 1, updated_at = now()"
                    " WHERE id = CAST(:i AS uuid) RETURNING dispatch_epoch"
                ),
                {"i": task_id},
            )
        ).scalar_one_or_none()
        return int(row) if row is not None else None

    async def mark_interrupted_stale(self) -> int:
        """启动扫描:RUNNING/PLANNING 存量标记 INTERRUPTED + 事件(重启不谎称完成)。
        无归属过滤(跨租户系统启动扫);版本随迁移递增;返回标记行数。"""

        result = await self._s.execute(
            text(
                "UPDATE public.sys_agent_task"
                " SET status = 'INTERRUPTED', version = version + 1,"
                "     events = events || CAST(:ev AS jsonb), updated_at = now()"
                " WHERE status IN ('RUNNING','PLANNING') RETURNING id"
            ),
            {
                "ev": _dump(
                    [
                        {
                            "at": datetime.now(UTC).isoformat(),
                            "kind": "interrupted",
                            "reason": "进程重启,执行中断(显式恢复后继续)",
                        }
                    ]
                )
            },
        )
        return len(result.fetchall())

    async def expire_stale_approvals(self) -> int:
        """启动扫描:挂起审批超过 TTL 的步骤置 SKIPPED(spec 审批过期语义)。
        任务保持 AWAITING_APPROVAL,由用户显式 resume 续跑(resume 守卫放行
        是因步骤已非 AWAITING)。返回跳过步骤数。"""
        result = await self._s.execute(
            text(
                "UPDATE public.sys_agent_task_step"
                " SET status = 'SKIPPED', fail_reason = :fr, updated_at = now()"
                " WHERE status = 'AWAITING_APPROVAL'"
                "   AND updated_at < now() - make_interval(hours => :ttl)"
                " RETURNING id"
            ),
            {"fr": "审批超时,自动跳过", "ttl": m.APPROVAL_TTL_HOURS},
        )
        return len(result.fetchall())
