"""audit 用例层(015,零框架):审计查询编排(行级可见性强制覆盖)。

行级可见性(audit spec):角色集含 data_scope=ALL → 不限;否则强制本人
(显式注入 vis_user_id,筛选传值被覆盖——与 014 feedback 同一口径)。
"""

from kongming.audit.ports import AuditRepo


class AuditNotFound(Exception):
    """审计记录不存在或数据范围外(详情 403 同形不区分,spec 防越权)。"""


class AuditService:
    def __init__(self, repo: AuditRepo) -> None:
        self._repo = repo

    async def _visibility_user_id(self, actor_id: str) -> str | None:
        if await self._repo.user_has_scope_all(user_id=actor_id):
            return None
        return actor_id

    async def sql_page(
        self,
        *,
        actor_id: str,
        trace_id: str | None,
        user_id: str | None,
        tenant_id: str | None,
        verdict: str | None,
        start_time: object | None,
        end_time: object | None,
        page: int,
        size: int,
    ) -> tuple[int, list[dict[str, object]]]:
        vis = await self._visibility_user_id(actor_id)
        return await self._repo.sql_page(
            vis_user_id=vis,
            trace_id=trace_id,
            user_id=(user_id if vis is None else None),  # userId 仅 ALL 生效(014 同款)
            tenant_id=tenant_id,
            verdict=verdict,
            start_time=start_time,
            end_time=end_time,
            page=page,
            size=size,
        )

    async def sql_detail(self, *, actor_id: str, audit_id: str) -> dict[str, object]:
        row = await self._repo.sql_detail(audit_id=audit_id)
        if row is None:
            raise AuditNotFound(audit_id)
        vis = await self._visibility_user_id(actor_id)
        # 非 ALL 且记录非本人 → 403 同形(spec 单条详情防越权)
        if vis is not None and str(row["user_id"]) != vis:
            raise AuditNotFound(audit_id)
        return row

    async def oper_page(
        self,
        *,
        actor_id: str,
        action: str | None,
        user_id: str | None,
        start_time: object | None,
        end_time: object | None,
        page: int,
        size: int,
    ) -> tuple[int, list[dict[str, object]]]:
        vis = await self._visibility_user_id(actor_id)
        return await self._repo.oper_page(
            vis_user_id=vis,
            action=action,
            user_id=(user_id if vis is None else None),
            start_time=start_time,
            end_time=end_time,
            page=page,
            size=size,
        )
