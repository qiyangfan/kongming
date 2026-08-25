"""toolcall 仓储(019):sys_tool_audit / sys_announcement 的 SQL 落地。

两类写路径:
- 执行审计(record):独立短事务(get_sessionmaker),失败仅 error 日志不阻断
  主流程——与 audit 域 SqlAuditWriter 同范式(审计失败绝不影响响应);
- 确认状态推进(mark_* / get_for_update):请求事务内,与公告写入/回执消息
  同事务原子推进(用户动作=业务写,非横切审计)。
"""

import json
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.db import get_sessionmaker, uuid7
from kongming.toolcall.models import (
    PendingConfirm,
    TenantBindingForbidden,
    TenantNotFound,
    ToolAuditEntry,
)

log = logging.getLogger(__name__)

_TRACE_ID_MAX = 64  # sys_tool_audit.trace_id VARCHAR(64)


class ToolAuditWriter:
    """执行审计独立短事务写入(失败仅日志,不阻断主流程)——返回行 id。"""

    async def record(self, entry: ToolAuditEntry) -> str | None:
        trace_id = entry.trace_id
        if len(trace_id) > _TRACE_ID_MAX:
            log.warning("trace_id 超长截断落库: %s", trace_id)
            trace_id = trace_id[:_TRACE_ID_MAX]
        row_id = str(uuid7())  # ADR-004:UUID v7 主键(R1-M1)
        try:
            async with get_sessionmaker()() as s:
                await s.execute(
                    text(
                        "INSERT INTO public.sys_tool_audit"
                        " (id, trace_id, user_id, tenant_id, tool_name, args_digest,"
                        "  args_json, status, error_reason, duration_ms, datasource_key,"
                        "  channel, idempotency_key, expires_at)"
                        " VALUES (:id, :trace_id, :user_id, :tenant_id, :tool_name,"
                        "         :args_digest, CAST(:args_json AS jsonb), :status,"
                        "         :error_reason, :duration_ms, :datasource_key,"
                        "         :channel, :idempotency_key, :expires_at)"
                    ),
                    {
                        "id": row_id,
                        "trace_id": trace_id,
                        "user_id": entry.user_id,
                        "tenant_id": entry.tenant_id,
                        "tool_name": entry.tool_name,
                        "args_digest": entry.args_digest,
                        "args_json": (
                            json.dumps(entry.args_json, ensure_ascii=False)
                            if entry.args_json is not None
                            else None
                        ),
                        "status": entry.status,
                        "error_reason": entry.error_reason,
                        "duration_ms": entry.duration_ms,
                        "datasource_key": entry.datasource_key,
                        "channel": entry.channel,
                        "idempotency_key": entry.idempotency_key,
                        "expires_at": entry.expires_at,
                    },
                )
                await s.commit()
        except Exception:
            # 丢审计是严重事故:error 级暴露,但不阻断主流程(与 sql 审计同口径)
            log.error("工具审计写入失败 trace_id=%s", trace_id, exc_info=True)
            return None
        return row_id


