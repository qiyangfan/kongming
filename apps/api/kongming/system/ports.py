"""system 域持久化端口,以 Protocol 隔离用例层与具体实现。

service 只依赖本接口;SQLAlchemy 实现在 repository.py。
事务由 repo 自身的 commit/rollback 负责,service 不直接操作 Session。
SystemRepo 按能力簇拆成多个协议再组合,用例仍只依赖 SystemRepo 这一个名字。
"""

from __future__ import annotations

from typing import Protocol

from kongming.system.models import (
    Dept,
    PermItem,
    RoleListItem,
    TenantItem,
    UserListItem,
)


class UserRepoPort(Protocol):
    """用户管理:列表/读写/启停/软删,以及角色与租户绑定。"""

    async def count_users(self, keyword: str) -> int: ...
    async def list_users(self, keyword: str, limit: int, offset: int) -> list[UserListItem]: ...
    async def get_user(self, user_id: str) -> dict[str, object] | None:
        """编辑/删除前的现状读取(含 username/status/dept_id 等)。"""
        ...

    async def insert_user(
        self,
        *,
        user_id: str,
        username: str,
        email: str | None,
        nickname: str,
        status: str,
        dept_id: str | None,
    ) -> None: ...
    async def update_user(
        self,
        user_id: str,
        *,
        email: str | None,
        nickname: str,
        status: str,
        dept_id: str | None,
    ) -> None:
        """全量字段覆盖(表单全量语义)。"""
        ...

    async def set_user_status(self, user_id: str, status: str) -> None:
        """专用启停:只翻状态列。"""
        ...

    async def soft_delete_user(self, user_id: str) -> bool: ...
    async def rebind_user_roles(self, user_id: str, role_ids: list[str]) -> None: ...
    async def rebind_user_tenant(self, user_id: str, tenant_id: str) -> None:
        """覆盖式单租户绑定(管理面单选;联邦建号同构)。"""
        ...


class RoleRepoPort(Protocol):
    """角色管理:列表/读写删,以及权限点与部门集合绑定。"""

    async def roles_exist(self, role_ids: list[str]) -> list[str]:
        """返回实际存在的 id 子集(与入参比对即知缺失)。"""
        ...

    async def list_roles(self) -> list[RoleListItem]: ...
    async def get_role(self, role_id: str) -> dict[str, object] | None: ...
    async def role_key_taken(self, role_key: str) -> bool: ...
    async def role_id_by_key(self, role_key: str) -> str | None: ...
    async def insert_role(
        self,
        *,
        role_id: str,
        role_key: str,
        role_name: str,
        data_scope: str,
        sql_scope: str,
        sort: int,
    ) -> None: ...
    async def update_role(
        self,
        role_id: str,
        *,
        role_name: str,
        data_scope: str,
        sql_scope: str,
        sort: int,
    ) -> None: ...
    async def delete_role(self, role_id: str) -> None:
        """物理删除;级联清理 user_role/role_permission/role_dept 绑定(事务内)。"""
        ...

    async def role_has_active_users(self, role_id: str) -> bool: ...
    async def rebind_role_perms(self, role_id: str, perm_ids: list[str]) -> None: ...
    async def rebind_role_depts(self, role_id: str, dept_ids: list[str]) -> None:
        """CUSTOM 部门集合整包替换(先删后插,与权限绑定覆盖式语义同构)。"""
        ...


class PermRepoPort(Protocol):
    """权限点:只读清单与存在性校验。"""

    async def perms_exist(self, perm_ids: list[str]) -> list[str]: ...
    async def list_perms(self) -> list[PermItem]: ...


class DeptRepoPort(Protocol):
    """部门管理:列表/读写删与引用检查。"""

    async def dept_exists(self, dept_id: str) -> bool: ...
    async def depts_exist(self, dept_ids: list[str]) -> list[str]:
        """返回实际存在的 dept_id 子集(CUSTOM 集合的存在性校验,缺即 UnknownDeptId)。"""
        ...

    async def list_depts(self) -> list[Dept]: ...
    async def get_dept(self, dept_id: str) -> Dept | None: ...
    async def insert_dept(
        self,
        *,
        dept_id: str,
        parent_id: str | None,
        dept_name: str,
        tenant_id: str,
        sort: int,
    ) -> None: ...
    async def update_dept(
        self,
        dept_id: str,
        *,
        parent_id: str | None,
        dept_name: str,
        tenant_id: str,
        sort: int,
        status: str,
    ) -> None: ...
    async def delete_dept(self, dept_id: str) -> None: ...
    async def dept_has_children(self, dept_id: str) -> bool: ...
    async def dept_has_active_users(self, dept_id: str) -> bool: ...


class TenantAuditPort(Protocol):
    """租户清单/存在性,以及管理面写操作的审计留痕(同事务)。"""

    async def list_tenants(self) -> list[TenantItem]: ...
    async def tenant_exists(self, tenant_id: str) -> bool: ...
    async def log_oper(
        self, actor_id: str, action: str, target: str | None, detail: dict[str, object]
    ) -> None: ...


class SystemRepo(
    UserRepoPort,
    RoleRepoPort,
    PermRepoPort,
    DeptRepoPort,
    TenantAuditPort,
    Protocol,
):
    """system 完整端口:上述能力簇的组合,外加事务控制。"""

    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...
