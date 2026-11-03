"""评分公式纯函数用例:确定性/封顶/净化/解析防御/证据装配(spec
「答案异步评分(分解与确定性重算)」scenario ↔ 测试对照)。"""

from kongming.chat.scoring import (
    Claim,
    ScoreDecomposition,
    assemble_data_evidence,
    assemble_doc_evidence,
    build_material,
    compute_score,
    parse_decomposition,
    purify_claims,
)


def _dec(
    claims: tuple[Claim, ...],
    total: int = 1,
    covered: int = 1,
) -> ScoreDecomposition:
    return ScoreDecomposition(claims=claims, points_total=total, points_covered=covered)


def _claim(
    text: str = "上月销售额为 100 万",
    importance: str = "normal",
    support: str = "fully_supported",
    excerpt: str = "",
) -> Claim:
    return Claim(text=text, importance=importance, support=support, excerpt=excerpt)


def test_same_decomposition_same_score():
    """同分解必同分(任意次重算)。"""
    d = _dec((_claim(), _claim("销量为 200", "minor", "partially_supported")))
    scores = [compute_score(d).score for _ in range(5)]
    assert len(set(scores)) == 1


def test_all_supported_full_coverage_is_100():
    d = _dec((_claim(importance="critical"), _claim("覆盖 2", "minor")))
    assert compute_score(d).score == 100
    assert compute_score(d).grounding == 100
    assert compute_score(d).coverage == 100


def test_critical_unsupported_caps_score():
    """critical 无支撑=硬伤封顶,其余支撑良好也不得超过上限。"""
    d = _dec(
        (
            _claim("核心结论 50 万", "critical", "unsupported"),
            _claim("次要 3 天", "minor", "fully_supported"),
        )
    )
    s = compute_score(d)
    assert s.score <= 40


def test_all_unsupported_critical_is_low():
    """全主张无支撑:依据分 0,要点虽被回应仍收敛到低分(封顶线内)。"""
    d = _dec((_claim("100", "critical", "unsupported"),))
    s = compute_score(d)
    assert s.grounding == 0
    assert s.score <= 40


def test_purify_drops_subjective_minor_unsupported_without_digit():
    """净化:minor+无支撑+无数字(主观句)不参与依据分,依据分回升。"""
    subjective = _claim("回答非常清晰易懂", "minor", "unsupported")
    factual = _claim("销售额 100 万", "normal", "fully_supported")
    kept = purify_claims((subjective, factual))
    assert kept == (factual,)
    with_subjective = compute_score(_dec((subjective, factual))).grounding
    assert with_subjective == 100


def test_purify_keeps_digit_bearing_minor_unsupported():
    """含数字的 minor 无支撑主张是可验证事实断言,保留(宁罚勿纵)。"""
    c = _claim("转化率为 12%", "minor", "unsupported")
    assert purify_claims((c,)) == (c,)


def test_no_points_coverage_falls_back_to_grounding():
    """要点拆解为 0 时 C 取 G(单事实轮不因粒度吃亏)。"""
    d = _dec((_claim(),), total=0, covered=0)
    s = compute_score(d)
    assert s.coverage == s.grounding
    assert s.score == round(100 * (0.6 * s.grounding / 100 + 0.4 * s.grounding / 100) ** 2)


def test_score_monotonic_in_support():
    good = compute_score(_dec((_claim(support="fully_supported"),)))
    mid = compute_score(_dec((_claim(support="partially_supported"),)))
    bad = compute_score(_dec((_claim(support="unsupported"),)))
    assert good.score > mid.score > bad.score


def test_parse_decomposition_defaults_and_drops():
    raw = {
        "claims": [
            {"text": "销售额 100 万", "importance": "critical", "support": "fully_supported"},
            {"text": " bogus", "importance": "weird", "support": "weird"},  # 未知分级→默认
            {"no_text": True},  # 缺文本丢弃
            "not-a-dict",  # 非法条目跳过
        ],
        "sub_questions": [
            {"question": "a", "answered": True},
            {"question": "b", "answered": False},
            "bad",
        ],
    }
    d = parse_decomposition(raw)  # type: ignore[arg-type]
    assert d is not None
    assert len(d.claims) == 2
    assert d.claims[0].importance == "critical"
    assert d.claims[1].importance == "normal"
    assert d.claims[1].support == "unsupported"
    assert (d.points_total, d.points_covered) == (2, 1)


def test_parse_decomposition_none_on_empty():
    assert parse_decomposition({}) is None
    assert parse_decomposition({"claims": []}) is None
    assert parse_decomposition({"claims": [{"no_text": 1}]}) is None


def test_parse_decomposition_truncates_claim_count():
    raw = {"claims": [{"text": f"c{i} {i}"} for i in range(20)]}
    d = parse_decomposition(raw)
    assert d is not None
    assert len(d.claims) == 12


def test_parse_decomposition_coverage_clamped():
    raw = {
        "claims": [{"text": "x 1"}],
        "sub_questions": [{"question": "a", "answered": True}, {"question": "b", "answered": True}],
    }
    d = parse_decomposition(raw)
    assert d is not None
    assert d.points_total == 2 and d.points_covered == 2


def test_assemble_data_evidence_truncation_marks():
    rows: list[list[object]] = [[i, f"n{i}"] for i in range(80)]
    text = assemble_data_evidence(["id", "name"], rows, {"rows": 80})
    assert "仅展示前 50 行" in text
    assert "共 80 行" in text
    long = assemble_data_evidence(["c"], [["x" * 20_000]], {})
    assert "证据超长已截断" in long


def test_assemble_doc_evidence_skips_missing_and_caps_sources():
    sources = [{"source_ref": f"r{i}", "doc_name": f"d{i}"} for i in range(10)]
    contents = {f"r{i}": f"内容{i} 100" for i in range(10)}
    text = assemble_doc_evidence(sources, contents)
    assert "[d7]" in text and "[d8]" not in text  # 来源数截断(8)
    empty = assemble_doc_evidence(sources, {})
    assert empty == ""


def test_build_material_prefers_resolved_question():
    m = build_material(
        question="那 4 月呢", resolved_question="上海仓 4 月出库量", answer="答 1", evidence=""
    )
    assert "上海仓 4 月出库量" in m and "那 4 月呢" not in m
    fallback = build_material(question="原始问句", resolved_question=None, answer="答", evidence="")
    assert "原始问句" in fallback