class SqlToolCallRepo:
    """请求 session 上的查询与状态推进(确认流/管理查询/chat 聚合/L1 复校)。"""

    def __init__(self, s: AsyncSession) -> None:
        self._s = s

    async def commit(self) -> None:
        await self._s.commit()

    async def resolve_tenant(self, tenant_key: str) -> str:
        """L1 租户解析(fail-closed;SQL 与 internal/service.resolve_tenant 同源——
        零框架契约下 service 不可 import internal,仓储层各持一份为先例形态)。"""
        row = (
            await self._s.execute(
                text("SELECT id, status FROM public.sys_tenant WHERE tenant_key = :k"),
                {"k": tenant_key},
            )
        ).mappings().one_or_none()
        if row is None or row["status"] != "ACTIVE":
            raise TenantNotFound(tenant_key)
        return str(row["id"])

    async def assert_bound(self, *, user_id: str, tenant_id: str) -> None:
        """L1 用户-租户绑定复校(与 internal/service.assert_user_bound 同源)。"""

        bound = (
            await self._s.execute(
                text(
                    "SELECT 1 FROM public.sys_user_tenant"
                    " WHERE user_id = :u AND tenant_id = :t"
                ),
                {"u": user_id, "t": tenant_id},
            )
        ).scalar_one_or_none()
        if bound is None:
            raise TenantBindingForbidden(f"{user_id} 未绑定租户 {tenant_id}")

    async def count_write_calls(self, trace_id: str, write_tools: list[str]) -> int:
        """同 trace 写工具已有调用数(全状态含挂起/拒绝;020 意图审核计数口径)。
        写工具名集由调用方自注册表给出(仓储不反向依赖注册表语义)。"""
        if not write_tools:
            return 0
        return int(
            (
                await self._s.execute(
                    text(
                        "SELECT count(*) FROM public.sys_tool_audit"
                        " WHERE trace_id = :t AND (tool_name = ANY(:names)"
                        # R1-A1:MCP 写工具(动态注册,无 readOnlyHint 即写)
                        # 不在静态名集——前缀口径并入,意图审核频控对动态
                        # 写面不失效;mcp__ 前缀由 028 命名规则独占
                        # 前缀判定走 left()(实测:LIKE 反斜杠转义在 Python
                        # 字符串层被二级吞掉,ESCAPE 落空静默不命中)
                        " OR left(tool_name, 5) = 'mcp__')"
                    ),
                    {"t": trace_id, "names": write_tools},
                )
            ).scalar_one()
        )

    async def get_for_update(self, confirm_id: str) -> PendingConfirm | None:
        row = (
            await self._s.execute(
                text(
                    "SELECT id, user_id, tenant_id, tool_name, args_json,"
                    "       idempotency_key, expires_at, status"
                    " FROM public.sys_tool_audit WHERE id = :i FOR UPDATE"
                ),
                {"i": confirm_id},
            )
        ).mappings().one_or_none()
        if row is None:
            return None
        return PendingConfirm(
            id=str(row["id"]),
            user_id=str(row["user_id"]),
            tenant_id=str(row["tenant_id"]),
            tool_name=str(row["tool_name"]),
            args=dict(row["args_json"] or {}),
            idempotency_key=row["idempotency_key"],
            expires_at=row["expires_at"],
            status=str(row["status"]),
        )

    async def mark_status(
        self, confirm_id: str, status: str, *, clear_reason: bool = False
    ) -> None:
        """状态推进;clear_reason=True 时清空挂起期 error_reason(R1-S3:
        确认成功后 EXECUTED 行不残留「需用户确认」文案)。

        审查循环 F3:确认终态推进时同步清空 args_json——重放已完成,原文
        (可能含 MCP 动态工具的凭证参数)无保留必要,不留 DB/备份侧泄露面。"""
        await self._s.execute(
            text(
                "UPDATE public.sys_tool_audit SET status = :st,"
                " error_reason = CASE WHEN :clr THEN NULL ELSE error_reason END,"
                " args_json = NULL"
                " WHERE id = :i"
            ),
            {"st": status, "clr": clear_reason, "i": confirm_id},
        )

    async def find_announcement(
        self, *, tenant_id: str, idempotency_key: str
    ) -> str | None:
        """按幂等键反查公告(R1-S1:重复确认幂等回放返回首次执行结果)。"""
        row = (
            await self._s.execute(
                text(
                    "SELECT id FROM public.sys_announcement"
                    " WHERE tenant_id = :t AND idempotency_key = :k"
                ),
                {"t": tenant_id, "k": idempotency_key},
            )
        ).scalar_one_or_none()
        return None if row is None else str(row)

    async def list_pending_by_trace(
        self, trace_id: str
    ) -> list[dict[str, Any]]:
        """chat 响应聚合 pending_tool_calls:本 trace 下仍待确认(未过期)的调用。"""
        rows = (
            await self._s.execute(
                text(
                    "SELECT id, tool_name, args_digest FROM public.sys_tool_audit"
                    " WHERE trace_id = :t AND status = 'PENDING_CONFIRM'"
                    "   AND (expires_at IS NULL OR expires_at > now())"
                    " ORDER BY created_at"
                ),
                {"t": trace_id},
            )
        ).mappings().all()
        return [
            {
                "confirm_id": str(r["id"]),
                "tool_name": str(r["tool_name"]),
                "args_digest": str(r["args_digest"] or ""),
            }
            for r in rows
        ]

    async def insert_announcement(
        self,
        *,
        tenant_id: str,
        title: str,
        content: str,
        publisher_id: str,
        idempotency_key: str,
    ) -> tuple[str, bool]:
        """幂等写入:(tenant_id, idempotency_key) 唯一约束兜底;
        已存在同键行返回 (既有 id, False)——重复执行返回首次结果(spec)。"""
        row_id = str(uuid7())  # ADR-004:UUID v7 主键(R1-M1)
        inserted = (
            await self._s.execute(
                text(
                    "INSERT INTO public.sys_announcement"
                    " (id, tenant_id, title, content, publisher_id, idempotency_key)"
                    " VALUES (:i, :t, :ti, :c, :p, :k)"
                    " ON CONFLICT (tenant_id, idempotency_key) DO NOTHING"
                    " RETURNING id"
                ),
                {
                    "i": row_id,
                    "t": tenant_id,
                    "ti": title,
                    "c": content,
                    "p": publisher_id,
                    "k": idempotency_key,
                },
            )
        ).scalar_one_or_none()
        if inserted is not None:
            return str(inserted), True
        existing = (
            await self._s.execute(
                text(
                    "SELECT id FROM public.sys_announcement"
                    " WHERE tenant_id = :t AND idempotency_key = :k"
                ),
                {"t": tenant_id, "k": idempotency_key},
            )
        ).scalar_one()
        return str(existing), False

    async def page_audit(
        self,
        *,
        page: int,
        size: int,
        tool_name: str | None = None,
        status: str | None = None,
        user_id: str | None = None,  # 行级可见性:非 ALL 角色强制本人(service 已覆盖)
        tenant_id: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        conditions = ["1=1"]
        params: dict[str, Any] = {}
        if tool_name:
            conditions.append("tool_name = :tool_name")
            params["tool_name"] = tool_name
        if status:
            conditions.append("status = :status")
            params["status"] = status
        if user_id:
            conditions.append("user_id = :user_id")
            params["user_id"] = user_id
        if tenant_id:
            conditions.append("tenant_id = :tenant_id")
            params["tenant_id"] = tenant_id
        if start:
            conditions.append("created_at >= :start")
            params["start"] = start
        if end:
            conditions.append("created_at < :end")
            params["end"] = end
        where = " AND ".join(conditions)
        total = (
            await self._s.execute(
                text(f"SELECT count(*) FROM public.sys_tool_audit WHERE {where}"),  # noqa: S608  # 条件片段为常量
                params,
            )
        ).scalar_one()
        rows = (
            await self._s.execute(
                text(
                    "SELECT id, trace_id, user_id, tenant_id, tool_name, args_digest,"  # noqa: S608  # 多行拼接含 f-string(条件片段常量)
                    "       status, error_reason, duration_ms, datasource_key,"
                    "       idempotency_key, expires_at, created_at"
                    f" FROM public.sys_tool_audit WHERE {where}"
                    " ORDER BY created_at DESC, id DESC LIMIT :limit OFFSET :offset"
                ),
                {**params, "limit": size, "offset": (page - 1) * size},
            )
        ).mappings().all()
        return [dict(r) for r in rows], int(total)


async def user_has_all_scope(session: AsyncSession, user_id: str) -> bool:
    """行级可见性判定(与 014 feedback 同口径):角色集含 data_scope=ALL 即 True。"""
    val = (
        await session.execute(
            text(
                "SELECT bool_or(r.data_scope = 'ALL') FROM public.sys_role r"
                " JOIN public.sys_user_role ur ON ur.role_id = r.id"
                " WHERE ur.user_id = :u"
            ),
            {"u": user_id},
        )
    ).scalar_one()
    return bool(val)
