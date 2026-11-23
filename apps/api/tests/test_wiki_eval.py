"""Wiki 评测件纯函数测试:页面校验/来源核对/三策略/盲评/结论(默认门禁)。"""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from evals.wiki.pages import (
    Fact,
    Page,
    PageError,
    generate_page,
    load_pages,
    maintenance_minutes,
    parse_page_text,
    review_problems,
    sha256_text,
    validate_page,
    validate_pages,
)
from evals.wiki.report import (
    Overhead,
    StrategyStat,
    config_issues,
    cost_from_usage,
    decide,
    question_coverage_issues,
)
from evals.wiki.runtime import (
    PageCheck,
    VerifiedFact,
    check_page,
    gather_evidence,
    verify_pages,
)
from evals.wiki.scoring import (
    AnswerResult,
    blind_objects,
    calibration_sample,
    parse_verdict,
    score_results,
    summarize,
)
from kongming.rag.chunker import chunk_document

_API_ROOT = Path(__file__).resolve().parent.parent
_CORPUS_DIR = _API_ROOT / "evals" / "retrieval" / "corpus"
_PAGES_DIR = _API_ROOT / "evals" / "wiki" / "pages"

_DOCS: dict[str, str] = {
    "规则.md": "# 基础\n所有影片基础租期为 7 天。\n\n# 逾期\n逾期每日每部 0.5 元。",
    "政策.md": "# 等级\n累计满 500 元为金卡会员。",
}


def _parents(doc: str, text: str) -> list[dict[str, object]]:
    return [c for c in chunk_document(doc, text) if c["level"] == "PARENT"]


def _fact(fid: str, doc: str, snippet: str) -> Fact:
    hits = [p for p in _parents(doc, _DOCS[doc]) if snippet in str(p["content"])]
    assert len(hits) == 1
    return Fact(
        id=fid, doc=doc, snippet=snippet, parent_sha256=sha256_text(str(hits[0]["content"]))
    )


def _page() -> Page:
    return Page(
        page_id="demo",
        title="演示",
        version=1,
        body="基础 7 天[[f1]];逾期每日每部 0.5 元[[f2]]。",
        facts=(
            _fact("f1", "规则.md", "基础租期为 7 天"),
            _fact("f2", "规则.md", "逾期每日每部 0.5 元"),
        ),
        source_sha={"规则.md": sha256_text(_DOCS["规则.md"])},
    )


# ============ 页面格式与校验 ============


def test_repo_prototype_pages_valid_against_corpus() -> None:
    """仓库内两页原型(评测目录 seed)与检索语料一致。"""
    corpus = {p.name: p.read_text(encoding="utf-8") for p in _CORPUS_DIR.glob("*.md")}
    pages = load_pages(_PAGES_DIR)
    assert len(pages) >= 2
    assert validate_pages(pages, corpus) == {}


def test_validate_page_rejects_tampering() -> None:
    page = _page()
    assert validate_page(page, _DOCS) == []
    bad_hash = Page(
        page_id=page.page_id,
        title=page.title,
        version=1,
        body=page.body,
        facts=(Fact("f1", "规则.md", page.facts[0].snippet, "deadbeef"), page.facts[1]),
        source_sha=page.source_sha,
    )
    problems = validate_page(bad_hash, _DOCS)
    assert any("摘要不符" in item for item in problems)
    bad_marker = Page(
        page_id=page.page_id,
        title=page.title,
        version=1,
        body="基础 7 天[[f1]]。",
        facts=page.facts,
        source_sha=page.source_sha,
    )
    assert any("未被正文引用" in item for item in validate_page(bad_marker, _DOCS))
    missing = Page(
        page_id=page.page_id,
        title=page.title,
        version=1,
        body=page.body,
        facts=page.facts,
        source_sha={"不存在.md": "x"},
    )
    assert any("来源不在语料" in item for item in validate_page(missing, _DOCS))


