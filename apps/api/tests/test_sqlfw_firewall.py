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
