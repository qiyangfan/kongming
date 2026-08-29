"""system 领域模型与领域错误(007):纯 dataclass,零框架依赖(宪法 Article II)。

枚举字面量与 baseline CHECK 约束同源(字符串字面量,禁 ORM Enum/魔数)。
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

DataScope = Literal["ALL", "CUSTOM", "DEPT_AND_BELOW", "DEPT", "SELF"]
SqlScope = Literal["SELECT_ONLY", "READ_DDL", "DML_DDL"]
UserStatus = Literal["ACTIVE", "DISABLED"]
DeptStatus = Literal["ACTIVE", "DISABLED"]

# 内置角色(种子契约):禁删;role_key 全局创建后不可改(rbac delta 007)
BUILTIN_ROLE_KEYS = ("SUPER_ADMIN", "DEVELOPER")


@dataclass(frozen=True)
class UserListItem:
    """用户列表聚合行(管理面视图:角色键集 + 部门名 + 编辑回显所需 id)。"""

    id: str
    username: str
    nickname: str
    email: str | None
    status: str
    dept_id: str | None
    dept_name: str | None
    tenant_id: str | None
    role_keys: list[str]
    created_at: datetime


@dataclass(frozen=True)
class RoleListItem:
    id: str
    role_key: str
    role_name: str
    data_scope: str
    sql_scope: str
    sort: int
    builtin: bool
    perm_ids: list[str]


@dataclass(frozen=True)
class PermItem:
    id: str
    perm_code: str
    perm_name: str
    sort: int


@dataclass(frozen=True)
class Dept:
    id: str
    parent_id: str | None
    dept_name: str
    tenant_id: str
    sort: int
    status: str


@dataclass(frozen=True)
class DeptNode:
    """部门树节点(全量行加载后内存组树,部门量级小)。"""

    dept: Dept
    children: list["DeptNode"] = field(default_factory=list["DeptNode"])


@dataclass(frozen=True)
class TenantItem:
    id: str
    tenant_key: str
    tenant_name: str


# ---- 领域错误(router 统一映射状态码;message 即用户可见文案) ----


class SystemError(Exception):
    """system 域错误基类。"""


# 404:目标不存在(含已软删)


class UserNotFound(SystemError):
    """用户不存在。"""


class RoleNotFound(SystemError):
    """角色不存在。"""


class DeptNotFound(SystemError):
    """部门不存在。"""


# 409:请求与资源状态冲突


class UsernameTaken(SystemError):
    """用户名已被未软删用户占用。"""


class RoleKeyTaken(SystemError):
    """role_key 已存在。"""


class BuiltinRoleProtected(SystemError):
    """内置角色(SUPER_ADMIN/DEVELOPER)不可删除。"""


class AdminAccountProtected(SystemError):
    """超管账号不可删除。"""


class SelfDeletion(SystemError):
    """不可删除自己的账号。"""


class SelfDisable(SystemError):
    """不可禁用自己的账号。"""


class RoleInUse(SystemError):
    """角色仍绑定未软删用户,不可删除。"""


class DeptInUse(SystemError):
    """部门存在子部门或绑定未软删用户,不可删除。"""


# 400:请求参数与领域规则不符


class RoleKeyImmutable(SystemError):
    """role_key 创建后不可修改。"""


class InvalidDeptParent(SystemError):
    """父部门不可为自身或其后代(防成环)。"""


class UnknownRoleId(SystemError):
    """role_ids 含不存在角色。"""


class UnknownPermId(SystemError):
    """perm_ids 含不存在权限点。"""


class UnknownTenantId(SystemError):
    """tenant_id 不存在。"""


class UnknownDeptId(SystemError):
    """dept_id 不存在。"""