def test_parse_page_text_roundtrip() -> None:
    page = _page()
    front = (
        "---\npage_id: demo\ntitle: 演示\nversion: 1\n"
        f"sources:\n- doc: 规则.md\n  doc_sha256: {sha256_text(_DOCS['规则.md'])}\n"
        "facts:\n"
        f"- id: f1\n  doc: 规则.md\n  snippet: {page.facts[0].snippet}\n"
        f"  parent_sha256: {page.facts[0].parent_sha256}\n"
        f"- id: f2\n  doc: 规则.md\n  snippet: {page.facts[1].snippet}\n"
        f"  parent_sha256: {page.facts[1].parent_sha256}\n"
        "---\n" + page.body
    )
    parsed = parse_page_text(front)
    assert parsed.page_id == "demo" and len(parsed.facts) == 2
    assert validate_page(parsed, _DOCS) == []


class _FakeChat:
    """固定应答的对话桩。"""

    def __init__(self, answer: str) -> None:
        self.answer = answer

    async def chat(self, system: str, user: str, *, max_tokens: int = 1200) -> str:
        return self.answer


async def test_generate_page_requires_verifiable_snippets() -> None:
    payload = {
        "facts": [
            {"doc": "规则.md", "snippet": "基础租期为 7 天"},
            {"doc": "规则.md", "snippet": "逾期每日每部 0.5 元"},
        ],
        "body": "基础 7 天[[f1]];逾期每日每部 0.5 元[[f2]]。",
    }
    page = await generate_page(
        _FakeChat(json.dumps(payload)),
        page_id="gen",
        title="生成",
        docs=_DOCS,
        generated_by="fake-model",
        prompt_version="v1",
    )
    assert len(page.facts) == 2 and page.generated_by == "fake-model"
    assert validate_page(page, _DOCS) == []
    bad = {"facts": [{"doc": "规则.md", "snippet": "不存在的句子"}], "body": "x[[f1]]"}
    with pytest.raises(PageError, match="无法唯一核对"):
        await generate_page(
            _FakeChat(json.dumps(bad)),
            page_id="gen",
            title="生成",
            docs=_DOCS,
            generated_by="fake-model",
            prompt_version="v1",
        )


# ============ 来源核对 ============


def _docs_rows() -> list[dict[str, object]]:
    return [
        {"id": "d1", "doc_name": "规则.md", "updated_at": "t1", "block_count": 4},
    ]


def _chunks() -> dict[str, list[dict[str, object]]]:
    return {
        "规则.md": [
            {"chunk_level": "PARENT", "seq": p["seq"], "content": p["content"]}
            for p in _parents("规则.md", _DOCS["规则.md"])
        ]
    }


def test_check_page_usable_and_rejects_missing_or_changed() -> None:
    page = _page()
    ok = check_page(page, _docs_rows(), _chunks())
    assert ok.usable and len(ok.facts) == 2
    missing = check_page(page, [], _chunks())
    assert not missing.usable and any("来源缺失" in r for r in missing.reasons)
    changed_chunks = _chunks()
    changed_chunks["规则.md"][0]["content"] = "基础租期为 7 天(已修改)"
    changed = check_page(page, _docs_rows(), changed_chunks)
    assert not changed.usable and any("摘要不符" in r for r in changed.reasons)


class _FakeDocsPort:
    def __init__(self) -> None:
        self.docs = _docs_rows()
        self.chunks = _chunks()

    async def list_docs(self) -> list[dict[str, object]]:
        return self.docs

    async def list_chunks(self, doc_id: str) -> list[dict[str, object]]:
        assert doc_id == "d1"
        return self.chunks["规则.md"]


async def test_verify_pages_reads_scoped_docs() -> None:
    checks = await verify_pages([_page()], _FakeDocsPort())
    assert [c.usable for c in checks] == [True]


# ============ 三种取材方式 ============


class _FakeProbe:
    def __init__(
        self, items: list[tuple[str, int]] | None = None, exc: Exception | None = None
    ) -> None:
        self.items = items or []
        self.exc = exc
        self.calls = 0

    async def probe(self, question: str) -> list[tuple[str, int]]:
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return self.items


class _FakeWiki:
    def __init__(
        self, pages: list[PageCheck] | None = None, exc: Exception | None = None, delay: float = 0.0
    ) -> None:
        self.pages = pages or []
        self.exc = exc
        self.delay = delay

    async def lookup(self, question: str, limit: int) -> list[PageCheck]:
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.exc is not None:
            raise self.exc
        return self.pages


