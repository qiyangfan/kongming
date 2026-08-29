"""datascope 领域模型:纯 dataclass,零框架依赖(宪法 Article II)。

枚举值与 sys_role.data_scope CHECK 约束同源(字符串字面量)。
ScopeFilter 是「过滤意图」:领域只产意图,AST 改写在 rewrite 模块。
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal

DataScope = Literal["ALL", "CUSTOM", "DEPT", "DEPT_AND_BELOW", "SELF"]

# 宽松序(值越小越宽松):与上游 SMALLINT 1~5 的 MIN 语义逐档对应。
# CUSTOM 排第二——未实现,命中即 fail-closed(rbac spec:多角色取最宽松)。
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
    SKIP = "SKIP"                    # 不改写(ALL / 安全降级)
    USER_EQ = "USER_EQ"              # create_by = 用户
    DEPT_IN = "DEPT_IN"              # dept_id IN (部门集)
    UNSUPPORTED = "UNSUPPORTED"      # fail-closed(CUSTOM / 未知值)


@dataclass(frozen=True)
class ScopeContext:
    """有效 data_scope 上下文(多角色已在调用方收敛为单值)。"""

    data_scope: str
    user_id: str
    dept_ids: list[str] = field(default_factory=list[str])  # DEPT 系有效部门集(含递归)


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
    """data_scope=CUSTOM(或未知值):fail-closed,不降级(spec 设计决策)。"""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
