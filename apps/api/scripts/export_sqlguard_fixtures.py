"""导出 SQL 安全引擎对拍基线(053 任务 2.1:期望集冻结,重复运行 diff 为空)。

数据源 = 既有 sqlfw/datascope 测试资产 + evals gold;期望值由**当前内置实现**
(firewall/rewrite)现算冻结——引擎判定与冻结值逐例比对,移植漂移即红。

用法(apps/api 下):
    uv run python scripts/export_sqlguard_fixtures.py [输出路径]
默认输出 packages/liannu/tests/fixtures/kongming_baseline.json(仓内共享包)。

确定性约定:用例按固定序排列,JSON sort_keys + 稳定缩进,同输入同字节。
"""

import json
import logging
import sys
from pathlib import Path
from typing import Any, cast

# 保证 kongming/tests/evals 可导入(scripts/ 直跑形态)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kongming.datascope import rewrite  # noqa: E402
from kongming.datascope.models import FilterType, ScopeFilter  # noqa: E402
from kongming.sqlfw import firewall  # noqa: E402

DEFAULT_OUT = Path(__file__).resolve().parents[2] / (
    "packages/liannu/tests/fixtures/kongming_baseline.json"
)

# ── 用例源 1:sqlfw 防火墙三闸(与 tests/test_sqlfw_firewall.py 同源)──────

FW_PASS = [
    "SELECT * FROM customer",
    "SELECT c.name, COUNT(r.id) FROM customer c "
    "JOIN rental r ON c.id = r.customer_id GROUP BY c.name",
    "SELECT 1",
    "SELECT * FROM (SELECT * FROM customer) t",
    "SELECT * FROM customer UNION SELECT * FROM customer",
    "WITH top AS (SELECT id FROM film LIMIT 5) SELECT * FROM top",
]

FW_BLOCK = [
    "DELETE FROM customer WHERE id = 1",
    "UPDATE customer SET name = 'x'",
    "INSERT INTO customer VALUES (1)",
    "DROP TABLE customer",
    "CREATE TABLE t (id int)",
    "ALTER TABLE customer ADD COLUMN x int",
    "TRUNCATE TABLE customer",
    "GRANT SELECT ON customer TO public",
    "SELECT 1; DROP TABLE customer",  # 多语句藏 DDL
    "SELECT * INTO t2 FROM customer",
    "WITH s AS (SELECT * FROM customer) SELECT * INTO t2 FROM s",
    "WITH gone AS (DELETE FROM customer RETURNING *) SELECT * FROM gone",
    "SELECT FROM WHERE",
    "",
    "SELECT * FROM goods WHERE name ILIKE '%连弩",  # TokenError 族
    "SELECT 1;",  # 尾分号形态
]

SCOPE_ALLOWED = ["tenant_ita", "public"]
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
    "SELECT * FROM tenant_itb.goods",
    "SELECT * FROM pg_catalog.pg_tables",
    "SELECT * FROM information_schema.columns",
    "SELECT * FROM other_db.tenant_ita.goods",
    'SELECT * FROM "Tenant_Ita".goods',
    "SELECT * FROM goods g JOIN tenant_itb.orders o ON g.id = o.id",
    "SELECT * FROM (SELECT * FROM tenant_itb.goods) t",
]
TWIN_ALLOWED = ["tenant_Sakila", "public"]
TWIN_CASES = [
    'SELECT * FROM "tenant_Sakila".goods',
    "SELECT * FROM tenant_sakila.goods",
    "SELECT * FROM tenant_Sakila.goods",
]

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
    "SELECT * FROM tenant_default.query_history",
    "SELECT * FROM tenant_sakila.table_descriptions",
]

# ── 用例源 2:datascope 改写(与 tests/test_datascope.py 改写层同源)────────

REWRITE_SQLS = [
    "SELECT mark FROM customer",
    "SELECT * FROM customer",
    "SELECT * FROM customer c WHERE c.id > 1",
    "SELECT c.name, COUNT(r.id) FROM customer c "
    "JOIN rental r ON c.id = r.customer_id GROUP BY c.name",
    "SELECT c.name FROM customer c WHERE c.id IN (SELECT customer_id FROM rental WHERE id > 10)",
    "SELECT 1 UNION SELECT 2",  # 取不到主表:改写跳过形态
    "SELECT 1;",
]

# 每条改写用例统一冻两种意图(USER_EQ / DEPT_IN),绑定值固定
REWRITE_FILTERS: list[dict[str, Any]] = [
    {"filter_type": "USER_EQ", "column": "create_by", "values": ["u-1"]},
    {"filter_type": "DEPT_IN", "column": "dept_id", "values": ["d1", "d2"]},
]

# ── 深嵌套受控行为钉样(引擎独有契约,无内置对拍形态;任务 4.2)──────────

DEPTH_CASES: list[dict[str, Any]] = [
    # 子查询嵌套 40 层 > 默认上限 32:预扫描拦截,不进 sqlglot
    {
        "id": "depth_subq_40_default",
        "sql": "SELECT *" + " FROM (" * 40 + "t" + ")" * 40,
        "max_depth": None,  # 默认上限
        "expect_reason_prefix": "DEPTH_EXCEEDED",
    },
    # 括号外深嵌套(60 层函数嵌套,预扫描零深度):显式抬高上限穿过预扫描,
    # 钉解析期 RecursionError 兜底收敛形态(实测 ≥48 层必炸)
    {
        "id": "depth_expr_60_prescan_bypassed",
        "sql": "SELECT " + "abs(" * 60 + "1" + ")" * 60,
        "max_depth": 200,
        "expect_reason_prefix": "DEPTH_EXCEEDED",
    },
]


