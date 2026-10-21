"""自动构造单测:规范校验器、并入口径、基线行构造——默认门禁可跑。"""
# pyright: reportUnknownArgumentType=false, reportUnknownVariableType=false

from pathlib import Path

import pytest
import yaml

from evals.construct.merge import (
    MergeError,
    append_cases,
    build_baseline_row,
    load_candidates,
    select_mergeable,
    to_eval_case,
)
from evals.construct.validators import (
    check_projection_readable,
    check_row_cap,
    check_schema_qualified,
    check_tiebreaker,
    validate_candidate,
)

# ── 规范校验器(README 编写规范操作化)──


def test_tiebreaker_missing_rejected() -> None:
    """三类拦截之一:top-N 无第二排序键。"""
    assert check_tiebreaker(
        "SELECT tenant_sakila.f.title FROM tenant_sakila.film f ORDER BY f.rental_rate DESC LIMIT 5"
    )
    ok = "SELECT f.title FROM tenant_sakila.film f ORDER BY f.rental_rate DESC, f.film_id LIMIT 5"
    assert check_tiebreaker(ok) is None


def test_tiebreaker_paren_aware() -> None:
    """括号感知:排序键含函数调用/多参函数时不得漏拦或误判(正则 [^()]
    跨不过括号的历史缺陷回归锁)。"""
    # 函数调用包住唯一排序键 → 仍须拦(旧实现整段失配漏拦)
    assert check_tiebreaker(
        "SELECT f.title FROM tenant_sakila.film f ORDER BY length(f.title) LIMIT 5"
    )
    # 多参函数内逗号不算键分隔:单键 → 拦;追加第二键 → 放行
    single = (
        "SELECT f.film_id FROM tenant_sakila.film f"
        " ORDER BY coalesce(f.description, f.title) LIMIT 5"
    )
    assert check_tiebreaker(single)
    ok = (
        "SELECT f.film_id FROM tenant_sakila.film f"
        " ORDER BY coalesce(f.description, f.title), f.film_id LIMIT 5"
    )
    assert check_tiebreaker(ok) is None
    # 子查询边界:内层 ORDER BY 无 LIMIT 不构成 tiebreaker 义务(不误拦),
    # 外层带 LIMIT 须有第二键
    inner = (
        "SELECT c.name, t.total FROM tenant_sakila.customer c"
        " JOIN (SELECT customer_id, SUM(amount) AS total FROM tenant_sakila.payment"
        " GROUP BY customer_id ORDER BY customer_id) t ON t.customer_id = c.customer_id"
    )
    assert check_tiebreaker(inner)  # 外层无 ORDER BY(保序拦截,既有口径)
    ok_sub = (
        "SELECT * FROM (SELECT customer_id, SUM(amount) AS total FROM tenant_sakila.payment"
        " GROUP BY customer_id) t ORDER BY t.total DESC, t.customer_id LIMIT 10"
    )
    assert check_tiebreaker(ok_sub) is None


def test_tiebreaker_string_literals_masked() -> None:
    """字面量掩码:字面量内 'order by'/'limit' 字样不参与结构判定
    (不产出伪段、不截断真段)。"""
    ok = (
        "SELECT f.title FROM tenant_sakila.film f"
        " WHERE f.title = 'order by the limit of dawn' "
        " ORDER BY f.title, f.film_id LIMIT 5"
    )
    assert check_tiebreaker(ok) is None
    # 字面量触发伪段不再成立:缺第二键的真段仍被拦
    bad = (
        "SELECT f.title FROM tenant_sakila.film f"
        " WHERE f.title = 'order by the limit of dawn' "
        " ORDER BY f.title LIMIT 5"
    )
    assert check_tiebreaker(bad)


def test_inner_orderby_without_outer_ordering_rejected() -> None:
    """规范 5 保守口径:内部含 ORDER BY 但外层不保序。"""
    sql = (
        "SELECT c.name, t.total FROM tenant_sakila.customer c"
        " JOIN (SELECT customer_id, SUM(amount) AS total FROM tenant_sakila.payment"
        " GROUP BY customer_id ORDER BY customer_id) t ON t.customer_id = c.customer_id"
    )
    assert check_tiebreaker(sql)


