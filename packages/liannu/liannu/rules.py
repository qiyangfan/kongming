"""防火墙判定规则——保留表/只读根/危险函数清单的**唯一定义处**(067)。

原则:AST 白名单而非正则黑名单。函数签名吃「已解析的语句列表」
(单遍解析产物),不再自行 parse——解析与深度门在 artifact 层。
消费方(kongming sqlfw / agent sqlguard)一律 import 本模块常量,
禁止第二处字面值定义(对象恒等断言守护,见 test_reserved_single_source)。
"""

from sqlglot import exp

from liannu.verdict import Verdict

# 允许的读语句类型(SELECT 系);任何写/DDL/事务控制一律 BLOCK
READONLY_ROOTS = (exp.Select, exp.Union, exp.Subquery)

# 控制面保留表(前缀 + 精确名双形态;按未加引号即折叠小写的 PG 语义,一律小写比对)。
# skill_(056)/user_prefs(059)已收编——与 api 历史实际拦截行为对齐,不放宽不收紧。
RESERVED_TABLE_PREFIXES = ("sys_", "chat_", "skill_", "judge_")
RESERVED_TABLE_NAMES = frozenset(
    {
        "alembic_version",
        "user_identities",
        "user_memory",
        "user_prefs",
        # 092:平台级产出表不设租户语义,任何路径形态按名拒读(知识/执行双闸兜底)
        "report",
        "report_section",
        "report_version",
    }
)

# 保留 schema:显式限定名(rag.query_history)不受 search_path 约束,按 schema 段拒
RESERVED_SCHEMA_NAMES = frozenset({"rag"})

# 危险函数拒用(服务器文件/系统操作/资源滥用);只读系统函数不在列
FORBIDDEN_FUNCS = frozenset(
    {
        "pg_read_file",
        "pg_read_binary_file",
        "pg_ls_dir",
        "pg_ls_logdir",
        "pg_ls_waldir",
        "pg_stat_file",
        "lo_import",
        "lo_export",
        "set_config",
        "pg_sleep",
        "dblink",
        "dblink_connect",
    }
)

# 语句内禁止出现的写/DDL 节点(嵌套也拦)
_FORBIDDEN_NODES = (
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
)


def is_readonly_root(statement: exp.Expression) -> bool:
    """只读根白名单(SELECT/UNION/子查询)。"""
    return isinstance(statement, READONLY_ROOTS)


def check_firewall(statements: list[exp.Expression | None]) -> Verdict:
    """防火墙判定层:只读根白名单 + SELECT INTO + 写/DDL 节点 + 危险函数。"""
    if not statements or any(st is None for st in statements):
        return Verdict(False, "EMPTY_STATEMENT")

    for st in statements:
        if not isinstance(st, READONLY_ROOTS):
            return Verdict(False, f"BLOCKED_ROOT: {type(st).__name__}")
        # SELECT ... INTO 是建表写操作(PG 专有形态),Select 根节点也要拦
        for sel in st.find_all(exp.Select):
            if sel.args.get("into") is not None:
                return Verdict(False, "BLOCKED_INTO")
        # 语句内禁止出现任何写/DDL 节点(嵌套也拦)
        for forbidden in _FORBIDDEN_NODES:
            if st.find(forbidden):
                return Verdict(False, f"BLOCKED_NODE: {forbidden.__name__}")
        # 危险函数拒用(服务器文件/系统操作/资源滥用)
        for func in st.find_all(exp.Func):
            # Anonymous(sqlglot 未收录的函数)名在 .name;已收录类型用 sql_name()
            func_name = (
                func.name.lower() if isinstance(func, exp.Anonymous) else func.sql_name().lower()
            )
            if func_name in FORBIDDEN_FUNCS:
                return Verdict(False, f"FORBIDDEN_FUNC: {func_name}")

    return Verdict(True)


def check_schema_scope(
    statements: list[exp.Expression | None], allowed_schemas: set[str]
) -> Verdict:
    """schema 维度白名单:显式限定的表引用仅允许落在 allowed_schemas 内。

    - 未限定(db 为空)与 CTE 名引用:放行(search_path 已约束);
    - 表函数(generate_series 等,无表名):放行;
    - 三段式引用(catalog 非空):拒绝(PG 不支持跨库,出现即异常形态)。
    PG 语义:仅未加引号的「引用侧」折叠小写;白名单本身不折叠。
    """
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


def check_reserved_tables(statements: list[exp.Expression | None]) -> Verdict:
    """保留表拒读:任何表引用的末段名命中保留前缀/名即拒绝(不限定 schema 也拦)。

    CTE 别名命中保留名同样拒(遮蔽形态绕过防护);非保留 CTE 名引用与
    表函数放行;保留 schema 按显式限定名的 schema 段拒。
    """
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
