"""验证评估失败的分类优先级、SQL 拆分、拒答类别和 OTHER 兜底。"""

import pytest

from evals.attribution import classify_failure, summarize_attributions


def _case(**kw: object) -> dict[str, object]:
    """归因输入缺省形态:一次「表齐/JOIN 齐/聚合齐/谓词齐但结果不等」之外
    的形态按测试意图覆盖字段。"""
    base: dict[str, object] = {
        "question": "测试问句",
        "gold_sql": "SELECT count(*) FROM s.a JOIN s.b ON a.id = b.aid",
        "gen_sql": "SELECT count(*) FROM s.a JOIN s.b ON a.id = b.aid",
        "success": True,
        "verdict": "PASS",
    }
    base.update(kw)
    return base


# ============ 1.1 优先级序固定,先匹配先归类 ============


def test_join_before_metric_when_tables_complete() -> None:
    """边界题:表齐(隐式逗号连接)但 JOIN 数少 → JOIN(而非 METRIC),
    即便聚合也不同——JOIN 判据前提「gen 表集覆盖 gold 表集」成立。"""
    a = classify_failure(
        **_case(  # type: ignore[arg-type]
            gold_sql="SELECT count(*) FROM s.a JOIN s.b ON a.id = b.aid",
            gen_sql="SELECT sum(id) FROM s.a, s.b",  # 表齐、零 JOIN、聚合不同
        )
    )
    assert a.category == "JOIN"


def test_metric_before_filter_when_both_differ() -> None:
    """边界题:聚合差且过滤差 → METRIC 先于 FILTER(优先级序)。"""
    a = classify_failure(
        **_case(  # type: ignore[arg-type]
            gold_sql="SELECT count(*) FROM s.a WHERE x > 1",
            gen_sql="SELECT sum(id) FROM s.a WHERE x > 5",
        )
    )
    assert a.category == "METRIC"


# ============ 1.2 SELECT 层拆分(召回层 vs 生成层) ============


def test_select_recall_stage_when_expected_missing() -> None:
    """expected_tables 未被 gen 覆盖 = 召回层(表没进提示词)。"""
    a = classify_failure(
        **_case(  # type: ignore[arg-type]
            gold_sql="SELECT count(*) FROM s.a JOIN s.c ON a.cid = c.id",
            gen_sql="SELECT count(*) FROM s.a",
            expected_tables={"s.a", "s.c"},
        )
    )
    assert a.category == "SELECT"
    assert a.stage == "recall"  # 期望表 c 缺席:召回层


def test_select_generate_stage_when_expected_covered_but_gold_missing() -> None:
    """expected 已覆盖但 gold 表集未覆盖 = 生成层(表在提示词里没用上)。"""
    a = classify_failure(
        **_case(  # type: ignore[arg-type]
            gold_sql="SELECT count(*) FROM s.a JOIN s.c ON a.cid = c.id",
            gen_sql="SELECT count(*) FROM s.a",
            expected_tables={"s.a"},  # expected 只点名 a(已覆盖)
        )
    )
    assert a.category == "SELECT"
    assert a.stage == "generate"  # gold 的 c 缺席:生成层


# ============ 1.3 负例/拒绝守护失效自成类 ============


def test_neg_guard_no_data_case_generating_sql() -> None:
    """expect_no_data 用例却生成了 SQL(哨兵未触发)→ NEG_GUARD,不细分。"""
    a = classify_failure(
        **_case(  # type: ignore[arg-type]
            expect_no_data=True,
            success=False,
            verdict="PASS",
            gen_sql="SELECT 1 FROM s.a",
        )
    )
    assert a.category == "NEG_GUARD"


def test_neg_guard_reject_case_not_rejected() -> None:
    """expect_reject 用例未被拒 → NEG_GUARD。"""
    a = classify_failure(
        **_case(  # type: ignore[arg-type]
            expect_reject=True,
            success=True,
            verdict="PASS",
        )
    )
    assert a.category == "NEG_GUARD"


# ============ 1.4 ABSTAIN/REJECT 不混类;EXEC 双形态 ============


def test_abstain_positive_case_no_data() -> None:
    """正例返回 NO_DATA 时归类为 ABSTAIN，而不是 EXEC 或 REJECT。"""
    a = classify_failure(
        **_case(  # type: ignore[arg-type]
            success=False,
            verdict="NO_DATA",
            gen_sql="",
        )
    )
    assert a.category == "ABSTAIN"


