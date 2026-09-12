"""team 用例编排(032):CRUD+成员管理+归属变更。零框架(宪法 Article II)。"""

from typing import Any, Protocol

from kongming.team.models import (
    ROLES,
    TEAM_KEY_RE,
    TeamNotFound,
    TeamValidationError,
)
from kongming.team.ports import TeamRepo


class TeamForbiddenError(Exception):
    """403(成员但角色不足/末位 OWNER/目标团队非 OWNER)。"""


class TeamOperLogger(Protocol):
    async def log(self, *, action: str, target: str, detail: dict[str, object]) -> None: ...


class TeamService:
    def __init__(self, repo: TeamRepo, oper: TeamOperLogger | None = None) -> None:
        self._repo = repo
        self._oper = oper

    async def _audit(self, action: str, target: str, detail: dict[str, object]) -> None:
        if self._oper is not None:
            await self._oper.log(action=action, target=target, detail=detail)

    async def create(self, *, tenant_id: str, team_key: str, name: str, created_by: str) -> str:
        if not TEAM_KEY_RE.fullmatch(team_key):
            raise TeamValidationError("team_key 须为小写字母开头的字母/数字/下划线(2-64 位)")
        if not name.strip() or len(name) > 128:
            raise TeamValidationError("名称非空且 ≤128 字符")
        if await self._repo.get_team_by_key(tenant_id=tenant_id, team_key=team_key) is not None:
            raise TeamValidationError("team_key 已存在")
        tid = await self._repo.create_team(
            tenant_id=tenant_id, team_key=team_key, name=name.strip(), created_by=created_by
        )
        await self._repo.upsert_member(team_id=tid, user_id=created_by, role="OWNER")
        await self._audit("team.create", tid, {"team_key": team_key})
        return tid

    async def list_all(self, *, tenant_id: str) -> list[dict[str, Any]]:
        return await self._repo.list_teams(tenant_id=tenant_id)

    async def list_mine(self, *, tenant_id: str, user_id: str) -> list[dict[str, Any]]:
        return await self._repo.list_my_teams(tenant_id=tenant_id, user_id=user_id)

    async def get_with_members(self, *, tenant_id: str, team_id: str) -> dict[str, Any]:
        team = await self._repo.get_team(tenant_id=tenant_id, team_id=team_id)
        if team is None:
            raise TeamNotFound(team_id)
        members = await self._repo.list_members(team_id=team_id)
        return {"team": team, "members": members}

    async def rename(self, *, tenant_id: str, team_id: str, name: str) -> None:
        if not name.strip() or len(name) > 128:
            raise TeamValidationError("名称非空且 ≤128 字符")
        if not await self._repo.update_team(
            tenant_id=tenant_id, team_id=team_id, name=name.strip()
        ):
            raise TeamNotFound(team_id)
        await self._audit("team.update", team_id, {"name": name.strip()})

    async def delete(self, *, tenant_id: str, team_id: str) -> None:
        # 资源回个人(不丢资源),成员连带清
        await self._repo.clear_team_on_resources(team_id=team_id)
        if not await self._repo.delete_team(tenant_id=tenant_id, team_id=team_id):
            raise TeamNotFound(team_id)
        await self._audit("team.delete", team_id, {})

    async def add_member(self, *, tenant_id: str, team_id: str, user_id: str, role: str) -> None:
        if role not in ROLES:
            raise TeamValidationError(f"role 须为 {'/'.join(ROLES)}")
        if await self._repo.get_team(tenant_id=tenant_id, team_id=team_id) is None:
            raise TeamNotFound(team_id)
        # 审查循环 B7:无 FK 的成员表须校验候选真实存在且属当前租户,
        # 否则幽灵行落库且成员列表(JOIN sys_user)永不可见
        if not await self._repo.user_active_in_tenant(tenant_id=tenant_id, user_id=user_id):
            raise TeamValidationError("用户不存在或不属于当前租户")
        await self._repo.upsert_member(team_id=team_id, user_id=user_id, role=role)
        await self._audit("team.member.add", team_id, {"user_id": user_id, "role": role})

    async def change_member_role(
        self, *, tenant_id: str, team_id: str, user_id: str, role: str
    ) -> None:
        if role not in ROLES:
            raise TeamValidationError(f"role 须为 {'/'.join(ROLES)}")
        # R1-S3:团队租户校验(跨租户成员写 404 同形)
        if await self._repo.get_team(tenant_id=tenant_id, team_id=team_id) is None:
            raise TeamNotFound(team_id)
        current = await self._repo.get_member_role(team_id=team_id, user_id=user_id)
        if current is None:
            raise TeamNotFound(user_id)
        # 降级末位 OWNER → 末位保护(spec)
        if current == "OWNER" and role != "OWNER":
            await self._assert_not_last_owner(team_id=team_id)
        await self._repo.upsert_member(team_id=team_id, user_id=user_id, role=role)
        await self._audit("team.member.role", team_id, {"user_id": user_id, "role": role})

    async def remove_member(self, *, tenant_id: str, team_id: str, user_id: str) -> None:
        if await self._repo.get_team(tenant_id=tenant_id, team_id=team_id) is None:
            raise TeamNotFound(team_id)
        current = await self._repo.get_member_role(team_id=team_id, user_id=user_id)
        if current is None:
            raise TeamNotFound(user_id)
        if current == "OWNER":
            await self._assert_not_last_owner(team_id=team_id)
        await self._repo.remove_member(team_id=team_id, user_id=user_id)
        await self._audit("team.member.remove", team_id, {"user_id": user_id})

    async def _assert_not_last_owner(self, *, team_id: str) -> None:
        owners = await self._repo.count_owners(team_id=team_id)
        if owners <= 1:
            raise TeamForbiddenError("团队至少保留一名 OWNER")

    async def attach(
        self,
        *,
        tenant_id: str,
        team_id: str,
        resource_type: str,
        resource_id: str,
        actor_id: str,
        actor_role: str | None,
        resource_creator_id: str,
    ) -> None:
        """归属变更:团队 OWNER,或资源创建者本人(目标团队须其为 OWNER)。"""
        if resource_type not in ("bot", "workflow"):
            raise TeamValidationError("resource_type 须为 bot/workflow")
        if await self._repo.get_team(tenant_id=tenant_id, team_id=team_id) is None:
            raise TeamNotFound(team_id)
        # T1 化简:两个子句逻辑等价(创建者路径必须同时为 OWNER)——
        # 语义=必须目标团队 OWNER(创建者本人无独立豁免,与 spec 字面一致)
        if actor_role != "OWNER":
            raise TeamForbiddenError("仅目标团队 OWNER 可变更归属")
        if not await self._repo.attach_resource(
            resource_type=resource_type,
            resource_id=resource_id,
            team_id=team_id,
            tenant_id=tenant_id,
        ):
            raise TeamNotFound(resource_id)
        await self._audit("team.attach", team_id, {"type": resource_type, "resource": resource_id})

    async def role_map(self, *, user_id: str) -> dict[str, str]:
        return await self._repo.role_map(user_id=user_id)
