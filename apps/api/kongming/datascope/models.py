"""datascope 领域模型:纯 dataclass,不依赖 Web 框架或数据库。

枚举值与 sys_role.data_scope CHECK 约束同源(字符串字面量)。
ScopeFilter 是「过滤意图」:领域只产意图,AST 改写在 rewrite 模块。
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal

DataScope = Literal["ALL", "CUSTOM", "DEPT", "DEPT_AND_BELOW", "SELF"]

# 宽松序(值越小越宽松):与上游 SMALLINT 1~5 的 MIN 语义逐档对应。
# CUSTOM 排第二——宽严取决于角色自定义的部门集合(集合不可枚举,故不落在
# DEPT 与 DEPT_AND_BELOW 之间任意一档);命中它即按集合并集过滤,
# 集合为空则 fail-closed(rbac spec:多角色取最宽松)。
PERMISSIVENESS: dict[str, int] = {
    "ALL": 0,
    "CUSTOM": 1,
    "DEPT_AND_BELOW": 2,
    "DEPT": 3,
    "SELF": 4,
}

COL_CREATE_BY = "create_by"
COL_DEPT_ID = "dept_id"


class FilterType(Enum):
    SKIP = "SKIP"  # 不改写(ALL / 安全降级)
    USER_EQ = "USER_EQ"  # create_by = 用户
    DEPT_IN = "DEPT_IN"  # dept_id IN (部门集;DEPT 系与 CUSTOM 共用同一 IN 语义)
    UNSUPPORTED = "UNSUPPORTED"  # fail-closed(CUSTOM 空集合 / 未知值)


@dataclass(frozen=True)
class ScopeContext:
    """有效 data_scope 上下文(多角色已在调用方收敛为单值)。"""

    data_scope: str
    user_id: str
    # DEPT 系有效部门集(含递归)/ CUSTOM 的角色部门集合并集
    dept_ids: list[str] = field(default_factory=list[str])


@dataclass(frozen=True)
class ScopeFilter:
    type: FilterType
    column: str | None = None
    values: list[str] = field(default_factory=list[str])
    skip_reason: str | None = None


@dataclass(frozen=True)
class AppliedSQL:
    """改写产物:SQL + 绑定参数 + 降级告警(落审计/日志用)。"""

    sql: str
    params: dict[str, object] = field(default_factory=dict[str, object])
    warning: str | None = None


class DataScopeUnsupported(Exception):
    """无法确定行级可见面,fail-closed 拒绝执行(spec 设计决策)。

    触发形态:主体查不到(用户不存在/已删除)、无任何启用角色、`data_scope=CUSTOM`
    但角色未配置部门集合、未知 `data_scope` 值。共同点是无法回答「这个人能看哪些行」,
    此时返回原样 SQL 等于按超管放行——禁止降级为不过滤。
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
