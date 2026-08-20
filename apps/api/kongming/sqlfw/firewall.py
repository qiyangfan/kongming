"""SQL 防火墙(sqlglot AST,纵深防御第 4 层)。

原则:AST 白名单而非正则黑名单。Agent 产出的 SQL 必须经此校验,
verdict 落审计表。方言当前 PG-only(datasource spec 为 PG-only),
sqlglot 天然保留多方言升级空间(ADR-001 诚实标注)。
"""

import sqlglot
from sqlglot import exp

# 允许的读语句类型(SELECT 系);任何写/DDL/事务控制一律 BLOCK
_READONLY_ROOTS = (exp.Select, exp.Union, exp.Subquery)

# 控制面保留表(005 评审实证:public 在 search_path 内,LLM 可猜中表名直读
# sys_user.password_hash / sys_datasource 密文 / chat_message 他人对话)。
# 前缀 + 精确名双形态;按未加引号即折叠小写的 PG 语义,一律小写比对。
# 保留前缀平台级生效:租户 schema 由平台开通,不接受 sys_/chat_ 同名表。
RESERVED_TABLE_PREFIXES = ("sys_", "chat_")
RESERVED_TABLE_NAMES = frozenset({"alembic_version", "user_identities"})

# 保留 schema(009 评审 B2):rag 知识两表含全租户问句/成功 SQL/表结构,
# 显式限定名(rag.query_history)不受 search_path 约束,按表引用的 schema 段拒;
# 两表末段名不进 RESERVED_TABLE_NAMES——query_history 等是常见业务表名,
# 末段名保留会误伤真实业务库(rag 不在任何执行 search_path 内,裸名无通路)。
RESERVED_SCHEMA_NAMES = frozenset({"rag"})

# 危险函数拒用(R3 侧报实证:SELECT pg_read_file(...) 经 Select 根全链路过,
# dev 库 kongming 为 superuser 时可读服务器文件;pg_sleep 属资源滥用)。
# 按 sqlglot 归一化函数名小写比对;只读系统函数(current_setting 等)不在列。
_FORBIDDEN_FUNCS = frozenset(
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





class Verdict:
    def __init__(self, passed: bool, reason: str | None = None) -> None:
        self.passed = passed
        self.reason = reason

    def __repr__(self) -> str:  # 便于测试断言
        return f"Verdict(passed={self.passed}, reason={self.reason!r})"


def check(sql: str) -> Verdict:
    try:
        statements = sqlglot.parse(sql, dialect="postgres")
    except sqlglot.errors.SqlglotError as e:
        # ParseError 与 TokenError(未闭合引号/非法 token)同族,均为语法类拒绝
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
        # 危险函数拒用(服务器文件/系统操作/资源滥用)
        for func in st.find_all(exp.Func):
            # Anonymous(sqlglot 未收录的函数)名在 .name;已收录类型用 sql_name()
            func_name = (
                func.name.lower() if isinstance(func, exp.Anonymous) else func.sql_name().lower()
            )
            if func_name in _FORBIDDEN_FUNCS:
                return Verdict(False, f"FORBIDDEN_FUNC: {func_name}")

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
    except sqlglot.errors.SqlglotError as e:
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




def check_reserved_tables(sql: str) -> Verdict:
    """保留表拒读:任何表引用的末段名命中保留前缀/名即拒绝(不限定 schema 也拦——
    裸名经 search_path 落到 public 控制表,是最危险形态)。
    CTE 别名命中保留名同样拒(否则遮蔽形态绕过:WITH sys_user AS (SELECT FROM
    public.sys_user) SELECT FROM sys_user——PG 里 CTE 名只遮蔽裸名,体内限定名
    引用会被 CTE 豁免误跳过,R2 评审实证);非保留 CTE 名引用与表函数放行。"""
    try:
        statements = sqlglot.parse(sql, dialect="postgres")
    except sqlglot.errors.SqlglotError as e:
        return Verdict(False, f"PARSE_ERROR: {e}")
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