def _usable_check() -> PageCheck:
    return PageCheck("demo", True, (VerifiedFact("规则.md", 0), VerifiedFact("规则.md", 2)), ())


async def test_gather_evidence_three_strategies() -> None:
    probe = _FakeProbe([("规则.md", 0), ("政策.md", 0)])
    raw = await gather_evidence("raw", "q", probe=probe, wiki=_FakeWiki())
    assert raw.items == (("规则.md", 0), ("政策.md", 0))
    wiki_first = await gather_evidence(
        "wiki_first", "q", probe=probe, wiki=_FakeWiki([_usable_check()])
    )
    assert wiki_first.items == (("规则.md", 0), ("规则.md", 2)) and wiki_first.switch_reason is None
    fallback = await gather_evidence("wiki_first", "q", probe=probe, wiki=_FakeWiki([]))
    assert fallback.items == (("规则.md", 0), ("政策.md", 0))
    assert fallback.switch_reason and "无可用主题页" in fallback.switch_reason
    parallel = await gather_evidence(
        "parallel", "q", probe=probe, wiki=_FakeWiki([_usable_check()])
    )
    assert parallel.items == (("规则.md", 0), ("规则.md", 2), ("政策.md", 0))


async def test_parallel_survives_single_route_failure_and_timeout() -> None:
    probe = _FakeProbe([("规则.md", 0)])
    single = await gather_evidence(
        "parallel", "q", probe=probe, wiki=_FakeWiki(exc=RuntimeError("boom"))
    )
    assert single.items == (("规则.md", 0),) and single.switch_reason
    timed_out = await gather_evidence(
        "wiki_first", "q", probe=probe, wiki=_FakeWiki(delay=0.2), timeout_s=0.05
    )
    assert timed_out.switch_reason and "超时" in timed_out.switch_reason
    with pytest.raises(RuntimeError):
        await gather_evidence(
            "parallel",
            "q",
            probe=_FakeProbe(exc=RuntimeError("down")),
            wiki=_FakeWiki(exc=RuntimeError("down")),
        )


async def test_parallel_records_raw_route_failure() -> None:
    """并行方式原文路失败时用主题页证据作答,并记录失败的一路。"""
    evidence = await gather_evidence(
        "parallel",
        "q",
        probe=_FakeProbe(exc=RuntimeError("down")),
        wiki=_FakeWiki([_usable_check()]),
    )
    assert evidence.items == (("规则.md", 0), ("规则.md", 2))
    assert evidence.switch_reason and "原文路不可用" in evidence.switch_reason


# ============ 盲评与结论 ============


def _results() -> list[AnswerResult]:
    return [
        AnswerResult("q1", "single_hop", "raw", "问题一", "回答 A"),
        AnswerResult("q1", "single_hop", "wiki_first", "问题一", "回答 B"),
        AnswerResult("q2", "multi_hop_cross", "parallel", "问题二", "回答 C"),
    ]


class _FakeJudge:
    def __init__(self, payload: dict[str, bool]) -> None:
        self.payload = payload
        self.objects: list[str] = []

    async def chat(self, system: str, user: str, *, max_tokens: int = 1200) -> str:
        self.objects.append(user)
        return json.dumps({**self.payload, "note": "ok"})


def test_blind_objects_strip_strategy_and_shuffle_deterministically() -> None:
    objects, mapping = blind_objects(_results(), seed=7)
    objects2, _ = blind_objects(_results(), seed=7)
    assert objects == objects2
    assert all(set(obj) == {"id", "question", "answer", "citations"} for obj in objects)
    assert "strategy" not in json.dumps(objects)
    assert {m.strategy for m in mapping.values()} == {"raw", "wiki_first", "parallel"}


async def test_score_results_and_summary() -> None:
    judge = _FakeJudge({"correct": True, "grounded": True, "citations_supported": False})
    verdicts = await score_results(judge, _results(), seed=7)
    assert len(verdicts) == 3 and all(v.correct for v in verdicts)
    summary = summarize(verdicts)
    assert summary["raw"]["correct_rate"] == 1.0
    assert summary["raw"]["citation_support"] == 0.0
    sample = calibration_sample(verdicts, ratio=1.0, seed=7)
    assert len(sample) == 3
    with pytest.raises(TypeError, match="缺少布尔字段"):
        parse_verdict('{"correct": true}')


