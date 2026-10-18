"""图级检查点与图内挂起的存储层(add-graph-checkpoint)。

职责边界:检查点 blob 对控制面**不透明**(agent 序列化、控制面只存不取,
不理解图框架格式——控制面与图框架解耦,换运行时只换 agent 适配器);
图内确认行是任务域状态机的伴生账本(归属人-only/过期/幂等,与审批门同源纪律)。
SQL 形态与 SqlTaskRepo 同款(text + CAS/谓词返回,原生 uuid 绑定)。
"""

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.db import uuid7


def _dump(obj: object) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


class SqlGraphRepo:
    """图检查点/图内确认的 SQL 存取(内部通道专用,无用户面读路径)。"""

    def __init__(self, s: AsyncSession) -> None:
        self._s = s

    # ── 检查点 blob 存储(不透明) ──

    async def put_checkpoint(
        self,
        *,
        tenant_id: str,
        thread_id: str,
        checkpoint_ns: str,
        kind: str,
        checkpoint_id: str | None,
        task_id: str,
        step_id: str,
        payload_type: str,
        payload: bytes,
    ) -> str:
        row_id = str(uuid7())
        await self._s.execute(
            text(
                "INSERT INTO public.sys_agent_graph_checkpoint"
                " (id, tenant_id, thread_id, checkpoint_ns, kind, checkpoint_id,"
                "  source_task_id, source_step_id, payload_type, payload)"
                " VALUES (:i, CAST(:t AS uuid), :th, :ns, :k, :cid,"
                "         :ti, :si, :pt, :p)"
            ),
            {
                "i": row_id,
                "t": tenant_id,
                "th": thread_id,
                "ns": checkpoint_ns,
                "k": kind,
                "cid": checkpoint_id,
                "ti": task_id,
                "si": step_id,
                "pt": payload_type,
                "p": payload,
            },
        )
        return row_id

    async def latest_checkpoint(
        self, *, tenant_id: str, thread_id: str, checkpoint_ns: str
    ) -> dict[str, Any] | None:
        # id(uuid7)作时间有序 tiebreaker:now() 是事务戳,同请求连写可能同戳,
        # 仅按 created_at 排序在快照行间取序不定
        row = (
            (
                await self._s.execute(
                    text(
                        "SELECT checkpoint_id, payload_type, payload"
                        " FROM public.sys_agent_graph_checkpoint"
                        " WHERE tenant_id = CAST(:t AS uuid) AND thread_id = :th"
                        "   AND checkpoint_ns = :ns AND kind = 'checkpoint'"
                        " ORDER BY created_at DESC, id DESC LIMIT 1"
                    ),
                    {"t": tenant_id, "th": thread_id, "ns": checkpoint_ns},
                )
            )
            .mappings()
            .one_or_none()
        )
        return dict(row) if row else None

    async def checkpoint_writes(
        self,
        *,
        tenant_id: str,
        thread_id: str,
        checkpoint_ns: str,
        checkpoint_id: str,
    ) -> list[dict[str, Any]]:
        rows = await self._s.execute(
            text(
                "SELECT source_task_id, payload_type, payload"
                " FROM public.sys_agent_graph_checkpoint"
                " WHERE tenant_id = CAST(:t AS uuid) AND thread_id = :th"
                "   AND checkpoint_ns = :ns AND kind = 'write'"
                "   AND checkpoint_id = :cid"
                " ORDER BY created_at ASC, id ASC"
            ),
            {"t": tenant_id, "th": thread_id, "ns": checkpoint_ns, "cid": checkpoint_id},
        )
        return [dict(r) for r in rows.mappings().all()]

    async def delete_thread(self, *, tenant_id: str, thread_id: str) -> int:
        result = await self._s.execute(
            text(
                "DELETE FROM public.sys_agent_graph_checkpoint"
                " WHERE tenant_id = CAST(:t AS uuid) AND thread_id = :th"
            ),
            {"t": tenant_id, "th": thread_id},
        )
        return int(result.rowcount or 0)  # type: ignore[attr-defined]  # asyncpg 适配缺标注

    # ── 图内确认账本 ──

    async def insert_graph_confirm(
        self,
        *,
        tenant_id: str,
        task_id: str,
        step_id: str,
        confirm_id: str,
        tool_name: str,
        summary: str,
        thread_id: str,
        ttl_minutes: int,
    ) -> None:
        """插入确认行。并发重入撞 (tenant, confirm_id) 唯一索引时抛完整性错误
        → 端点归一 409,agent 按「已挂起」幂等收口(不做事务内吞异常——
        rollback 会连带丢弃同请求既有写入)。"""
        await self._s.execute(
            text(
                "INSERT INTO public.sys_agent_graph_confirm"
                " (id, tenant_id, task_id, step_id, confirm_id, tool_name,"
                "  summary, thread_id, status, expires_at)"
                " VALUES (:i, CAST(:t AS uuid), CAST(:ti AS uuid), CAST(:si AS uuid),"
                "         :cid, :tn, :sm, :th, 'pending',"
                "         now() + make_interval(mins => :ttl))"
            ),
            {
                "i": str(uuid7()),
                "t": tenant_id,
                "ti": task_id,
                "si": step_id,
                "cid": confirm_id,
                "tn": tool_name,
                "sm": summary,
                "th": thread_id,
                "ttl": ttl_minutes,
            },
        )

    async def get_graph_confirm(self, *, tenant_id: str, confirm_id: str) -> dict[str, Any] | None:
        row = (
            (
                await self._s.execute(
                    text(
                        "SELECT id, tenant_id, task_id, step_id, confirm_id, tool_name,"
                        "       summary, thread_id, status, decided_by, decided_at,"
                        "       expires_at, created_at"
                        " FROM public.sys_agent_graph_confirm"
                        " WHERE tenant_id = CAST(:t AS uuid) AND confirm_id = :cid"
                    ),
                    {"t": tenant_id, "cid": confirm_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        return dict(row) if row else None

    async def latest_decided_confirm_for_step(
        self, *, task_id: str, step_id: str
    ) -> dict[str, Any] | None:
        """恢复提示单源:该步骤最近一条已决确认(批准/拒绝)——批准/拒绝后
        重派时 begin_step 据此携带决策;过期行不携带(过期=步骤跳过语义)。"""
        row = (
            (
                await self._s.execute(
                    text(
                        "SELECT confirm_id, status, tool_name, summary"
                        " FROM public.sys_agent_graph_confirm"
                        " WHERE task_id = CAST(:ti AS uuid) AND step_id = CAST(:si AS uuid)"
                        "   AND status IN ('approved', 'denied')"
                        " ORDER BY decided_at DESC NULLS LAST, created_at DESC"
                        " LIMIT 1"
                    ),
                    {"ti": task_id, "si": step_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        return dict(row) if row else None

    async def decide_graph_confirm(
        self,
        *,
        tenant_id: str,
        confirm_id: str,
        to_status: str,
        decided_by: str,
    ) -> dict[str, Any] | None:
        """CAS 单向迁移 pending→approved/denied(幂等闸);未命中返回 None。"""
        row = (
            (
                await self._s.execute(
                    text(
                        "UPDATE public.sys_agent_graph_confirm"
                        " SET status = :st, decided_by = CAST(:u AS uuid), decided_at = now()"
                        " WHERE tenant_id = CAST(:t AS uuid) AND confirm_id = :cid"
                        "   AND status = 'pending'"
                        " RETURNING task_id, step_id"
                    ),
                    {"t": tenant_id, "cid": confirm_id, "st": to_status, "u": decided_by},
                )
            )
            .mappings()
            .one_or_none()
        )
        return dict(row) if row else None

    async def expire_stale_graph_confirms(self) -> list[dict[str, Any]]:
        """过期扫描:pending 且超时 → expired;对应步骤 AWAITING_APPROVAL →
        SKIPPED(原因=图内确认过期,与任务审批门过期同语义)。返回过期确认行。"""
        expired = (
            (
                await self._s.execute(
                    text(
                        "UPDATE public.sys_agent_graph_confirm"
                        " SET status = 'expired', decided_at = now()"
                        " WHERE status = 'pending' AND expires_at < now()"
                        " RETURNING task_id, step_id, confirm_id"
                    )
                )
            )
            .mappings()
            .all()
        )
        out = [dict(r) for r in expired]
        for r in out:
            await self._s.execute(
                text(
                    "UPDATE public.sys_agent_task_step"
                    " SET status = 'SKIPPED', fail_reason = :fr, updated_at = now()"
                    " WHERE id = CAST(:si AS uuid) AND status = 'AWAITING_APPROVAL'"
                ),
                {"si": str(r["step_id"]), "fr": "图内确认过期,动作跳过"},
            )
        return out

    async def pending_confirms_for_task(self, *, task_id: str) -> list[dict[str, Any]]:
        rows = await self._s.execute(
            text(
                "SELECT confirm_id, step_id, tool_name, summary, status, expires_at, created_at"
                " FROM public.sys_agent_graph_confirm"
                " WHERE task_id = CAST(:ti AS uuid)"
                " ORDER BY created_at DESC LIMIT 50"
            ),
            {"ti": task_id},
        )
        return [dict(r) for r in rows.mappings().all()]

    async def task_event(self, *, task_id: str, event: dict[str, object]) -> None:
        await self._s.execute(
            text(
                "UPDATE public.sys_agent_task"
                " SET events = events || CAST(:ev AS jsonb), updated_at = now()"
                " WHERE id = CAST(:i AS uuid)"
            ),
            {"i": task_id, "ev": _dump([event])},
        )

    @staticmethod
    def now_iso() -> str:
        return datetime.now(UTC).isoformat()
