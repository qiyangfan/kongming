"""team 存储(032):sys_team/sys_team_member;成员集单点查询
(list_visible_ids:用户→(team_id→role) 映射,供列表过滤双支)。"""

from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.uuid7 import uuid7


class SqlTeamRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def create_team(
        self, *, tenant_id: str, team_key: str, name: str, created_by: str
    ) -> str:
        rid = str(uuid7())
        await self._s.execute(
            text(
                "INSERT INTO public.sys_team (id, tenant_id, team_key, name, created_by)"
                " VALUES (:i, CAST(:t AS uuid), :k, :n, CAST(:u AS uuid))"
            ),
            {"i": rid, "t": tenant_id, "k": team_key, "n": name, "u": created_by},
        )
        return rid

    async def get_team(self, *, tenant_id: str, team_id: str) -> dict[str, Any] | None:
        row = (
            (
                await self._s.execute(
                    text(
                        "SELECT * FROM public.sys_team"
                        " WHERE id=CAST(:i AS uuid) AND tenant_id=CAST(:t AS uuid)"
                    ),
                    {"i": team_id, "t": tenant_id},
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    async def get_team_by_key(self, *, tenant_id: str, team_key: str) -> dict[str, Any] | None:
        row = (
            (
                await self._s.execute(
                    text(
                        "SELECT * FROM public.sys_team"
                        " WHERE tenant_id=CAST(:t AS uuid) AND team_key=:k"
                    ),
                    {"t": tenant_id, "k": team_key},
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    async def list_teams(self, *, tenant_id: str) -> list[dict[str, Any]]:
        rows = (
            (
                await self._s.execute(
                    text(
                        "SELECT * FROM public.sys_team WHERE tenant_id=CAST(:t AS uuid)"
                        " ORDER BY created_at DESC, id DESC"
                    ),
                    {"t": tenant_id},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def list_my_teams(self, *, tenant_id: str, user_id: str) -> list[dict[str, Any]]:
        rows = (
            (
                await self._s.execute(
                    text(
                        "SELECT t.* FROM public.sys_team t"
                        " JOIN public.sys_team_member m ON m.team_id=t.id"
                        " WHERE t.tenant_id=CAST(:t AS uuid) AND m.user_id=CAST(:u AS uuid)"
                        " ORDER BY t.created_at DESC, t.id DESC"
                    ),
                    {"t": tenant_id, "u": user_id},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def update_team(self, *, tenant_id: str, team_id: str, name: str) -> bool:
        res = cast(
            "CursorResult[object]",
            await self._s.execute(
                text(
                    "UPDATE public.sys_team SET name=:n"
                    " WHERE id=CAST(:i AS uuid) AND tenant_id=CAST(:t AS uuid)"
                ),
                {"n": name, "i": team_id, "t": tenant_id},
            ),
        )
        return res.rowcount > 0

    async def delete_team(self, *, tenant_id: str, team_id: str) -> bool:
        await self._s.execute(
            text("DELETE FROM public.sys_team_member WHERE team_id=CAST(:i AS uuid)"),
            {"i": team_id},
        )
        res = cast(
            "CursorResult[object]",
            await self._s.execute(
                text(
                    "DELETE FROM public.sys_team"
                    " WHERE id=CAST(:i AS uuid) AND tenant_id=CAST(:t AS uuid)"
                ),
                {"i": team_id, "t": tenant_id},
            ),
        )
        return res.rowcount > 0

    # ── 成员 ──

    async def list_members(self, *, team_id: str) -> list[dict[str, Any]]:
        rows = (
            (
                await self._s.execute(
                    text(
                        "SELECT m.user_id, m.role, m.created_at, u.username"
                        " FROM public.sys_team_member m"
                        " JOIN public.sys_user u ON u.id=m.user_id"
                        " WHERE m.team_id=CAST(:t AS uuid)"
                        " ORDER BY m.created_at ASC, m.user_id ASC"
                    ),
                    {"t": team_id},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def get_member_role(self, *, team_id: str, user_id: str) -> str | None:
        return (
            await self._s.execute(
                text(
                    "SELECT role FROM public.sys_team_member"
                    " WHERE team_id=CAST(:t AS uuid) AND user_id=CAST(:u AS uuid)"
                ),
                {"t": team_id, "u": user_id},
            )
        ).scalar_one_or_none()

    async def user_active_in_tenant(self, *, tenant_id: str, user_id: str) -> bool:
        """成员候选存在性校验(审查循环 B7:sys_team_member 无 FK,不校验会
        落幽灵行且 list_members JOIN sys_user 不可见)。"""
        row = (
            await self._s.execute(
                text(
                    "SELECT 1 FROM public.sys_user_tenant ut"
                    " JOIN public.sys_user u ON u.id = ut.user_id"
                    " WHERE ut.tenant_id = CAST(:t AS uuid)"
                    " AND ut.user_id = CAST(:u AS uuid) AND u.status = 'ACTIVE'"
                ),
                {"t": tenant_id, "u": user_id},
            )
        ).scalar_one_or_none()
        return row is not None

    async def role_map(self, *, user_id: str) -> dict[str, str]:
        """用户→(team_id→role) 映射(列表过滤双支的单点查询,无 N+1)。"""
        rows = (
            await self._s.execute(
                text(
                    "SELECT team_id, role FROM public.sys_team_member"
                    " WHERE user_id=CAST(:u AS uuid)"
                ),
                {"u": user_id},
            )
        ).all()
        return {str(r[0]): str(r[1]) for r in rows}

    async def upsert_member(self, *, team_id: str, user_id: str, role: str) -> None:
        await self._s.execute(
            text(
                "INSERT INTO public.sys_team_member (team_id, user_id, role)"
                " VALUES (CAST(:t AS uuid), CAST(:u AS uuid), :r)"
                " ON CONFLICT (team_id, user_id) DO UPDATE SET role=EXCLUDED.role"
            ),
            {"t": team_id, "u": user_id, "r": role},
        )

    async def remove_member(self, *, team_id: str, user_id: str) -> bool:
        res = cast(
            "CursorResult[object]",
            await self._s.execute(
                text(
                    "DELETE FROM public.sys_team_member"
                    " WHERE team_id=CAST(:t AS uuid) AND user_id=CAST(:u AS uuid)"
                ),
                {"t": team_id, "u": user_id},
            ),
        )
        return res.rowcount > 0

    async def count_owners(self, *, team_id: str) -> int:
        return int(
            (
                await self._s.execute(
                    text(
                        "SELECT count(*) FROM public.sys_team_member"
                        " WHERE team_id=CAST(:t AS uuid) AND role='OWNER'"
                    ),
                    {"t": team_id},
                )
            ).scalar_one()
        )

    # ── 资源归属(三域共用:置 team_id 或回个人) ──

    _RESOURCE_TABLES = {
        "bot": "public.sys_bot_definition",
        "workflow": "public.sys_workflow_def",
    }
    _MEMORY_TABLE = "public.user_memory"

    async def attach_resource(
        self,
        *,
        resource_type: str,
        resource_id: str,
        team_id: str | None,
        tenant_id: str,
    ) -> bool:
        """归属变更;R1-S1:UPDATE 带租户条件(跨租户资源零命中→404)。"""
        table = self._RESOURCE_TABLES[resource_type]
        res = cast(
            "CursorResult[object]",
            await self._s.execute(
                text(
                    f"UPDATE {table} SET team_id=CAST(:t AS uuid)"  # noqa: S608  # 常量表名映射
                    " WHERE id=CAST(:i AS uuid) AND tenant_id=CAST(:tn AS uuid)"
                ),
                {"t": team_id, "i": resource_id, "tn": tenant_id},
            ),
        )
        return res.rowcount > 0

    async def clear_team_on_resources(self, *, team_id: str) -> None:
        """团队删除时资源回个人(team_id 置 NULL,不丢资源);
        R1-T5:团队记忆同回个人(与资源口径一致)。"""
        await self._s.execute(
            text(
                f"UPDATE {self._MEMORY_TABLE} SET team_id=NULL"  # noqa: S608  # 常量
                " WHERE team_id=CAST(:t AS uuid)"
            ),
            {"t": team_id},
        )
        for table in self._RESOURCE_TABLES.values():
            await self._s.execute(
                text(f"UPDATE {table} SET team_id=NULL WHERE team_id=CAST(:t AS uuid)"),  # noqa: S608  # 常量表名映射
                {"t": team_id},
            )

    async def commit(self) -> None:
        await self._s.commit()
