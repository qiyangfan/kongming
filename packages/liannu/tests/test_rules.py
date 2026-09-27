"""规则判定测试:与消费方 kongming 内置测试资产同源(一比一移植的自证)。"""

from liannu import parse

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
    ("DELETE FROM customer WHERE id = 1", "BLOCKED"),
    ("UPDATE customer SET name = 'x'", "BLOCKED"),
    ("INSERT INTO customer VALUES (1)", "BLOCKED"),
    ("DROP TABLE customer", "BLOCKED"),
    ("CREATE TABLE t (id int)", "BLOCKED"),
    ("ALTER TABLE customer ADD COLUMN x int", "BLOCKED"),
    ("TRUNCATE TABLE customer", "BLOCKED"),
    ("GRANT SELECT ON customer TO public", "BLOCKED"),
    ("SELECT 1; DROP TABLE customer", "BLOCKED"),  # 多语句藏 DDL
    ("SELECT * INTO t2 FROM customer", "BLOCKED_INTO"),
    ("WITH s AS (SELECT * FROM customer) SELECT * INTO t2 FROM s", "BLOCKED_INTO"),
    ("WITH gone AS (DELETE FROM customer RETURNING *) SELECT * FROM gone", "BLOCKED"),
    ("SELECT FROM WHERE", "PARSE_ERROR"),
    ("", "EMPTY"),
    ("SELECT * FROM goods WHERE name ILIKE '%连弩", "PARSE_ERROR"),  # TokenError 族
]


def test_firewall_pass_cases() -> None:
    for sql in PASS_CASES:
        v = parse(sql).check_firewall()
        assert v.passed, f"{sql!r} 应放行,实际 {v!r}"


def test_firewall_block_cases() -> None:
    for sql, reason in BLOCK_CASES:
        v = parse(sql).check_firewall()
        assert not v.passed, f"{sql!r} 应拦截,实际 {v!r}"
        assert v.reason is not None and v.reason.startswith(reason), f"{sql!r} 原因 {v.reason}"


def test_forbidden_functions() -> None:
    for sql in (
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT pg_read_binary_file('x')",
        "SELECT * FROM pg_ls_dir('/data')",
        "SELECT pg_sleep(60)",
        "SELECT set_config('log_statement','all',false)",
        "SELECT lo_import('/etc/passwd')",
        "SELECT id FROM goods WHERE name = (SELECT pg_read_file('x'))",
    ):
        v = parse(sql).check_firewall()
        assert not v.passed and (v.reason or "").startswith("FORBIDDEN_FUNC"), sql


def test_benign_functions_pass() -> None:
    for sql in (
        "SELECT count(*), max(price) FROM goods",
        "SELECT date_trunc('month', created_at) FROM goods",
        "SELECT current_setting('search_path')",
        "SELECT * FROM generate_series(1, 3)",
        "SELECT coalesce(name, 'n/a') FROM goods",
    ):
        v = parse(sql).check_firewall()
        assert v.passed, f"{sql!r} 应放行,实际 {v!r}"


# ============ schema 作用域 ============

_ALLOWED = {"tenant_ita", "public"}

SCOPE_PASS = [
    "SELECT * FROM goods",
    "SELECT * FROM public.goods",
    "SELECT * FROM tenant_ita.goods",
    "SELECT * FROM PUBLIC.goods",
    "SELECT * FROM Tenant_Ita.goods",
    'SELECT * FROM "tenant_ita".goods',
    "WITH x AS (SELECT 1) SELECT * FROM x",
    "SELECT * FROM generate_series(1, 5)",
    "SELECT * FROM tenant_ita.goods g JOIN public.t t ON g.id = t.id",
]

