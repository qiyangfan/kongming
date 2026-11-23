"""BM25 对照统计/判定与资料快照纯函数测试(默认门禁)。"""

from evals.retrieval.bm25_metrics import (
    GroupStat,
    assess,
    build_groups,
    config_issues,
    coverage_issues,
    mean,
    paired_ci,
    percentile,
)
from evals.retrieval.bm25_scope import doc_snapshot, snapshot_diff


def test_percentile_and_mean() -> None:
    assert percentile([10, 20, 30, 40], 50) == 20
    assert percentile([10, 20, 30, 40], 95) == 40
    assert percentile([], 95) == 0.0
    assert mean([1.0, 2.0, 3.0]) == 2.0
    assert mean([]) == 0.0


def test_paired_ci_deterministic_and_bounds_mean() -> None:
    diffs = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
    ci1 = paired_ci(diffs, samples=500, seed=3)
    ci2 = paired_ci(diffs, samples=500, seed=3)
    assert ci1 == ci2
    assert ci1[0] <= mean(diffs) <= ci1[1]
    assert paired_ci([], samples=100) == (0.0, 0.0)


def test_build_groups_aggregates_by_label() -> None:
    rows = [
        {
            "groups": ["overall", "precise"],
            "base_recall": 0.5,
            "exp_recall": 1.0,
            "base_full_hop": 0.0,
            "exp_full_hop": 1.0,
        },
        {
            "groups": ["overall"],
            "base_recall": 1.0,
            "exp_recall": 0.5,
            "base_full_hop": 1.0,
            "exp_full_hop": 0.0,
        },
    ]
    groups = build_groups(rows)
    assert groups["overall"].n == 2
    assert groups["overall"].recall_diff == 0.0
    assert groups["precise"].n == 1 and groups["precise"].recall_diff == 0.5


def test_config_issues_flags_missing_and_invalid() -> None:
    assert config_issues({}) == [
        "缺少运行条件: top_k",
        "缺少运行条件: latency_cap_ms_p95",
        "缺少运行条件: bootstrap_samples",
        "缺少运行条件: bootstrap_seed",
        "缺少运行条件: min_group_counts",
    ]
    ok = {
        "top_k": 5,
        "latency_cap_ms_p95": 3000,
        "bootstrap_samples": 100,
        "bootstrap_seed": 7,
        "min_group_counts": {"precise": 20},
    }
    assert config_issues(ok) == []


def _groups(
    *, precise_diff: float, overall_diff: float = 0.0, multihop_fh_diff: float = 0.0
) -> dict[str, GroupStat]:
    return {
        "precise": GroupStat(0.5, 0.5 + precise_diff, 0.5, 0.5, 10),
        "overall": GroupStat(0.5, 0.5 + overall_diff, 0.5, 0.5, 30),
        "paraphrase": GroupStat(0.5, 0.5, 0.5, 0.5, 10),
        "multihop": GroupStat(0.5, 0.5, 0.5, 0.5 + multihop_fh_diff, 10),
    }


def test_assess_adopt_keep_pending() -> None:
    adopt, why = assess(
        _groups(precise_diff=0.1),
        precise_ci=(0.01, 0.2),
        latency_p95_ms=800,
        latency_cap_ms=3000,
        config_issues_=[],
        scope_ok=True,
    )
    assert adopt == "adopt" and why == []

    keep, why = assess(
        _groups(precise_diff=0.1, overall_diff=-0.1),
        precise_ci=(0.01, 0.2),
        latency_p95_ms=800,
        latency_cap_ms=3000,
        config_issues_=[],
        scope_ok=True,
    )
    assert keep == "keep" and any("overall" in item for item in why)

    pending, why = assess(
        {"overall": _groups(precise_diff=0.1)["overall"]},
        precise_ci=(0.01, 0.2),
        latency_p95_ms=800,
        latency_cap_ms=3000,
        config_issues_=[],
        scope_ok=True,
    )
    assert pending == "pending" and any("precise" in item for item in why)

    pending2, _ = assess(
        _groups(precise_diff=0.1),
        precise_ci=(0.01, 0.2),
        latency_p95_ms=800,
        latency_cap_ms=3000,
        config_issues_=[],
        scope_ok=False,
    )
    assert pending2 == "pending"

    keep2, why2 = assess(
        _groups(precise_diff=0.1),
        precise_ci=(0.01, 0.2),
        latency_p95_ms=5000,
        latency_cap_ms=3000,
        config_issues_=[],
        scope_ok=True,
    )
    assert keep2 == "keep" and any("P95" in item for item in why2)


def test_assess_requires_registered_group_minimums() -> None:
    """登记的分组题量下限参与待验证判定(n=10 < 20)。"""
    verdict, why = assess(
        _groups(precise_diff=0.1),
        precise_ci=(0.01, 0.2),
        latency_p95_ms=800,
        latency_cap_ms=3000,
        config_issues_=[],
        scope_ok=True,
        min_group_counts={"precise": 20, "paraphrase": 20, "multihop": 20},
    )
    assert verdict == "pending" and any("precise" in item for item in why)


def test_doc_snapshot_and_diff() -> None:
    before = doc_snapshot(
        [
            {"doc_name": "a.md", "updated_at": "t1", "block_count": 3},
            {"doc_name": "b.md", "updated_at": "t1", "block_count": 2},
        ]
    )
    assert before["a.md"] == ("t1", 3)
    assert snapshot_diff(before, before) == []
    after = doc_snapshot(
        [
            {"doc_name": "a.md", "updated_at": "t2", "block_count": 3},
            {"doc_name": "c.md", "updated_at": "t1", "block_count": 1},
        ]
    )
    assert snapshot_diff(before, after) == ["a.md", "b.md", "c.md"]


def test_coverage_issues_counts_precise_subtypes() -> None:
    questions = [
        {"groups": ["precise"], "subtype": "number"},
        {"groups": ["precise"], "subtype": "term"},
        {"groups": ["precise"], "subtype": "term"},
        {"groups": ["paraphrase"]},
    ]
    issues = coverage_issues(questions, {"brand": 1, "term": 2, "number": 1})
    assert issues == ["正式题集缺少题型: brand(0/1)"]
    assert coverage_issues(questions, {"term": 2}) == []
