"""以 sqlglot AST 校验 Agent 生成的 PostgreSQL 查询,判定结果写入审计。
规则单点在 `liannu.rules`;执行模式与引擎降级由 `guard.py` 管理。"""

from liannu import rules as _rules
from sqlglot import Expr, exp

from kongming.sqlfw.guard import parse_guarded

# 清单唯一定义在 liannu.rules;此处显式 re-export(消费方引用面零改动)
RESERVED_TABLE_PREFIXES = _rules.RESERVED_TABLE_PREFIXES
RESERVED_TABLE_NAMES = _rules.RESERVED_TABLE_NAMES
RESERVED_SCHEMA_NAMES = _rules.RESERVED_SCHEMA_NAMES
_READONLY_ROOTS = _rules.READONLY_ROOTS
_FORBIDDEN_FUNCS = _rules.FORBIDDEN_FUNCS


class Verdict:
    def __init__(self, passed: bool, reason: str | None = None) -> None:
        self.passed = passed
        self.reason = reason

    def __repr__(self) -> str:  # 便于测试断言
        return f"Verdict(passed={self.passed}, reason={self.reason!r})"


# ── 内置路径规则体(吃共享解析产物,不再自行 parse)──


def check_statements(statements: list[Expr | None]) -> Verdict:
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
            exp.Insert,
            exp.Update,
            exp.Delete,
            exp.Merge,
            exp.Create,
            exp.Drop,
            exp.Alter,
            exp.Command,
            exp.Grant,
            exp.TruncateTable,
        ):
            if st.find(forbidden):
                return Verdict(False, f"BLOCKED_NODE: {forbidden.__name__}")
        # 危险函数拒用(服务器文件/系统操作/资源滥用)
        for func in st.find_all(exp.Func):
            # Anonymous(sqlglot 未收录的函数)名在 .name;已收录类型用 sql_name
            func_name = (
                func.name.lower() if isinstance(func, exp.Anonymous) else func.sql_name().lower()
            )
            if func_name in _FORBIDDEN_FUNCS:
                return Verdict(False, f"FORBIDDEN_FUNC: {func_name}")

    return Verdict(True)


def check_schema_scope_statements(
    statements: list[Expr | None], allowed_schemas: set[str]
) -> Verdict:
    """schema 维度白名单:显式限定的表引用仅允许落在 allowed_schemas 内。

        search_path 只约束未限定名的解析——`SELECT * FROM tenant_b.t` 可跨租户读
    因此，执行前会统一检查所有显式 schema 限定：
        - 未限定(db 为空)与 CTE 名引用:放行(search_path 已约束);
        - 表函数(generate_series 等,无表名):放行;
        - 三段式引用(catalog 非空):拒绝(PG 不支持跨库,出现即异常形态)。
    """
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


def check_reserved_tables_statements(statements: list[Expr | None]) -> Verdict:
    """保留表拒读:任何表引用的末段名命中保留前缀/名即拒绝(不限定 schema 也拦——
    裸名经 search_path 落到 public 控制表,是最危险形态)。
    CTE 别名命中保留名同样拒(否则遮蔽形态绕过:WITH sys_user AS (SELECT FROM
    public.sys_user) SELECT FROM sys_user——PG 里 CTE 名只遮蔽裸名,体内限定名
    引用会被 CTE 豁免误跳过);非保留 CTE 名引用与表函数放行。"""
    for st in statements:
        if st is None:
            continue
        cte_names = {cte.alias.lower() for cte in st.find_all(exp.CTE)}
        for alias in cte_names:
            if alias.startswith(RESERVED_TABLE_PREFIXES) or alias in RESERVED_TABLE_NAMES:
                return Verdict(False, f"RESERVED_TABLE: {alias}")
        for table in st.find_all(exp.Table):
            if not table.name:  # 表函数等无名节点
                continue
            name = table.name.lower()
            if name in cte_names:
                continue  # 非保留 CTE 名引用(保留别名已在上方拒掉)
            # 保留 schema:显式限定名不受 search_path 约束,按 schema 段拒
            if table.db and table.db.lower() in RESERVED_SCHEMA_NAMES:
                return Verdict(False, f"RESERVED_TABLE: {table.db}.{table.name}")
            if name.startswith(RESERVED_TABLE_PREFIXES) or name in RESERVED_TABLE_NAMES:
                return Verdict(False, f"RESERVED_TABLE: {table.name}")
    return Verdict(True)


# ── 公开 API(薄适配:委托 guard 门,模式语义见 guard.py)──────────────


def check(sql: str) -> Verdict:
    return parse_guarded(sql).check()


def check_schema_scope(sql: str, allowed_schemas: set[str]) -> Verdict:
    return parse_guarded(sql).check_schema_scope(allowed_schemas)


def check_reserved_tables(sql: str) -> Verdict:
    return parse_guarded(sql).check_reserved_tables()
