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
