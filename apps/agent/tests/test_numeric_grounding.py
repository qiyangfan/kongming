"""数值纪律:白名单构建、审计剥离、compute 工具纯函数。"""

from __future__ import annotations

import pytest

from kongming_agent.numeric import (
    audit_summarize_output,
    audit_text,
    build_whitelist,
    compute_delta,
    compute_result_values,
    compute_share,
    extract_numbers,
    filter_insights,
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


class TestAuditText:
    def test_strips_non_whitelist_sentence_keeps_others(self) -> None:
        text = "结果由 80 变为 100。环比增长 15%。共 2 行。"
        out, n = audit_text(text, [80.0, 100.0, 2.0])
        assert "15%" not in out
        assert "由 80 变为 100" in out
        assert "共 2 行" in out
        assert n == 1

    def test_zero_violation_verbatim(self) -> None:
        text = "共 10 行,平均 3.5。"
        out, n = audit_text(text, [10.0, 3.5])
        assert out == text
        assert n == 0

    def test_empty_text(self) -> None:
        assert audit_text("", [1.0]) == ("", 0)

    def test_calendar_year_marker_not_data_number(self) -> None:
        # 2024 本身不在白名单,但「2024年」是时间标记,不得整句剥离
        out, n = audit_text("2024年销售额为 100。", [100.0])
        assert "100" in out
        assert n == 0

    def test_scientific_notation_normalized(self) -> None:
        out, n = audit_text("总和 1.5e3。", [1500.0])
        assert n == 0
        assert "1.5e3" in out


class TestFilterInsights:
    def test_trend_requires_three_rows(self) -> None:
        out = filter_insights([{"type": "trend", "text": "逐月上升"}], n_rows=2, whitelist=[])
        assert out == []

    def test_trend_ok_with_three_and_whitelist(self) -> None:
        out = filter_insights(
            [{"type": "trend", "text": "由 80 升至 100"}], n_rows=3, whitelist=[80.0, 100.0]
        )
        assert len(out) == 1

    def test_anomaly_quantitative_needs_whitelist(self) -> None:
        out = filter_insights(
            [{"type": "anomaly", "text": "偏离均值 40%"}], n_rows=5, whitelist=[3.5]
        )
        assert out == []

    def test_max_stripped_when_number_illegal(self) -> None:
        out = filter_insights([{"type": "max", "text": "最多 100"}], n_rows=5, whitelist=[50.0])
        assert out == []


class TestAuditSummarize:
    def test_growth_rate_not_in_whitelist_stripped(self) -> None:
        result = audit_summarize_output(
            "由 80 变为 100。环比增长 25%。",
            [{"type": "trend", "text": "增长 15%"}],
            columns=["y", "v"],
            rows=[[2023, 80], [2024, 100]],
            stats={"rows": 2, "v_max": 100},
        )
        assert "25%" not in result.summary
        assert "80" in result.summary or "100" in result.summary
        assert result.insights == []  # 两期 trend 不产出

    def test_tool_result_allows_growth_phrase(self) -> None:
        result = audit_summarize_output(
            "增长 25%。",
            [],
            rows=[[80], [100]],
            stats={"rows": 2},
            compute_values=[25.0],
        )
        assert result.summary == "增长 25%。"

    def test_structural_constants_not_stripped(self) -> None:
        result = audit_summarize_output(
            "共 10 行,平均 3.5,预览前 5 行。",
            [],
            rows=[[1], [2], [3]],
            stats={"rows": 10, "avg": 3.5},
        )
        assert result.stripped_sentences == 0
        assert "10" in result.summary and "5" in result.summary

    def test_zero_violation_zero_regression(self) -> None:
        summary = "共 2 行,由 80 变为 100。"
        insights = [{"type": "max", "text": "最大 100"}]
        result = audit_summarize_output(
            summary,
            insights,
            rows=[[80], [100]],
            stats={"rows": 2},
        )
        assert result.summary == summary
        assert result.insights == insights


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
