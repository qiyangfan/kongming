"""功能权限查询(auth「权限点校验机制」/rbac「撤销权限立即生效」)。

get_rbac_user 只读账号与有效权限两次:查询范围由替身记录断言,能发现
旧路径(me)多读角色名与租户列表的实现;权限并集取启用角色;账号不可用拒绝。
"""

from dataclasses import dataclass, field

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.models import AuthenticatedUser, RbacUser, TokenInvalid, User
from kongming.auth.service import AuthService


def _user(user_id: str, *, status: str = "ACTIVE", deleted: bool = False) -> User:
    return User(
        id=user_id,
        username="u",
        email=None,
        nickname="u",
        status=status,  # type: ignore[arg-type]
        deleted=deleted,
    )


@dataclass
class _RecordingRepos:
    """记录每个仓储被调用的替身:查询范围断言的基础。"""

    user: User | None
    perm_codes: list[str]
    calls: list[str] = field(default_factory=list[str])

    async def users_get_by_id(self, user_id: str) -> User | None:
        self.calls.append("users.get_by_id")
        return self.user

    async def perms_list_codes(self, user_id: str) -> list[str]:
        self.calls.append("perms.list_codes")
        return self.perm_codes

    async def roles_list_role_keys(self, user_id: str) -> list[str]:
        self.calls.append("roles.list_role_keys")  # 旧路径才会调用
        return ["DEV"]

    async def tenants_list_for_user(self, user_id: str) -> list[object]:
        self.calls.append("tenants.list_for_user")  # 旧路径才会调用
        return []


class _Uow:
    def __init__(self, repos: _RecordingRepos) -> None:
        self._r = repos
        self.users = self
        self.perms = self
        self.roles = self
        self.tenants = self

    async def get_by_id(self, user_id: str) -> User | None:
        return await self._r.users_get_by_id(user_id)

    async def list_codes(self, user_id: str) -> list[str]:
        return await self._r.perms_list_codes(user_id)

    async def list_role_keys(self, user_id: str) -> list[str]:
        return await self._r.roles_list_role_keys(user_id)

    async def list_for_user(self, user_id: str) -> list[object]:
        return await self._r.tenants_list_for_user(user_id)


def _svc(uow: _Uow) -> AuthService:
    return AuthService(
        uow,  # type: ignore[arg-type]
        families=None,  # type: ignore[arg-type]
        states=None,  # type: ignore[arg-type]
        settings=None,  # type: ignore[arg-type]
        registry={},
    )


UID = "01936d7e-8c1b-7cc2-9d4e-5fa1b2c3d4e8"


async def test_reads_only_account_and_perms() -> None:
    repos = _RecordingRepos(user=_user(UID), perm_codes=["chat:execute"])
    rbac = await _svc(_Uow(repos)).get_rbac_user(UID)
    assert isinstance(rbac, RbacUser)
    assert rbac.user_id == UID
    assert rbac.perm_codes == frozenset({"chat:execute"})
    # 只读账号与权限两处;读角色名/租户列表属旧路径的浪费
    assert repos.calls == ["users.get_by_id", "perms.list_codes"]


async def test_unavailable_account_rejected() -> None:
    # 与 me() 同一检查同一异常:TokenInvalid 经全局映射为 401(既有端点行为)
    for u in (
        _user(UID, status="DISABLED"),
        _user(UID, deleted=True),
        None,
    ):
        with pytest.raises(TokenInvalid):
            await _svc(_Uow(_RecordingRepos(user=u, perm_codes=[]))).get_rbac_user(UID)


async def test_rbac_user_unrelated_to_authenticated_user_type() -> None:
    # 两类身份对象职责不同,不混用
    assert AuthenticatedUser(user_id=UID).user_id == UID
    assert RbacUser(user_id=UID, perm_codes=frozenset()).perm_codes == frozenset()


async def test_get_rbac_user_live_union_of_active_roles(session: AsyncSession) -> None:
    """集成:多角色权限取启用角色的并集,停用角色不贡献(与 me() 同一查询)。"""
    from sqlalchemy import text

    from kongming.auth.repository import SqlUnitOfWork

    async def _sql_scalar(q: str, **params: object) -> str:
        return str((await session.execute(text(q), params)).scalar_one())

    uid = await _sql_scalar(
        "INSERT INTO public.sys_user (id, username)"
        " VALUES (gen_random_uuid(), 'rbac_u') RETURNING id"
    )
    role_ids: list[str] = []
    for i, status in enumerate(("ACTIVE", "ACTIVE", "DISABLED")):
        role_ids.append(
            await _sql_scalar(
                "INSERT INTO public.sys_role (id, role_key, role_name, data_scope, status)"
                " VALUES (gen_random_uuid(), :k, :k, 'SELF', :st) RETURNING id",
                k=f"R_RBAC_{i}",
                st=status,
            )
        )
    perm_ids: list[str] = [
        await _sql_scalar(
            "INSERT INTO public.sys_permission (id, perm_code, perm_name, sort)"
            " VALUES (gen_random_uuid(), :c, :c, 0) RETURNING id",
            c=f"rbac:p{i}",
        )
        for i in range(3)
    ]
    # 角色 0/1 → 权限 0/1;停用的角色 2 → 权限 2(不应出现)
    for rid, pid in (
        (role_ids[0], perm_ids[0]),
        (role_ids[1], perm_ids[1]),
        (role_ids[2], perm_ids[2]),
    ):
        await session.execute(
            text("INSERT INTO public.sys_role_permission (role_id, permission_id) VALUES (:r, :p)"),
            {"r": rid, "p": pid},
        )
    for rid in role_ids:
        await session.execute(
            text("INSERT INTO public.sys_user_role (user_id, role_id) VALUES (:u, :r)"),
            {"u": uid, "r": rid},
        )
    await session.commit()

    svc = AuthService(
        SqlUnitOfWork(session),
        families=None,  # type: ignore[arg-type]
        states=None,  # type: ignore[arg-type]
        settings=None,  # type: ignore[arg-type]
        registry={},
    )
    rbac = await svc.get_rbac_user(uid)
    assert rbac.perm_codes == frozenset({"rbac:p0", "rbac:p1"})