def test_reject_blocked_verdict() -> None:
    """verdict=BLOCKED(防火墙拦截终止)→ REJECT,不与 ABSTAIN 混类。"""
    a = classify_failure(
        **_case(  # type: ignore[arg-type]
            success=False,
            verdict="BLOCKED",
            gen_sql="DELETE FROM s.a",
        )
    )
    assert a.category == "REJECT"


def test_exec_covers_failure_and_missing_sql() -> None:
    """EXEC 覆盖两种形态:执行失败(success=false)与无生成 SQL。"""
    a = classify_failure(
        **_case(success=False, verdict="ERROR", gen_sql="SELECT 1")  # type: ignore[arg-type]
    )
    assert a.category == "EXEC"
    b = classify_failure(
        **_case(success=True, verdict="PASS", gen_sql="")  # type: ignore[arg-type]
    )
    assert b.category == "EXEC"


# ============ 1.5 OTHER 桶:显式归类 + 题目 id + 占比提示 ============


def test_other_when_no_rule_matches() -> None:
    """表/JOIN/聚合/谓词全齐但结果仍不等(如投影形态差异)→ OTHER 显式归类。"""
    a = classify_failure(
        **_case(  # type: ignore[arg-type]
            gold_sql="SELECT name FROM s.a",
            gen_sql="SELECT upper(name) FROM s.a",  # 结构/聚合/谓词全齐,仅投影形态异
        )
    )
    assert a.category == "OTHER"


def test_other_bucket_reports_ids_and_threshold_hint() -> None:
    """OTHER 占比 ≥20%:统计块显式报告数量、题目 id 与「规则不足需人工扩充」提示。"""
    items = [
        (f"q{i}", classify_failure(**_case(success=False, verdict="ERROR")))  # type: ignore[arg-type]
        for i in range(15)
    ] + [
        (f"other{i}", classify_failure(**_case()))  # type: ignore[arg-type]
        for i in range(5)  # 5/20 = 25% ≥ 20%
    ]
    summary = summarize_attributions(items)
    assert "OTHER" in summary
    assert "other0" in summary and "other4" in summary  # 题目 id 透出
    assert "20%" in summary  # 阈值提示
    assert "人工扩充" in summary

    # 低于阈值:不提示(1/20 = 5%)
    few = [
        (f"q{i}", classify_failure(**_case(success=False, verdict="ERROR")))  # type: ignore[arg-type]
        for i in range(19)
    ] + [("ok0", classify_failure(**_case()))]  # type: ignore[arg-type]
    low = summarize_attributions(few)
    assert "人工扩充" not in low


# ============ 1.6 纯函数:零外部调用 ============


def test_classify_is_pure_no_external_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """分类不触网/不调 LLM/不建连:外部出口全部换成了即抛桩,九类各跑一例。"""
    import asyncpg
    import httpx

    def _boom(*a: object, **kw: object) -> None:
        raise AssertionError("归因分类触发了外部调用")

    monkeypatch.setattr(asyncpg, "connect", _boom)
    monkeypatch.setattr(httpx, "AsyncClient", _boom)
    monkeypatch.setattr("socket.create_connection", _boom)

    cases = [
        _case(expect_reject=True),
        _case(expect_no_data=True),
        _case(success=False, verdict="BLOCKED"),
        _case(success=False, verdict="NO_DATA"),
        _case(success=False, verdict="ERROR"),
        _case(
            gold_sql="SELECT count(*) FROM s.a JOIN s.c ON a.cid=c.id",
            gen_sql="SELECT count(*) FROM s.a",
            expected_tables={"s.a", "s.c"},
        ),
        _case(
            gold_sql="SELECT 1 FROM s.a JOIN s.c ON a.cid=c.id", gen_sql="SELECT 1 FROM s.a, s.c"
        ),  # 表齐零 JOIN → JOIN 类
        _case(gold_sql="SELECT count(*) FROM s.a", gen_sql="SELECT sum(id) FROM s.a"),
        _case(gold_sql="SELECT name FROM s.a WHERE x>1", gen_sql="SELECT name FROM s.a WHERE x>9"),
        _case(),  # OTHER
    ]
    for c in cases:
        attribution = classify_failure(**c)  # type: ignore[arg-type]
        assert attribution.category in {
            "NEG_GUARD",
            "EXEC",
            "REJECT",
            "ABSTAIN",
            "SELECT",
            "JOIN",
            "METRIC",
            "FILTER",
            "OTHER",
        }
        assert attribution.reason  # 判据原文非空(可审计)
