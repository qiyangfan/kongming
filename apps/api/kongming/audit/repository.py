"""audit 查询仓储(015):sys_sql_audit/sys_oper_log 分页与详情(AuditRepo 端口实现)。

纪律:筛选参数全部 CAST(:x AS ...) 可空绑定(asyncpg None 类型推断坑,014 教训);
倒序 id DESC 时序 tiebreak(与会话列表同一惯例);行级可见性由 vis_user_id
显式注入(audit spec「不依赖通用机制对审计表的列推断」)。
"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def user_has_scope_all(self, *, user_id: str) -> bool:
        v = (
            await self._s.execute(
                text(
                    "SELECT bool_or(r.data_scope = 'ALL') FROM public.sys_role r"
                    " JOIN public.sys_user_role ur ON ur.role_id = r.id"
                    " WHERE ur.user_id = :u"
                ),
                {"u": user_id},
            )
        ).scalar_one_or_none()
        return bool(v)

    # ============ SQL 审计 ============

    _SQL_COLS = (
        "SELECT a.id, a.trace_id, a.user_id, a.tenant_id, a.datasource_key,"
        " a.question, a.sql_text, a.verdict, a.block_reason, a.row_count,"
        " a.duration_ms, a.created_at, u.nickname AS user_nickname,"
        " t.tenant_key"
        " FROM public.sys_sql_audit a"
        " LEFT JOIN public.sys_user u ON u.id = a.user_id"
        " JOIN public.sys_tenant t ON t.id = a.tenant_id"
    )
    _SQL_WHERE = (
        " WHERE (CAST(:vis AS uuid) IS NULL OR a.user_id = CAST(:vis AS uuid))"
        " AND (CAST(:tid AS text) IS NULL OR a.trace_id = CAST(:tid AS text))"
        " AND (CAST(:uid AS uuid) IS NULL OR a.user_id = CAST(:uid AS uuid))"
        " AND (CAST(:tenant AS uuid) IS NULL OR a.tenant_id = CAST(:tenant AS uuid))"
        " AND (CAST(:verdict AS text) IS NULL OR a.verdict = CAST(:verdict AS text))"
        " AND (CAST(:st AS timestamptz) IS NULL OR a.created_at >= CAST(:st AS timestamptz))"
        " AND (CAST(:et AS timestamptz) IS NULL OR a.created_at < CAST(:et AS timestamptz))"
    )

    async def list_sql_by_trace(self, trace_id: str) -> list[dict[str, Any]]:
        """038 观测域:trace 时间线 SQL 事件(时间升序稳定序)。"""
        rows = (
            await self._s.execute(
                text(
                    "SELECT id, verdict, duration_ms, datasource_key, created_at"
                    " FROM public.sys_sql_audit WHERE trace_id = :t"
                    " ORDER BY created_at ASC, id ASC"
                ),
                {"t": trace_id},
            )
        ).mappings().all()
        return [dict(r) for r in rows]

    async def sql_page(
        self,
        *,
        vis_user_id: str | None,
        trace_id: str | None,
        user_id: str | None,
        tenant_id: str | None,
        verdict: str | None,
        start_time: object | None,
        end_time: object | None,
        page: int,
        size: int,
    ) -> tuple[int, list[dict[str, object]]]:
        params = {
            "vis": vis_user_id,
            "tid": trace_id,
            "uid": user_id,
            "tenant": tenant_id,
            "verdict": verdict,
            "st": start_time,
            "et": end_time,
            "lim": size,
            "off": (page - 1) * size,
        }
        total = int(
            (
                await self._s.execute(
                    text("SELECT count(*) FROM public.sys_sql_audit a"  # noqa: S608  # 常量模板,参数化绑定
                         " JOIN public.sys_tenant t ON t.id = a.tenant_id" + self._SQL_WHERE),
                    params,
                )
            ).scalar_one()
        )
        rows = (
            await self._s.execute(
                text(
                    self._SQL_COLS + self._SQL_WHERE  # noqa: S608  # 常量模板,参数化绑定
                    + " ORDER BY a.created_at DESC, a.id DESC LIMIT :lim OFFSET :off"
                ),
                params,
            )
        ).mappings().all()
        return total, [dict(r) for r in rows]

    async def sql_detail(self, *, audit_id: str) -> dict[str, object] | None:
        row = (
            await self._s.execute(
                text(self._SQL_COLS + " WHERE a.id = CAST(:id AS uuid)"),  # noqa: S608  # 常量模板
                {"id": audit_id},
            )
        ).mappings().one_or_none()
        return None if row is None else dict(row)

    # ============ 操作日志 ============

    _OPER_WHERE = (
        " WHERE (CAST(:vis AS uuid) IS NULL OR o.user_id = CAST(:vis AS uuid))"
        " AND (CAST(:action AS text) IS NULL OR o.action = CAST(:action AS text))"
        " AND (CAST(:uid AS uuid) IS NULL OR o.user_id = CAST(:uid AS uuid))"
        " AND (CAST(:st AS timestamptz) IS NULL OR o.created_at >= CAST(:st AS timestamptz))"
        " AND (CAST(:et AS timestamptz) IS NULL OR o.created_at < CAST(:et AS timestamptz))"
    )

    async def oper_page(
        self,
        *,
        vis_user_id: str | None,
        action: str | None,
        user_id: str | None,
        start_time: object | None,
        end_time: object | None,
        page: int,
        size: int,
    ) -> tuple[int, list[dict[str, object]]]:
        params = {
            "vis": vis_user_id,
            "action": action,
            "uid": user_id,
            "st": start_time,
            "et": end_time,
            "lim": size,
            "off": (page - 1) * size,
        }
        total = int(
            (
                await self._s.execute(
                    text("SELECT count(*) FROM public.sys_oper_log o" + self._OPER_WHERE),  # noqa: S608  # 常量模板,参数化绑定
                    params,
                )
            ).scalar_one()
        )
        rows = (
            await self._s.execute(
                text(
                    "SELECT o.id, o.user_id, o.action, o.target, o.detail, o.ip,"  # noqa: S608  # 常量模板,参数化绑定
                    " o.created_at, u.nickname AS user_nickname"
                    " FROM public.sys_oper_log o"
                    " LEFT JOIN public.sys_user u ON u.id = o.user_id"
                    + self._OPER_WHERE
                    + " ORDER BY o.created_at DESC, o.id DESC LIMIT :lim OFFSET :off"
                ),
                params,
            )
        ).mappings().all()
        return total, [dict(r) for r in rows]

    # ============ 事务 ============

    async def commit(self) -> None:
        await self._s.commit()

    async def rollback(self) -> None:
        await self._s.rollback()
