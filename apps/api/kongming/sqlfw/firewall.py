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
        # SELECT ... INTO 是建表写操作(PG 专有形态),Select 根节点也要拦
        for sel in st.find_all(exp.Select):
            if sel.args.get("into") is not None:
                return Verdict(False, "BLOCKED_INTO")
        # 语句内禁止出现任何写/DDL 节点(嵌套也拦)
        for forbidden in (
            exp.Insert, exp.Update, exp.Delete, exp.Merge,
            exp.Create, exp.Drop, exp.Alter, exp.Command,
            exp.Grant, exp.TruncateTable,
        ):
            if st.find(forbidden):
                return Verdict(False, f"BLOCKED_NODE: {forbidden.__name__}")

    return Verdict(True)


def check_schema_scope(sql: str, allowed_schemas: set[str]) -> Verdict:
    """schema 维度白名单:显式限定的表引用仅允许落在 allowed_schemas 内。

    search_path 只约束未限定名的解析——`SELECT * FROM tenant_b.t` 可跨租户读
    (评审实证),故在执行前对所有显式 schema 限定做一次收口:
    - 未限定(db 为空)与 CTE 名引用:放行(search_path 已约束);
    - 表函数(generate_series 等,无表名):放行;
    - 三段式引用(catalog 非空):拒绝(PG 不支持跨库,出现即异常形态)。
    """
    try:
        statements = sqlglot.parse(sql, dialect="postgres")
    except sqlglot.errors.ParseError as e:
        return Verdict(False, f"PARSE_ERROR: {e}")
    # PG 语义:仅未加引号的「引用侧」折叠小写;schema 名本身大小写精确,
    # 白名单不折叠(否则大写 tenant_key 会误放行其小写双胞胎 schema)
    for st in statements:
        if st is None:
            continue
        for table in st.find_all(exp.Table):
            if not table.name:  # 表函数等无名节点
                continue
            if table.catalog:
                return Verdict(False, f"CROSS_SCHEMA: {table.catalog}")
            db_id = table.args.get("db")
            if db_id is None:
                continue
            db_name = table.db
            quoted = db_id.args.get("quoted", False) if isinstance(db_id, exp.Identifier) else False
            hit = (db_name in allowed_schemas) if quoted else (db_name.lower() in allowed_schemas)
            if not hit:
                return Verdict(False, f"CROSS_SCHEMA: {db_name}")
    return Verdict(True)
