"""SQL 防火墙(sqlglot AST,纵深防御第 4 层)。

原则:AST 白名单而非正则黑名单。Agent 产出的 SQL 必须经此校验,
verdict 落审计表。方言当前 PG-only(datasource spec 为 PG-only),
sqlglot 天然保留多方言升级空间(ADR-001 诚实标注)。
"""

import sqlglot
from sqlglot import exp

# 允许的读语句类型(SELECT 系);任何写/DDL/事务控制一律 BLOCK
_READONLY_ROOTS = (exp.Select, exp.Union, exp.Subquery)


class Verdict:
    def __init__(self, passed: bool, reason: str | None = None) -> None:
        self.passed = passed
        self.reason = reason

    def __repr__(self) -> str:  # 便于测试断言
        return f"Verdict(passed={self.passed}, reason={self.reason!r})"


def check(sql: str) -> Verdict:
    try:
        statements = sqlglot.parse(sql, dialect="postgres")
    except sqlglot.errors.ParseError as e:
        return Verdict(False, f"PARSE_ERROR: {e}")

    if not statements or any(st is None for st in statements):
        return Verdict(False, "EMPTY_STATEMENT")

    for st in statements:
        if not isinstance(st, _READONLY_ROOTS):
            return Verdict(False, f"BLOCKED_ROOT: {type(st).__name__}")
        # 语句内禁止出现任何写/DDL 节点(嵌套也拦)
        for forbidden in (
            exp.Insert, exp.Update, exp.Delete, exp.Merge,
            exp.Create, exp.Drop, exp.Alter, exp.Command,
            exp.Grant, exp.TruncateTable,
        ):
            if st.find(forbidden):
                return Verdict(False, f"BLOCKED_NODE: {forbidden.__name__}")

    return Verdict(True)
