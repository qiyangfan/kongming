"""SQL 防火墙测试(宪法 Article III:安全规则测试先行)。

对应 nl2sql spec「SQL 操作权限强制只读」全部场景 + sqlfw 实现的边界。
"""

from kongming.sqlfw.firewall import check

PASS_CASES = [
    "SELECT * FROM customer",
    "SELECT c.name, COUNT(r.id) FROM customer c "
    "JOIN rental r ON c.id = r.customer_id GROUP BY c.name",
    "SELECT 1",
    "SELECT * FROM (SELECT * FROM customer) t",
    "SELECT * FROM customer UNION SELECT * FROM customer",
    "WITH top AS (SELECT id FROM film LIMIT 5) SELECT * FROM top",
]

BLOCK_CASES = [
    # (sql, reason 片段)
    ("DELETE FROM customer WHERE id = 1", "BLOCKED"),
    ("UPDATE customer SET name = 'x'", "BLOCKED"),
    ("INSERT INTO customer VALUES (1)", "BLOCKED"),
    ("DROP TABLE customer", "BLOCKED"),
    ("CREATE TABLE t (id int)", "BLOCKED"),
    ("ALTER TABLE customer ADD COLUMN x int", "BLOCKED"),
    ("TRUNCATE TABLE customer", "BLOCKED"),
    ("GRANT SELECT ON customer TO public", "BLOCKED"),
    # 多语句藏 DDL
    ("SELECT 1; DROP TABLE customer", "BLOCKED"),
    # SELECT INTO 是建表写操作(Select 根也拦)
    ("SELECT * INTO t2 FROM customer", "BLOCKED_INTO"),
    ("WITH s AS (SELECT * FROM customer) SELECT * INTO t2 FROM s", "BLOCKED_INTO"),
    # 数据修改 CTE(PG 支持,AST 内必命中 Delete 节点)
    ("WITH gone AS (DELETE FROM customer RETURNING *) SELECT * FROM gone", "BLOCKED"),
    # 语法错误
    ("SELECT FROM WHERE", "PARSE_ERROR"),
    ("", "EMPTY"),
]


def test_pass_cases() -> None:
    for sql in PASS_CASES:
        v = check(sql)
        assert v.passed, f"{sql!r} 应放行,实际 {v!r}"


def test_block_cases() -> None:
    for sql, reason in BLOCK_CASES:
        v = check(sql)
        assert not v.passed, f"{sql!r} 应拦截,实际 {v!r}"
        assert v.reason is not None and v.reason.startswith(reason), f"{sql!r} 原因 {v.reason}"


# ============ check_schema_scope:schema 维度白名单(sql-execution spec) ============

from kongming.sqlfw.firewall import check_schema_scope  # noqa: E402

_ALLOWED = {"tenant_ita", "public"}

SCOPE_PASS = [
    "SELECT * FROM goods",                                  # 未限定(search_path 约束)
    "SELECT * FROM public.goods",                           # public 放行
    "SELECT * FROM tenant_ita.goods",                       # 本租户
    "SELECT * FROM PUBLIC.goods",                           # 未加引号折叠小写 → public
    "SELECT * FROM Tenant_Ita.goods",                       # 未加引号折叠 → tenant_ita 命中
    'SELECT * FROM "tenant_ita".goods',                     # 加引号精确命中
    "WITH x AS (SELECT 1) SELECT * FROM x",                 # CTE 名引用
    "SELECT * FROM generate_series(1, 5)",                  # 表函数(无名节点)
    "SELECT * FROM tenant_ita.goods g JOIN public.t t ON g.id = t.id",
]

SCOPE_BLOCK = [
    ("SELECT * FROM tenant_itb.goods", "tenant_itb"),       # 跨租户
    ("SELECT * FROM pg_catalog.pg_tables", "pg_catalog"),
    ("SELECT * FROM information_schema.columns", "information_schema"),
    ("SELECT * FROM other_db.tenant_ita.goods", "other_db"),  # 三段式 catalog
    ('SELECT * FROM "Tenant_Ita".goods', "Tenant_Ita"),     # 加引号大小写敏感,不命中
    ("SELECT * FROM goods g JOIN tenant_itb.orders o ON g.id = o.id", "tenant_itb"),
    ("SELECT * FROM (SELECT * FROM tenant_itb.goods) t", "tenant_itb"),  # 子查询内也拦
]


def test_schema_scope_pass_cases() -> None:
    for sql in SCOPE_PASS:
        v = check_schema_scope(sql, _ALLOWED)
        assert v.passed, f"{sql!r} 应放行,实际 {v!r}"


def test_schema_scope_block_cases() -> None:
    for sql, schema in SCOPE_BLOCK:
        v = check_schema_scope(sql, _ALLOWED)
        assert not v.passed, f"{sql!r} 应拦截,实际 {v!r}"
        assert v.reason is not None and schema in v.reason, f"{sql!r} 原因 {v.reason}"


def test_schema_scope_uppercase_tenant_key_no_twin_bypass() -> None:
    """白名单大小写精确:tenant_key 含大写时,未加引号引用被 PG 折叠为小写
    (tenant_Sakila→tenant_sakila),指向小写双胞胎 schema,不得误放行;
    大写 schema 只能加引号精确引用。"""
    allowed = {"tenant_Sakila", "public"}
    assert check_schema_scope('SELECT * FROM "tenant_Sakila".goods', allowed).passed
    assert not check_schema_scope("SELECT * FROM tenant_sakila.goods", allowed).passed
    assert not check_schema_scope("SELECT * FROM tenant_Sakila.goods", allowed).passed


# ============ 保留表拒读(005 评审:public 控制表经问答链路透出的实证防线)============

