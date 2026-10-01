"""system 域持久化端口(typing.Protocol 反转,宪法 Article II)。

service 只依赖本接口;SQLAlchemy 实现在 repository.py。
事务由 repo 自身的 commit/rollback 收口,service 不触碰 Session(零框架)。
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


class SystemRepo(Protocol):
    # ---- 用户 ----

    async def count_users(self, keyword: str) -> int: ...
    async def list_users(self, keyword: str, limit: int, offset: int) -> list[UserListItem]: ...
    async def get_user(self, user_id: str) -> dict[str, object] | None:
        """编辑/删除前的现状读取(含 username/status/dept_id 等)。"""
        ...

    async def username_taken(self, username: str) -> bool:
        """未软删用户占用判定(软删记录不占名,rbac spec)。"""
        ...

    async def insert_user(
        self,
        *,
        user_id: str,
        username: str,
        email: str | None,
        password_hash: str | None,
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
        password_hash: str | None,
    ) -> None:
        """全量字段覆盖(表单全量语义);password_hash None=不改密码。"""
        ...

    async def reset_password(self, user_id: str, password_hash: str) -> None:
        """重置密码并复位首登改密标志(password_changed_at=NULL)。"""
        ...

    async def set_user_status(self, user_id: str, status: str) -> None:
        """专用启停(012):只翻状态列。"""
        ...

    async def soft_delete_user(self, user_id: str) -> bool: ...
    async def rebind_user_roles(self, user_id: str, role_ids: list[str]) -> None: ...
    async def rebind_user_tenant(self, user_id: str, tenant_id: str) -> None:
        """覆盖式单租户绑定(管理面单选;联邦建号同构)。"""
        ...

    # ---- 存在性校验(写路径领域校验的依赖) ----

    async def roles_exist(self, role_ids: list[str]) -> list[str]:
        """返回实际存在的 id 子集(与入参比对即知缺失)。"""
        ...

    async def perms_exist(self, perm_ids: list[str]) -> list[str]: ...
    async def tenant_exists(self, tenant_id: str) -> bool: ...
    async def dept_exists(self, dept_id: str) -> bool: ...
    async def depts_exist(self, dept_ids: list[str]) -> list[str]:
        """返回实际存在的 dept_id 子集(CUSTOM 集合的存在性校验,缺即 UnknownDeptId)。"""
        ...

    # ---- 角色 ----

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

    # ---- 权限点 ----

    async def list_perms(self) -> list[PermItem]: ...

    # ---- 部门 ----

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

    # ---- 租户(只读清单) ----

    async def list_tenants(self) -> list[TenantItem]: ...

    # ---- 操作审计(007 S2:管理面写操作与业务同事务留痕) ----

    async def log_oper(
        self, actor_id: str, action: str, target: str | None, detail: dict[str, object]
    ) -> None: ...

    # ---- 事务 ----

    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...