SCOPE_BLOCK = [
    ("SELECT * FROM tenant_itb.goods", "tenant_itb"),
    ("SELECT * FROM pg_catalog.pg_tables", "pg_catalog"),
    ("SELECT * FROM information_schema.columns", "information_schema"),
    ("SELECT * FROM other_db.tenant_ita.goods", "other_db"),
    ('SELECT * FROM "Tenant_Ita".goods', "Tenant_Ita"),
    ("SELECT * FROM goods g JOIN tenant_itb.orders o ON g.id = o.id", "tenant_itb"),
    ("SELECT * FROM (SELECT * FROM tenant_itb.goods) t", "tenant_itb"),
]


def test_schema_scope_cases() -> None:
    for sql in SCOPE_PASS:
        v = parse(sql).check_schema_scope(_ALLOWED)
        assert v.passed, f"{sql!r} 应放行,实际 {v!r}"
    for sql, schema in SCOPE_BLOCK:
        v = parse(sql).check_schema_scope(_ALLOWED)
        assert not v.passed and schema in (v.reason or ""), f"{sql!r} 原因 {v.reason}"


def test_schema_scope_uppercase_twin_no_bypass() -> None:
    allowed = {"tenant_Sakila", "public"}
    assert parse('SELECT * FROM "tenant_Sakila".goods').check_schema_scope(allowed).passed
    assert not parse("SELECT * FROM tenant_sakila.goods").check_schema_scope(allowed).passed
    assert not parse("SELECT * FROM tenant_Sakila.goods").check_schema_scope(allowed).passed


# ============ 保留表拒读 ============

RESERVED_BLOCK = [
    "SELECT username, password_hash FROM sys_user",
    "SELECT * FROM public.sys_user",
    "SELECT * FROM SYS_USER",
    "SELECT * FROM chat_message",
    "SELECT * FROM user_identities",
    "SELECT * FROM alembic_version",
    "SELECT content FROM user_memory",
    "SELECT * FROM public.user_memory",
    "WITH m AS (SELECT content FROM public.user_memory) SELECT * FROM m",
    "SELECT * FROM tenant_default.goods g JOIN user_memory u ON true",
    "SELECT * FROM tenant_default.goods g JOIN sys_user u ON true",
    "WITH sys_user AS (SELECT password_hash FROM public.sys_user) SELECT * FROM sys_user",
    "WITH g AS (SELECT password_hash FROM public.sys_user) SELECT * FROM g",
    "WITH sys_x AS (SELECT 1) SELECT * FROM sys_x",
    "SELECT question, sql_text FROM rag.query_history",
    "SELECT * FROM rag.table_descriptions",
    'SELECT * FROM "rag".query_history',
    "WITH g AS (SELECT * FROM rag.query_history) SELECT * FROM g",
    "SELECT * FROM tenant_default.goods g JOIN rag.table_descriptions t ON true",
]

RESERVED_PASS = [
    "SELECT id, name FROM goods",
    "SELECT * FROM tenant_default.goods",
    "WITH g AS (SELECT 1) SELECT * FROM g",
    "WITH top5 AS (SELECT id FROM goods LIMIT 5) SELECT * FROM top5",
    "SELECT * FROM generate_series(1, 3)",
    "SELECT * FROM tenant_default.query_history",  # 业务库同名表不误伤(009 D14)
    "SELECT * FROM tenant_sakila.table_descriptions",
]


def test_reserved_tables_cases() -> None:
    for sql in RESERVED_BLOCK:
        v = parse(sql).check_reserved_tables()
        assert not v.passed, f"{sql!r} 应拦截,实际放行"
        assert (v.reason or "").startswith("RESERVED_TABLE")
    for sql in RESERVED_PASS:
        v = parse(sql).check_reserved_tables()
        assert v.passed, f"{sql!r} 应放行,实际 {v!r}"


def test_reserved_list_is_authoritative_members() -> None:
    """067:清单成员收编 skill_/user_prefs(与 api 历史拦截行为对齐)。"""
    from liannu import rules

    assert "skill_" in rules.RESERVED_TABLE_PREFIXES
    assert "user_prefs" in rules.RESERVED_TABLE_NAMES
    assert "sys_" in rules.RESERVED_TABLE_PREFIXES
    assert "chat_" in rules.RESERVED_TABLE_PREFIXES
