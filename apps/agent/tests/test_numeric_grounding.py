"""数值纪律:可信数值源构建与 compute 工具纯函数。"""

from __future__ import annotations

import pytest

from kongming_agent.numeric import (
    build_whitelist,
    compute_delta,
    compute_result_values,
    compute_share,
    extract_numbers,
    normalize_number,
    precompute_derived_stats,
    value_in_whitelist,
)


class TestNormalizeAndExtract:
    def test_percent_and_thousand(self) -> None:
        assert normalize_number("25%") == 25.0
        assert normalize_number("1,234") == 1234.0
        assert normalize_number("1.5") == 1.5
        assert normalize_number("abc") is None

    def test_extract_marks_percent(self) -> None:
        toks = extract_numbers("增长 25%,共 1,234 件")
        assert len(toks) == 2
        assert toks[0].value == 25.0 and toks[0].is_percent
        assert toks[1].value == 1234.0 and not toks[1].is_percent


class TestWhitelist:
    def test_builds_from_rows_stats_compute_structural(self) -> None:
        wl = build_whitelist(
            columns=["a", "b"],
            rows=[[1, 2.5], ["x", 3]],
            stats={"rows": 2, "a_max": 1, "a_avg": 1.0},
            compute_values=[25.0],
            structural=[2, 5],
        )
        assert set(wl) >= {1.0, 2.5, 3.0, 25.0, 2.0, 5.0}

    def test_one_and_one_point_zero_equivalent(self) -> None:
        assert value_in_whitelist(1, is_percent=False, whitelist=[1.0])
        assert value_in_whitelist(1.0, is_percent=False, whitelist=[1])

    def test_percent_maps_to_ratio(self) -> None:
        assert value_in_whitelist(25, is_percent=True, whitelist=[0.25])
        assert value_in_whitelist(25, is_percent=True, whitelist=[25.0])
        assert not value_in_whitelist(30, is_percent=True, whitelist=[0.25, 25.0])


class TestComputeTools:
    def test_delta_formula(self) -> None:
        out = compute_delta(80, 100)
        assert out["status"] == "executed"
        assert out["delta_pct"] == pytest.approx(25.0)
        assert out["inputs"] == {"base": 80.0, "target": 100.0}

    def test_share_formula(self) -> None:
        out = compute_share(25, 100)
        assert out["status"] == "executed"
        assert out["share"] == pytest.approx(0.25)
        assert out["share_pct"] == pytest.approx(25.0)

    def test_reject_out_of_whitelist(self) -> None:
        bad = compute_share(50, 100, whitelist=[25.0, 100.0])
        assert bad["status"] == "error"
        assert "白名单" in str(bad["reason"])
        assert "share" not in bad

    def test_zero_base_rejected(self) -> None:
        assert compute_delta(0, 1)["status"] == "error"
        assert compute_share(1, 0)["status"] == "error"

    def test_result_values_feed_whitelist(self) -> None:
        vals = compute_result_values({"delta_pct": 25.0, "inputs": {"base": 1}})
        assert vals == [25.0]


class TestPrecomputeDerived:
    def test_no_time_col_no_output(self) -> None:
        assert precompute_derived_stats(["v"], [[1], [2]]) == {}

    def test_time_col_produces_delta_and_share(self) -> None:
        out = precompute_derived_stats(
            ["year", "sales"],
            [[2023, 80], [2024, 100]],
        )
        assert out["sales_delta_pct"] == pytest.approx(25.0)
        assert out["sales_share_pct"] == pytest.approx(100 / 180 * 100)

    def test_numeric_month_sorts_numerically_not_lexically(self) -> None:
        # 月序 9,10,11:若按 str 字典序会把 10,11 排在 9 前,末两行算错
        out = precompute_derived_stats(
            ["month", "sales"],
            [[11, 30], [9, 10], [10, 20]],
        )
        assert out["sales_delta_pct"] == pytest.approx(50.0)  # (30-20)/20
        assert out["sales_share_pct"] == pytest.approx(30 / 60 * 100)


class TestComputeStatsIntegration:
    def test_compute_stats_merges_derived(self) -> None:
        from kongming_agent.graph import nodes

        stats = nodes.compute_stats(
            ["year", "sales"],
            [[2023, 80], [2024, 100]],
        )
        assert stats["sales_max"] == 100
        assert stats["sales_delta_pct"] == pytest.approx(25.0)
