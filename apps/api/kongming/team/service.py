"""team 用例编排:CRUD、成员管理和归属变更。用例层不依赖 Web 框架或数据库。

团队删除挂知识公共化后置钩子(KnowledgePublicizer,组合根注入 rag 域
适配)——名下知识条目归属置空+审计,知识资产不因团队解散丢失;钩子失败
告警不阻塞删除(FK ON DELETE SET NULL 级联兜底公共化)。
"""

import logging
from typing import Any, Protocol

from kongming.team.models import (
    ROLES,
    TEAM_KEY_RE,
    TeamNotFound,
    TeamValidationError,
)
from kongming.team.ports import TeamRepo

log = logging.getLogger(__name__)


class TeamForbiddenError(Exception):
    """403(成员但角色不足/末位 OWNER/目标团队非 OWNER)。"""


class TeamOperLogger(Protocol):
    async def log(self, *, action: str, target: str, detail: dict[str, object]) -> None: ...


class KnowledgePublicizer(Protocol):
    """团队删除后置钩子:其名下知识条目公共化(team_id 置空)+审计。

    领域只见协议;实现=组合根注入的 rag 域适配(savepoint 包裹,失败只回滚
    自身)。抛错由 TeamService 捕获告警,不阻塞团队删除。"""

    async def publicize(self, *, team_id: str) -> None: ...


class TeamService:
    def __init__(
        self,
        repo: TeamRepo,
        oper: TeamOperLogger | None = None,
        knowledge_publicizer: KnowledgePublicizer | None = None,
    ) -> None:
        self._repo = repo
        self._oper = oper
        self._knowledge_publicizer = knowledge_publicizer

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
        # 租户校验前置:解绑/公共化(Redis 逐出不可回滚)不可先行,
        # 否则跨租户 team_id 会先对它租户缓存发起逐出后才 404
        if await self._repo.get_team(tenant_id=tenant_id, team_id=team_id) is None:
            raise TeamNotFound(team_id)
        # 资源回个人(不丢资源),成员连带清
        await self._repo.clear_team_on_resources(team_id=team_id)
        # 知识条目公共化后置钩子——失败告警不阻塞删除
        # (条目残留按团队不存在=不可见;FK 级联 SET NULL 兜底公共化,不丢资产)
        if self._knowledge_publicizer is not None:
            try:
                await self._knowledge_publicizer.publicize(team_id=team_id)
            except Exception:  # noqa: BLE001  # 钩子失败不阻塞删除
                log.warning(
                    "知识条目公共化钩子失败(团队删除继续,级联兜底公共化): team=%s",
                    team_id,
                    exc_info=True,
                )
        if not await self._repo.delete_team(tenant_id=tenant_id, team_id=team_id):
            raise TeamNotFound(team_id)
        await self._audit("team.delete", team_id, {})

    async def add_member(self, *, tenant_id: str, team_id: str, user_id: str, role: str) -> None:
        if role not in ROLES:
            raise TeamValidationError(f"role 须为 {'/'.join(ROLES)}")
        if await self._repo.get_team(tenant_id=tenant_id, team_id=team_id) is None:
            raise TeamNotFound(team_id)
        # 无 FK 的成员表须校验候选真实存在且属当前租户,
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
        # 团队租户校验(跨租户成员写 404 同形)
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
        # 创建者路径同样要求 OWNER——语义=必须目标团队 OWNER,
        # 创建者本人无独立豁免
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