RESERVED_BLOCK = [
    "SELECT username, password_hash FROM sys_user",           # 裸名(search_path 落 public)
    "SELECT * FROM public.sys_user",                          # 显式 public 限定
    "SELECT * FROM SYS_USER",                                  # 大小写变体(折叠语义)
    "SELECT * FROM chat_message",                              # 对话表
    "SELECT * FROM user_identities",                           # 联邦身份
    "SELECT * FROM alembic_version",                           # 迁移版本
    # 045(039 评审上报):user_memory 归属列 user_id 非 create_by,
    # datascope SELF/DEPT 降级不过滤,保留表拒读是唯一防线
    "SELECT content FROM user_memory",                         # 裸名
    "SELECT * FROM public.user_memory",                        # 显式限定
    "WITH m AS (SELECT content FROM public.user_memory) SELECT * FROM m",  # CTE 遮蔽
    "SELECT * FROM tenant_default.goods g JOIN user_memory u ON true",  # JOIN 夹带
    "SELECT * FROM tenant_default.goods g JOIN sys_user u ON true",  #  JOIN 夹带
    # CTE 遮蔽形态(R2 评审实证绕过):体内限定名引用不得被 CTE 豁免吞掉
    "WITH sys_user AS (SELECT password_hash FROM public.sys_user) SELECT * FROM sys_user",
    "WITH g AS (SELECT password_hash FROM public.sys_user) SELECT * FROM g",
    "WITH sys_x AS (SELECT 1) SELECT * FROM sys_x",            # 保留名 CTE 别名本身即拒
    # rag 知识两表(009 评审 B2:显式 schema 限定名,search_path 管不了)
    "SELECT question, sql_text FROM rag.query_history",
    "SELECT * FROM rag.table_descriptions",
    'SELECT * FROM "rag".query_history',                       # 引号限定形态(小写等价)
    "WITH g AS (SELECT * FROM rag.query_history) SELECT * FROM g",  # CTE 体内限定名
    "SELECT * FROM tenant_default.goods g JOIN rag.table_descriptions t ON true",  # JOIN 夹带
]


def test_reserved_rag_schema_names_not_overblocked() -> None:
    """rag 拒读只按显式 schema 限定名判:通用业务表名(非 rag schema)不误伤——
    query_history 等是常见业务表名,末段名保留会误伤真实业务库(009 D14)。"""
    from kongming.sqlfw.firewall import check_reserved_tables

    for sql in (
        "SELECT * FROM tenant_default.query_history",   # 业务库同名表(显式非 rag schema)
        "SELECT * FROM tenant_sakila.table_descriptions",
    ):
        v = check_reserved_tables(sql)
        assert v.passed, f"{sql!r} 应放行(业务库同名表),实际 {v!r}"


def test_reserved_tables_blocked() -> None:
    from kongming.sqlfw.firewall import check_reserved_tables

    for sql in RESERVED_BLOCK:
        v = check_reserved_tables(sql)
        assert not v.passed, f"{sql!r} 应拦截,实际放行"
        assert v.reason is not None and v.reason.startswith("RESERVED_TABLE")


def test_reserved_tables_pass_cases() -> None:
    from kongming.sqlfw.firewall import check_reserved_tables

    for sql in (
        "SELECT id, name FROM goods",
        "SELECT * FROM tenant_default.goods",
        "WITH g AS (SELECT 1) SELECT * FROM g",          # 非保留 CTE 名引用,放行
        "WITH top5 AS (SELECT id FROM goods LIMIT 5) SELECT * FROM top5",
        "SELECT * FROM generate_series(1, 3)",           # 表函数无名节点
    ):
        v = check_reserved_tables(sql)
        assert v.passed, f"{sql!r} 应放行,实际 {v!r}"


def test_token_error_forms_blocked_as_parse_error() -> None:
    """未闭合引号等触发 sqlglot TokenError(非 ParseError 子类)——
    防火墙两处校验都必须收敛为 PARSE_ERROR 而不是让异常逃逸(005 R1/S1)。"""
    from kongming.sqlfw.firewall import check, check_schema_scope

    bad = "SELECT * FROM goods WHERE name ILIKE '%连弩"  # 未闭合引号 → TokenError
    v1 = check(bad)
    assert not v1.passed and v1.reason is not None and v1.reason.startswith("PARSE_ERROR")
    v2 = check_schema_scope(bad, {"public"})
    assert not v2.passed and v2.reason is not None and v2.reason.startswith("PARSE_ERROR")


def test_forbidden_functions_blocked() -> None:
    """危险函数拒用(R3 侧报实证:pg_read_file 经 Select 根可达服务器文件)。"""
    from kongming.sqlfw.firewall import check

    for sql in (
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT pg_read_binary_file('x')",
        "SELECT * FROM pg_ls_dir('/data')",
        "SELECT pg_sleep(60)",
        "SELECT set_config('log_statement','all',false)",
        "SELECT lo_import('/etc/passwd')",
        "SELECT id FROM goods WHERE name = (SELECT pg_read_file('x'))",  # 嵌套也拦
    ):
        v = check(sql)
        assert not v.passed, f"{sql!r} 应拦截"
        assert v.reason is not None and v.reason.startswith("FORBIDDEN_FUNC"), sql


def test_benign_functions_pass() -> None:
    """常规只读函数不误伤。"""
    from kongming.sqlfw.firewall import check

    for sql in (
        "SELECT count(*), max(price) FROM goods",
        "SELECT date_trunc('month', created_at) FROM goods",
        "SELECT current_setting('search_path')",
        "SELECT * FROM generate_series(1, 3)",
        "SELECT coalesce(name, 'n/a') FROM goods",
    ):
        v = check(sql)
        assert v.passed, f"{sql!r} 应放行,实际 {v!r}"