def _stat(
    *, cross: float = 0.6, single: float = 0.8, citation: float = 1.0, n: int = 10
) -> StrategyStat:
    return StrategyStat(
        n=n,
        correct_rate=single,
        grounded_rate=single,
        citation_support=citation,
        cross_doc_correct=cross,
        single_doc_correct=single,
    )


def _overhead(**overrides: object) -> Overhead:
    """开销检查默认值(费用与维护时间均在登记上限内);按用例覆盖。"""
    kw: dict[str, Any] = {
        "cost_registered": True,
        "question_costs": [0.1],
        "max_question_cost_cny": 1.0,
        "missing_usage": 0,
        "maintenance_minutes": 60.0,
        "max_maintenance_minutes": 120.0,
    }
    kw.update(overrides)
    return Overhead(**kw)


def _decide_kw(**overrides: object) -> dict[str, Any]:
    """判定入参默认值(质量与开销条件均满足);按用例覆盖。"""
    kw: dict[str, Any] = {
        "citation_min": 0.95,
        "cross_improve_pp": 0.05,
        "single_regress_pp": 0.05,
        "overhead": _overhead(),
        "sources_ok": True,
        "config_issues_": [],
    }
    kw.update(overrides)
    return kw


def test_decide_all_branches() -> None:
    baseline = _stat(cross=0.5, single=0.8)
    stats = {"raw": baseline, "wiki_first": _stat(cross=0.7), "parallel": _stat(cross=0.5)}
    verdict, reasons = decide(stats, **_decide_kw())
    assert verdict == "wiki_first" and reasons == []
    parallel_only = {"raw": baseline, "wiki_first": _stat(cross=0.5), "parallel": _stat(cross=0.7)}
    verdict, _ = decide(parallel_only, **_decide_kw())
    assert verdict == "parallel"
    keep, why = decide(
        {
            "raw": baseline,
            "wiki_first": _stat(cross=0.5, citation=0.5),
            "parallel": _stat(cross=0.5),
        },
        **_decide_kw(),
    )
    assert keep == "keep" and why
    pending, _ = decide({}, **_decide_kw(overhead=_overhead(cost_registered=False)))
    assert pending == "pending"
    invalid, _ = decide({"raw": baseline}, **_decide_kw(sources_ok=False))
    assert invalid == "keep"
    assert config_issues({}) != []


def test_decide_enforces_cost_cap_and_missing_usage() -> None:
    """登记的单题费用上限参与判定;用量缺失只能待判断。"""
    stats = {
        "raw": _stat(cross=0.5),
        "wiki_first": _stat(cross=0.7),
        "parallel": _stat(cross=0.5),
    }
    over, why = decide(
        stats,
        **_decide_kw(overhead=_overhead(question_costs=[0.3], max_question_cost_cny=0.2)),
    )
    assert over == "keep" and any("超上限" in item for item in why)
    pending, why = decide(stats, **_decide_kw(overhead=_overhead(missing_usage=2)))
    assert pending == "pending" and any("缺用量" in item for item in why)


def test_decide_enforces_maintenance_minutes() -> None:
    """维护时间未登记只能待判断;超登记上限判保留。"""
    stats = {
        "raw": _stat(cross=0.5),
        "wiki_first": _stat(cross=0.7),
        "parallel": _stat(cross=0.5),
    }
    missing, why = decide(
        stats,
        **_decide_kw(overhead=_overhead(record_problems=("缺核对记录",), maintenance_minutes=None)),
    )
    assert missing == "pending" and any("缺核对记录" in item for item in why)
    over, why = decide(stats, **_decide_kw(overhead=_overhead(maintenance_minutes=200.0)))
    assert over == "keep" and any("维护时间超上限" in item for item in why)