def test_projection_bare_id_rejected() -> None:
    """三类拦截之二:裸投影 ID。"""
    assert check_projection_readable("SELECT film_id FROM tenant_sakila.film")
    assert check_projection_readable("SELECT order_no FROM tenant_olist.orders")
    # 名称列/多列投影放行
    assert check_projection_readable("SELECT f.title, f.film_id FROM tenant_sakila.film f") is None
    # SELECT * 拦截
    assert check_projection_readable("SELECT * FROM tenant_sakila.film")


def test_schema_qualification_rejected() -> None:
    assert check_schema_qualified("SELECT title FROM film", "tenant_sakila")
    assert check_schema_qualified("SELECT title FROM tenant_sakila.film", "tenant_sakila") is None
    assert check_schema_qualified("SELECT title FROM tenant_olist.orders", "tenant_sakila")


def test_row_cap_rejected() -> None:
    assert check_row_cap(1001)
    assert check_row_cap(1000) is None
    assert check_row_cap(0)  # 空 gold:不可判定题(错误 SQL 返回空集即伪通过)


def test_validate_candidate_collects_all_failures() -> None:
    failures = validate_candidate(
        sql="SELECT id FROM film ORDER BY rental_rate LIMIT 5",
        schema="tenant_sakila",
        row_count=5000,
    )
    assert len(failures) == 4  # tiebreaker/裸ID/schema/行数上限 全中


# ── 并入口径(人审唯一并入路径)──


def test_select_mergeable_rejects_failed_validation() -> None:
    candidates = [
        {"id": "aaa", "question": "q1", "validation": {"ok": True}},
        {"id": "bbb", "question": "q2", "validation": {"ok": False}},
    ]
    from typing import cast

    typed = cast("list[dict[str, object]]", candidates)
    assert [c["id"] for c in select_mergeable(typed, ["aaa"])] == ["aaa"]
    with pytest.raises(MergeError, match="未过自洽验证"):
        select_mergeable(typed, ["bbb"])
    with pytest.raises(MergeError, match="不存在"):
        select_mergeable(typed, ["zzz"])


def test_append_cases_conflict_and_roundtrip(tmp_path: Path) -> None:
    dataset = tmp_path / "sakila.yaml"
    dataset.write_text(
        yaml.dump([{"id": "aaa", "question": "已有题"}], allow_unicode=True), encoding="utf-8"
    )
    assert append_cases(dataset, [{"id": "bbb", "question": "新题"}]) == 1
    loaded = yaml.safe_load(dataset.read_text(encoding="utf-8"))
    assert len(loaded) == 2
    with pytest.raises(MergeError, match="冲突"):
        append_cases(dataset, [{"id": "bbb", "question": "重复扩题"}])


def test_to_eval_case_strips_validation_and_stamps_tenant() -> None:
    case = to_eval_case(
        {
            "id": "abc",
            "question": "q",
            "gold_sql": "SELECT 1",
            "expected_tables": ["s.t"],
            "category": "aggregate",
            "validation": {"ok": True},
        },
        "sakila",
    )
    assert "validation" not in case
    assert case["id"] == "abc"
    # 租户章必须显式携带:候选 id 是内容哈希,评估器按 id 前缀推断租户会 403
    assert case["tenant"] == "sakila"


def test_load_candidates_rejects_non_list(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text("just a string", encoding="utf-8")
    with pytest.raises(MergeError, match="列表"):
        load_candidates(p)


# ── 基线行构造(评估统计行断言)──


def test_build_baseline_row_parses_stats_line() -> None:
    row = build_baseline_row(
        "=== 评估统计: 宽松 50/50 (100%) | 严格 35/50 (70%) | 表命中 48/50 (96%)"
        " | 动态数据源 4/4 | 多轮 12/12 | prompt 指纹 b4e17a09 ===",
        "自动构造扩题(测试)",
    )
    assert "自动构造扩题(测试)" in row
    assert "50/50 (100%)" in row  # 宽松列
    assert "35/50 (70%)" in row  # 严格列
    assert row.startswith("| 20")  # 日期列形态
    assert "b4e17a09" in row


def test_build_baseline_row_missing_segments_placeholder() -> None:
    row = build_baseline_row("=== 评估统计: 无可解析字段 ===", "r")
    assert row.count("—") >= 4  # 缺失段以 — 占位(与历史行「未采集」口径一致)