def _verdict_dict(passed: bool, reason: str | None) -> dict[str, Any]:
    return {"passed": passed, "reason": reason}


def _builtin_case(sql: str, allowed: list[str] | None, with_rewrite: bool) -> dict[str, Any]:
    """单用例:内置实现现算期望(与引擎 replay 的键位约定一致)。"""
    expect: dict[str, Any] = {}
    v = firewall.check(sql)
    expect["check"] = _verdict_dict(v.passed, v.reason)
    if allowed is not None:
        v2 = firewall.check_schema_scope(sql, set(allowed))
        expect["check_schema_scope"] = _verdict_dict(v2.passed, v2.reason)
    v3 = firewall.check_reserved_tables(sql)
    expect["check_reserved_tables"] = _verdict_dict(v3.passed, v3.reason)

    # 主表引用:内置 parse 成功才冻结(键缺省 = 解析门形态,回放方跳过)
    try:
        ref = rewrite.extract_main_table_ref(sql)
        expect["main_table_ref"] = {"ref": list(ref) if ref is not None else None}
    except Exception:
        logging.getLogger(__name__).debug("extract_main_table_ref 异常,跳过冻结: %r", sql[:80])

    # 改写产物:内置成功才冻结该意图(PARSE_ERROR 等由 check 键钉住)
    if with_rewrite:
        apply_out: list[dict[str, Any]] = []
        for flt_spec in REWRITE_FILTERS:
            flt = ScopeFilter(
                type=FilterType(flt_spec["filter_type"]),
                column=flt_spec["column"],
                values=list(flt_spec["values"]),
            )
            try:
                new_sql, params = rewrite.apply_filter(sql, flt)
            except Exception:
                logging.getLogger(__name__).debug(
                    "apply_filter 异常,跳过冻结: %r %s", sql[:80], flt_spec["filter_type"]
                )
                continue
            apply_out.append(
                {
                    "filter_type": flt_spec["filter_type"],
                    "column": flt_spec["column"],
                    "values": flt_spec["values"],
                    "sql": new_sql,
                    "params": params,
                }
            )
        expect["apply_filters"] = apply_out
    return {"sql": sql, "allowed_schemas": allowed, "expect": expect}


def _eval_gold_sqls() -> list[tuple[str, str]]:
    """evals gold SQL(文件名, gold_sql);仅钉 check/reserved(租户假设零依赖)。"""
    import yaml

    evals_dir = Path(__file__).resolve().parent.parent / "evals"
    out: list[tuple[str, str]] = []
    for yml in sorted(evals_dir.glob("*.yaml")):
        data = cast("list[object] | None", yaml.safe_load(yml.read_text(encoding="utf-8")))
        for item in data or []:
            if not isinstance(item, dict):
                continue
            gold = cast("dict[str, object]", item).get("gold_sql")
            if gold:
                out.append((f"eval_{yml.stem}", str(gold)))
    return out


def build() -> dict[str, Any]:
    cases: list[dict[str, Any]] = []

    for i, sql in enumerate(FW_PASS):
        c = _builtin_case(sql, None, with_rewrite=False)
        c["id"] = f"fw_pass_{i:02d}"
        cases.append(c)
    for i, sql in enumerate(FW_BLOCK):
        c = _builtin_case(sql, None, with_rewrite=False)
        c["id"] = f"fw_block_{i:02d}"
        cases.append(c)
    for i, sql in enumerate(SCOPE_PASS):
        c = _builtin_case(sql, SCOPE_ALLOWED, with_rewrite=False)
        c["id"] = f"scope_pass_{i:02d}"
        cases.append(c)
    for i, sql in enumerate(SCOPE_BLOCK):
        c = _builtin_case(sql, SCOPE_ALLOWED, with_rewrite=False)
        c["id"] = f"scope_block_{i:02d}"
        cases.append(c)
    for i, sql in enumerate(TWIN_CASES):
        c = _builtin_case(sql, TWIN_ALLOWED, with_rewrite=False)
        c["id"] = f"scope_twin_{i:02d}"
        cases.append(c)
    for i, sql in enumerate(RESERVED_BLOCK):
        c = _builtin_case(sql, None, with_rewrite=False)
        c["id"] = f"res_block_{i:02d}"
        cases.append(c)
    for i, sql in enumerate(RESERVED_PASS):
        c = _builtin_case(sql, None, with_rewrite=False)
        c["id"] = f"res_pass_{i:02d}"
        cases.append(c)
    for i, sql in enumerate(REWRITE_SQLS):
        c = _builtin_case(sql, None, with_rewrite=True)
        c["id"] = f"rewrite_{i:02d}"
        cases.append(c)
    for prefix, sql in _eval_gold_sqls():
        c = _builtin_case(sql, None, with_rewrite=False)
        c["id"] = f"{prefix}_{len(cases):03d}"
        cases.append(c)

    return {
        "schema_version": 1,
        "generator": "kongming apps/api/scripts/export_sqlguard_fixtures.py",
        "cases": cases,
        "depth_cases": DEPTH_CASES,
    }


def main() -> None:
    # 期望值源强制内置路径:任何模式下冻结的都是「内置实现」的行为,
    # 引擎模式(如 KM_SQLGUARD_MODE=primary)下运行不得改变期望值来源
    from kongming.sqlfw import guard

    guard.configure("builtin")
    out_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT
    data = build()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    n_filters = sum(len(c["expect"].get("apply_filters", [])) for c in data["cases"])
    print(
        f"对拍基线已冻结: {out_path}\n"
        f"  cases={len(data['cases'])} depth_cases={len(data['depth_cases'])} "
        f"apply_filters={n_filters}"
    )


if __name__ == "__main__":
    main()
