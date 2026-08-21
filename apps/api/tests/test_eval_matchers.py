"""评估匹配器单测(016):严格/宽松双路径纯函数直测(默认门禁,不依赖评估环境)。

ai-eval spec「CI smoke 守护」:匹配器判定逻辑由常规单测守护——异源类型
(gold=asyncpg 原生类型 vs gen=链路 JSON 类型)归一化是 Kongming 相对上游的
新增关键逻辑,重点覆盖。
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from evals.matchers import (
    canon_cell,
    columns_compatible,
    numeric_multiset,
    project_rows,
    result_set_equal,
    result_set_loose_match,
    rows_approx_equal,
)

# ============ canon_cell(异源类型归一化) ============


def test_canon_numeric_rounds_and_converts_decimal() -> None:
    assert canon_cell(Decimal("1.5000004")) == 1.5  # round 1e-6
    assert canon_cell(Decimal("1.5")) == canon_cell(1.5)
    assert canon_cell(3) == 3.0


def test_canon_datetime_isoformat_aligns_json_side() -> None:
    # gold 侧 asyncpg 返回 datetime;gen 侧 JSON 已是 ISO 字符串
    assert canon_cell(datetime(2026, 1, 1, 12, 0, 0)) == "2026-01-01T12:00:00"


def test_canon_uuid_to_str_aligns_json_side() -> None:
    # gold 侧 asyncpg 返回 uuid.UUID;gen 侧经 JSON 已是字符串(R1-B4)
    assert canon_cell(UUID("123e4567-e89b-12d3-a456-426614174000")) == (
        "123e4567-e89b-12d3-a456-426614174000"
    )


def test_canon_nonfinite_maps_none_json_cell口径() -> None:
    assert canon_cell(float("nan")) is None
    assert canon_cell(float("inf")) is None


def test_canon_bool_not_numeric() -> None:
    assert canon_cell(True) is True  # bool 不入数值通道(int 子类陷阱)


# ============ 严格 multiset ============


def test_strict_equal_ignores_order() -> None:
    assert result_set_equal([(1, "a"), (2, "b")], [(2, "b"), (1, "a")])


def test_strict_unequal_on_value_diff() -> None:
    assert not result_set_equal([(1,)], [(2,)])


def test_strict_none_and_str_mix_safe() -> None:
    # None 与 str 混排:排序键不抛 TypeError(上游审查修复形态)
    assert result_set_equal([(None, "a"), ("b", None)], [("b", None), (None, "a")])


# ============ 列等价与投影 ============


def test_columns_compatible_subset_and_case() -> None:
    assert columns_compatible(["id"], ["Name", "ID"])  # gold⊆gen+大小写不敏感
    assert columns_compatible(["a", "b"], ["b", "a"])  # 集合相等,顺序无关
    assert not columns_compatible(["name"], ["id"])  # gold 要求的列 gen 缺


def test_project_rows_reorders_and_subsets() -> None:
    rows = [["x", 1, "y"]]
    # gen 列 [c1, c2, c3],按 gold 列 [c2, c1] 投影重排
    assert project_rows(["c1", "c2", "c3"], rows, ["c2", "c1"]) == [(1, "x")]


def test_project_rows_missing_col_returns_none() -> None:
    assert project_rows(["c1"], [[1]], ["c2"]) is None


# ============ 宽松:路径 1(列等价+投影行近似) ============


def test_loose_projection_passes_when_gen_has_extra_cols() -> None:
    # gold 投影 ID,gen 投影 姓名+ID(语义等价放行)
    gold_cols, gold_rows = ["customer_id"], [(1,), (2,)]
    gen_cols, gen_rows = ["name", "customer_id"], [("a", 1), ("b", 2)]
    assert result_set_loose_match(gold_cols, gold_rows, gen_cols, gen_rows)


def test_loose_float_tolerance_1e_6() -> None:
    gold_rows = [(1.0000001,)]
    gen_rows = [(1.0000002,)]
    assert result_set_loose_match(["v"], gold_rows, ["v"], gen_rows)


def test_loose_missing_required_column_fails() -> None:
    # 生成 SQL 缺 gold 要求的列且数值对不上 → fail
    gold_cols, gold_rows = ["name", "cnt"], [("a", 1)]
    gen_cols, gen_rows = ["name"], [("a",)]
    assert not result_set_loose_match(gold_cols, gold_rows, gen_cols, gen_rows)


def test_loose_ordered_same_values_reordered_still_passes() -> None:
    # 既定语义:宽松分不惩罚序错——值集对时路径 2(数值子集)/纯文本回退
    # (multiset)兜底放行;gold_ordered 仅是路径 1 的先行判定,序错检出
    # 属人工溯源范畴(上游同款宽松哲学,见 evals/README 匹配规则节)
    gold_rows = [(1,), (2,)]
    gen_rows = [(2,), (1,)]
    assert result_set_loose_match(["v"], gold_rows, ["v"], gen_rows, gold_ordered=True)


def test_loose_ordered_fails_when_values_differ() -> None:
    # 值集真的不同(gold 有 3,gen 无)→ 路径 1/2 双挂
    gold_rows = [(1,), (2,), (3,)]
    gen_rows = [(2,), (1,)]
    assert not result_set_loose_match(["v"], gold_rows, ["v"], gen_rows, gold_ordered=True)


def test_loose_ordered_path2_inherently_unordered() -> None:
    # 路径 2(数值子集)天然无序:聚合别名差异(数值全对)+行序错 → 仍放行
    # (保序检验靠路径 1;序错的严格口径差异由严格分兜底记录)
    gold_cols, gold_rows = ["customer_id", "total"], [(1, 100.0), (2, 200.0)]
    gen_cols, gen_rows = ["customer_id", "sum"], [(2, 200.0), (1, 100.0)]
    assert result_set_loose_match(gold_cols, gold_rows, gen_cols, gen_rows, gold_ordered=True)


def test_loose_unordered_when_no_order_by() -> None:
    gold_rows = [(1,), (2,)]
    gen_rows = [(2,), (1,)]
    assert result_set_loose_match(["v"], gold_rows, ["v"], gen_rows, gold_ordered=False)


# ============ 宽松:路径 2(数值子集) ============


def test_loose_numeric_subset_tolerates_alias_diff() -> None:
    # 聚合别名差异(count vs order_count 类):列名不兼容但数值全对 → 放行
    gold_cols, gold_rows = ["customer_id", "total"], [(1, 100.0)]
    gen_cols, gen_rows = ["customer_id", "sum"], [(1, 100.0)]
    assert result_set_loose_match(gold_cols, gold_rows, gen_cols, gen_rows)


def test_loose_numeric_subset_fails_when_gold_id_only_in_gold() -> None:
    # gold 数值型 id 列 gen 缺失(纯 id→name 替换):子集失败属既定语义,
    # 靠编写规范「投影业务可读列」规避(上游 G2 边界)
    gold_rows = [(1, 100.0)]
    gen_rows = [("alice", 100.0)]
    assert not result_set_loose_match(
        ["customer_id", "total"], gold_rows, ["name", "sum"], gen_rows
    )


def test_loose_pure_text_falls_back_to_strict_equality() -> None:
    # 纯文本(无数值):路径 2 恒真误判,回退严格集合等值——列别名不同内容同 → 放行
    gold_rows = [("alice",), ("bob",)]
    gen_rows = [("bob",), ("alice",)]
    assert result_set_loose_match(["客户"], gold_rows, ["name"], gen_rows)


def test_loose_pure_text_content_diff_fails() -> None:
    gold_rows = [("alice",)]
    gen_rows = [("carol",)]
    assert not result_set_loose_match(["客户"], gold_rows, ["name"], gen_rows)


# ============ 异源组合(gold=asyncpg 原生,gen=JSON) ============


def test_loose_heterogeneous_decimal_vs_float_and_datetime() -> None:
    gold_rows = [(Decimal("4.99"), datetime(2026, 8, 21, 9, 30))]
    gen_rows = [[4.99, "2026-08-21T09:30:00"]]
    assert result_set_loose_match(["price", "at"], gold_rows, ["price", "at"], gen_rows)


def test_numeric_multiset_excludes_bool_and_nonfinite() -> None:
    rows = [(1, True, float("nan"))]
    assert numeric_multiset(rows) == [1.0]


def test_rows_approx_equal_heterogeneous_types() -> None:
    assert rows_approx_equal([(1.5,)], [[Decimal("1.5")]], ordered=False)