def test_config_issues_requires_category_registration() -> None:
    """跨资料/单资料题型登记缺失或为空时条件不完整。"""
    full: dict[str, object] = {
        "answer_model": "m",
        "prompt_version": "v",
        "embedding_channel": "agent_internal",
        "answer_max_tokens": 800,
        "evidence_limit": 5,
        "citation_support_min": 0.95,
        "cross_doc_improve_pp": 0.05,
        "single_doc_regress_pp": 0.05,
        "per_question_cost_cap_cny": 0.2,
        "wiki_maintenance_minutes_cap": 120,
        "min_question_counts": {},
        "cross_categories": ["multi"],
        "single_categories": ["single"],
    }
    assert config_issues(full) == []
    for key in ("cross_categories", "single_categories"):
        missing = {k: v for k, v in full.items() if k != key}
        assert any(key in item for item in config_issues(missing))
        assert any(key in item for item in config_issues({**full, key: []}))


def test_cost_from_usage_marks_missing_usage() -> None:
    """用量齐全按登记单价折算;缺失返回 None(报告标未计入,不估算)。"""
    usage = {"prompt_tokens": 1000, "completion_tokens": 500}
    assert cost_from_usage(usage, input_per_million=2.0, output_per_million=6.0) == pytest.approx(
        (1000 * 2.0 + 500 * 6.0) / 1_000_000.0
    )
    assert cost_from_usage({}, input_per_million=2.0, output_per_million=6.0) is None


async def test_score_results_include_expected_answer() -> None:
    """提供标准答案时评审提示携带核对信息;未提供时标注「未提供」。"""
    judge = _FakeJudge({"correct": True, "grounded": True, "citations_supported": True})
    results = [AnswerResult("q1", "single", "raw", "问题", "回答")]
    await score_results(
        judge,
        results,
        expectations={"q1": {"expected": "标准答案文本", "key_points": ["要点一"]}},
    )
    assert "标准答案文本" in judge.objects[0] and "要点一" in judge.objects[0]


def test_question_coverage_issues() -> None:
    questions = [
        {"category": "single"},
        {"category": "single"},
        {"category": "multi"},
    ]
    assert question_coverage_issues(questions, {"single": 2, "multi": 2}) == [
        "正式题集缺少题型: multi(1/2)"
    ]
    assert question_coverage_issues(questions, {"single": 2}) == []


def test_parse_verdict_tolerates_surrounding_text() -> None:
    raw = (
        '{"correct": true, "grounded": false, "citations_supported": true, "note": "x"}'
        '{"correct": false, "grounded": false, "citations_supported": false, "note": "y"}'
        "补充说明"
    )
    parsed = parse_verdict(raw)
    assert parsed["correct"] is True and parsed["grounded"] is False
    with pytest.raises(ValueError):
        parse_verdict("没有 JSON 的纯文本")


def test_validate_page_review_gate() -> None:
    """正式目录校验拒绝未审核页;approved + 评审者/日期/人工分钟齐全才放行。"""
    page = _page()
    assert any("未审核" in item for item in validate_page(page, _DOCS, require_review=True))
    approved = Page(
        page_id=page.page_id,
        title=page.title,
        version=1,
        body=page.body,
        facts=page.facts,
        source_sha=page.source_sha,
        review_status="approved",
        reviewer="owner",
        reviewed_at="2026-11-23",
        review_minutes=30,
    )
    assert validate_page(approved, _DOCS, require_review=True) == []
    no_minutes = Page(
        page_id=page.page_id,
        title=page.title,
        version=1,
        body=page.body,
        facts=page.facts,
        source_sha=page.source_sha,
        review_status="approved",
        reviewer="owner",
        reviewed_at="2026-11-23",
    )
    assert any("维护时间" in item for item in validate_page(no_minutes, _DOCS, require_review=True))


def test_maintenance_minutes_and_review_problems() -> None:
    """人工分钟合计缺页即未登记;记录问题按页点名。"""
    page = _page()
    blank = Page(
        page_id=page.page_id,
        title=page.title,
        version=1,
        body=page.body,
        facts=page.facts,
        source_sha=page.source_sha,
    )
    assert maintenance_minutes([blank]) is None
    assert review_problems([blank])
    assert maintenance_minutes([]) is None
    reviewed = Page(
        page_id="demo",
        title=page.title,
        version=1,
        body=page.body,
        facts=page.facts,
        source_sha=page.source_sha,
        review_status="approved",
        reviewer="owner",
        reviewed_at="2026-11-23",
        review_minutes=30,
    )
    assert maintenance_minutes([reviewed]) == 30.0
    assert review_problems([reviewed]) == ()
