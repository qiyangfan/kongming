"""rag 组合根适配器:semantic/team 域仓储 → rag 窄端口(域间经端口反转)。"""

from sqlalchemy.ext.asyncio import AsyncSession


class RelationExpansionAdapter:
    """组合根适配器:rag 域只见 RelationExpansionPort,关系查询走
    请求会话的 semantic 仓储(域间经端口反转,不直连;suggestion_evictor
    同款纪律)。tables 键序列转两段限定名字符串(关系行存储形态)。"""

    def __init__(self, session: AsyncSession) -> None:
        from kongming.semantic.repository import SqlSemanticRepo

        self._repo = SqlSemanticRepo(session)

    async def related_tables(
        self, *, tenant_id: str, datasource_key: str, tables: list[tuple[str, str]]
    ) -> list[dict[str, object]]:
        return await self._repo.relations_touching(
            tenant_id=tenant_id,
            datasource_key=datasource_key,
            tables=[f"{schema}.{table}" for schema, table in tables],
        )


class TeamMembershipAdapter:
    """组合根适配器:rag 域只见 MemberTeamsReader/MemberRoleReader 窄端口,
    成员关系查询走 team 域既有仓储(只读消费,team 域零改动)。
    team_ids_of_user 角色全收(可见性按「所在团队」判,VIEWER 亦在集合)。"""

    def __init__(self, session: AsyncSession) -> None:
        from kongming.team.repository import SqlTeamRepo

        self._repo = SqlTeamRepo(session)

    async def team_ids_of_user(self, user_id: str) -> list[str]:
        return list(await self._repo.role_map(user_id=user_id))

    async def role_of(self, tenant_id: str, team_id: str, user_id: str) -> str | None:
        return await self._repo.get_member_role_in_tenant(
            tenant_id=tenant_id, team_id=team_id, user_id=user_id
        )
