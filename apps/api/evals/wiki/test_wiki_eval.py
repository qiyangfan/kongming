"""Wiki 问答对照运行器:三种取材 → 回答 → 盲评 → 报告。

运行:`pytest evals/wiki/test_wiki_eval.py -m retrieval`(需 api/agent 与
LLM 凭证;缺凭证整层跳过)。主题页(七页覆盖全部语料)与题集均为仓库
内置,条件未登记时结论判为待判断。
"""

# pyright: reportPrivateUsage=false

import os
import time
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
import yaml
from conftest import API_BASE, _login

from evals.judge.client import JudgeLlm
from evals.retrieval.test_retrieval_eval import _load_dataset, _upload_corpus
from evals.wiki.pages import Page, load_pages, maintenance_minutes, review_problems
from evals.wiki.report import (
    PRICE_TABLE_VERSION,
    Overhead,
    StrategyStat,
    config_issues,
    cost_from_usage,
    decide,
    question_coverage_issues,
)
from evals.wiki.runtime import (
    Evidence,
    PageCheck,
    gather_evidence,
    verify_pages,
)
from evals.wiki.scoring import AnswerResult, score_results, summarize

pytestmark = [pytest.mark.retrieval]

_WIKI_DIR = Path(__file__).parent
_RETR_DIR = _WIKI_DIR.parent / "retrieval"
_STRATEGIES = ("raw", "wiki_first", "parallel")
_ANSWER_SYSTEM = (
    "你是文档问答助手。只依据给定原文证据作答;证据不足时明确说明缺少依据;"
    "回答末尾列出所用的证据编号。"
)


def _load_conditions() -> dict[str, Any]:
    loaded = cast("object", yaml.safe_load((_WIKI_DIR / "wiki_eval.yaml").read_text("utf-8")))
    if not isinstance(loaded, dict):
        raise pytest.UsageError("wiki_eval.yaml: 顶层必须是映射")
    return cast("dict[str, Any]", loaded)


class _ApiDocs:
    """资料/分块读取(生产=现有文档管理接口)。"""

    def __init__(self, c: httpx.AsyncClient, headers: dict[str, str], dsk: str) -> None:
        self._c = c
        self._h = headers
        self._dsk = dsk
        self.cache: dict[tuple[str, int], str] = {}

    async def list_docs(self) -> list[dict[str, Any]]:
        resp = await self._c.get(
            "/system/document/documents", headers=self._h, params={"datasource_key": self._dsk}
        )
        assert resp.status_code == 200
        return cast("list[dict[str, Any]]", resp.json())

    async def list_chunks(self, doc_id: str) -> list[dict[str, Any]]:
        resp = await self._c.get(f"/system/document/documents/{doc_id}/chunks", headers=self._h)
        assert resp.status_code == 200
        return cast("list[dict[str, Any]]", resp.json())


class _ApiProbe:
    """原文检索(试跑接口父块名次)。"""

    def __init__(self, c: httpx.AsyncClient, headers: dict[str, str], dsk: str, k: int) -> None:
        self._c = c
        self._h = headers
        self._dsk = dsk
        self._k = k

    async def probe(self, question: str) -> list[tuple[str, int]]:
        resp = await self._c.get(
            "/system/document/retrieval-probe",
            headers=self._h,
            params={"datasource_key": self._dsk, "question": question, "parent_top_k": self._k},
        )
        assert resp.status_code == 200
        return [(str(p["doc_name"]), int(p["seq"])) for p in resp.json()["parents"]]


class _EmbeddingWikiIndex:
    """主题页向量查找:页面与问句均经 Agent 内部向量化接口(与检索同模型)。"""

    def __init__(
        self,
        pages: list[Page],
        checks: list[PageCheck],
        agent_base: str,
        internal_token: str,
        limit: int,
    ) -> None:
        self._agent_base = agent_base.rstrip("/")
        self._token = internal_token
        self._limit = limit
        self._by_id = {check.page_id: check for check in checks}
        empty = PageCheck("", False, (), ())
        self._pages = [p for p in pages if self._by_id.get(p.page_id, empty).usable]
        self._vectors: list[list[float]] | None = None

    async def _embed(self, texts: list[str]) -> list[list[float]]:
        async with httpx.AsyncClient(timeout=60.0) as c:
            resp = await c.post(
                f"{self._agent_base}/internal/embedding",
                headers={"X-Internal-Token": self._token},
                json={"texts": texts},
            )
            assert resp.status_code == 200, f"向量化失败 {resp.status_code}"
            return cast("list[list[float]]", resp.json()["vectors"])

    async def _ensure_vectors(self) -> list[list[float]]:
        if self._vectors is None:
            self._vectors = await self._embed([f"{p.title}\n{p.body}" for p in self._pages])
        return self._vectors

    async def lookup(self, question: str, limit: int) -> list[PageCheck]:
        vectors = await self._ensure_vectors()
        query = (await self._embed([question]))[0]
        scored = sorted(
            zip(self._pages, vectors, strict=True),
            key=lambda pair: -sum(a * b for a, b in zip(query, pair[1], strict=True)),
        )
        take = min(limit, self._limit)
        return [self._by_id[p.page_id] for p, _ in scored[:take]]


class _UsageTrackingJudge:
    """评审用量采集包装:透传 chat,累计每次调用返回的 usage(费用单列)。"""

    def __init__(self, inner: JudgeLlm) -> None:
        self._inner = inner
        self.usages: list[dict[str, Any]] = []

    async def chat(self, system: str, user: str, *, max_tokens: int = 1200) -> str:
        content, usage = await self._inner.chat_with_usage(system, user, max_tokens=max_tokens)
        self.usages.append(usage)
        return content


def _evidence_text(items: tuple[tuple[str, int], ...], chunks: dict[tuple[str, int], str]) -> str:
    """证据拼装;单段截 1200 字、总量截 6000 字(作答预算固定,避免长证据拖垮调用)。"""
    blocks: list[str] = []
    total = 0
    for doc, seq in items:
        block = f"[{doc} §{seq}]\n{chunks.get((doc, seq), '(原文未取到)')[:1200]}"
        if total + len(block) > 6000:
            break
        blocks.append(block)
        total += len(block)
    return "\n\n".join(blocks)


def _strategy_stat(
    verdicts: list[dict[str, Any]],
    strategy: str,
    cross_categories: set[str],
    single_categories: set[str],
) -> StrategyStat:
    rows = [v for v in verdicts if v["strategy"] == strategy]
    cross = [v for v in rows if v["category"] in cross_categories]
    single = [v for v in rows if v["category"] in single_categories]

    def rate(items: list[dict[str, Any]], key: str) -> float:
        return sum(int(v[key]) for v in items) / len(items) if items else 0.0

    return StrategyStat(
        n=len(rows),
        correct_rate=rate(rows, "correct"),
        grounded_rate=rate(rows, "grounded"),
        citation_support=rate(rows, "citations_supported"),
        cross_doc_correct=rate(cross, "correct"),
        single_doc_correct=rate(single, "correct"),
    )


async def test_wiki_paired_report() -> None:  # noqa: PLR0912,PLR0915,C901
    conditions = _load_conditions()
    if issues := config_issues(conditions):
        pytest.fail(f"运行条件缺项: {issues}")
    questions_file = os.environ.get("KM_EVAL_WIKI_QUESTIONS", "")
    if questions_file:
        loaded = cast(
            "dict[str, Any]",
            yaml.safe_load((_WIKI_DIR / questions_file).read_text(encoding="utf-8")),
        )
        cfg = cast("dict[str, Any]", loaded["dataset"])
        questions = cast("list[dict[str, Any]]", loaded["questions"])
    else:
        ds = _load_dataset()
        cfg = cast("dict[str, Any]", ds["dataset"])
        questions = cast("list[dict[str, Any]]", ds["questions"])
    coverage_gaps = question_coverage_issues(
        questions, cast("dict[str, int]", conditions.get("min_question_counts", {}))
    )
    expectations = {
        str(q["id"]): {
            "expected": str(q.get("expected") or ""),
            "key_points": list(cast("list[Any]", q.get("key_points") or [])),
        }
        for q in questions
    }
    tenant = str(cfg["tenant"])
    dsk = str(cfg["datasource_key"])
    limit = int(conditions["evidence_limit"])
    timeout_s = 30.0
    token = await _login()
    headers = {"Authorization": f"Bearer {token}"}
    if tenant != "default":
        headers["X-Tenant-Id"] = tenant

    corpus = {
        p.name: p.read_text(encoding="utf-8")
        for p in sorted((_RETR_DIR / str(cfg.get("corpus_dir", "corpus"))).glob("*.md"))
    }
    pages = load_pages(_WIKI_DIR / "pages")
    approved_pages = [
        p for p in pages if p.review_status == "approved" and p.reviewer and p.reviewed_at
    ]
    record_problems = review_problems(pages)
    review_minutes = maintenance_minutes(approved_pages)
    judge = JudgeLlm()
    scoring_judge = _UsageTrackingJudge(judge)
    answers: list[AnswerResult] = []
    cost_registered = "input_per_million_cny" in conditions
    q_costs: dict[str, float] = {}
    missing_usage = 0
    switches = 0  # wiki_first 实际转用原文的题数(与「全部 wiki_first」分列)

    async with httpx.AsyncClient(base_url=API_BASE, timeout=120.0) as client:
        await _upload_corpus(client, corpus, headers, dsk)
        docs_port = _ApiDocs(client, headers, dsk)
        checks_before = await verify_pages(approved_pages, docs_port)
        chunks: dict[tuple[str, int], str] = {}
        for doc in await docs_port.list_docs():
            for row in await docs_port.list_chunks(str(doc["id"])):
                if str(row.get("chunk_level")) == "PARENT":
                    chunks[(str(doc["doc_name"]), int(row["seq"]))] = str(row["content"])
        agent_base = os.environ.get("KM_EVAL_AGENT_BASE", "http://127.0.0.1:8001")
        internal = os.environ.get("KM_EVAL_INTERNAL_TOKEN") or os.environ.get(
            "KM_INTERNAL_TOKEN", ""
        )
        wiki = _EmbeddingWikiIndex(approved_pages, checks_before, agent_base, internal, limit)
        probe = _ApiProbe(client, headers, dsk, limit)
        for q in questions:
            qid = str(q["id"])
            category = str(q["category"])
            for strategy in _STRATEGIES:
                started = time.perf_counter()
                evidence: Evidence = await gather_evidence(
                    strategy,
                    str(q["question"]),
                    probe=probe,
                    wiki=wiki,
                    limit=limit,
                    timeout_s=timeout_s,
                )
                text = _evidence_text(evidence.items, chunks)
                answer, usage = await judge.chat_with_usage(
                    _ANSWER_SYSTEM,
                    f"问题:{q['question']}\n证据:\n{text}",
                    max_tokens=int(conditions["answer_max_tokens"]),
                )
                if cost_registered:
                    per = cost_from_usage(
                        usage,
                        input_per_million=float(conditions["input_per_million_cny"]),
                        output_per_million=float(conditions["output_per_million_cny"]),
                    )
                    if per is None:
                        missing_usage += 1
                    else:
                        q_costs[qid] = q_costs.get(qid, 0.0) + per
                if strategy == "wiki_first" and evidence.switch_reason:
                    switches += 1
                answers.append(
                    AnswerResult(
                        qid=qid,
                        category=category,
                        strategy=strategy,
                        question=str(q["question"]),
                        answer=answer,
                        citations=tuple(
                            f"[{doc} §{seq}]\n{chunks.get((doc, seq), '')[:400]}"
                            for doc, seq in evidence.items
                        ),
                    )
                )
                print(
                    f"[wiki] {qid} {strategy} evidence={len(evidence.items)} "
                    f"switch={evidence.switch_reason} "
                    f"{(time.perf_counter() - started) * 1000:.0f}ms"
                )
        checks_after = await verify_pages(approved_pages, docs_port)

    checks_changed = [c.page_id for b, c in zip(checks_before, checks_after, strict=True) if b != c]
    sources_ok = not checks_changed
    verdicts = await score_results(scoring_judge, answers, expectations=expectations)
    judge_cost = 0.0
    judge_missing = 0
    if cost_registered:
        for usage in scoring_judge.usages:
            per = cost_from_usage(
                usage,
                input_per_million=float(conditions["input_per_million_cny"]),
                output_per_million=float(conditions["output_per_million_cny"]),
            )
            if per is None:
                judge_missing += 1
            else:
                judge_cost += per
    for v in verdicts:
        print(
            f"[wiki-verdict] {v.qid} {v.strategy} correct={int(v.correct)} "
            f"grounded={int(v.grounded)} cited={int(v.citations_supported)}"
        )
    rows = [
        {
            "strategy": v.strategy,
            "category": v.category,
            "correct": v.correct,
            "grounded": v.grounded,
            "citations_supported": v.citations_supported,
        }
        for v in verdicts
    ]
    cross_categories = set(cast("list[str]", conditions["cross_categories"]))
    single_categories = set(cast("list[str]", conditions["single_categories"]))
    stats = {s: _strategy_stat(rows, s, cross_categories, single_categories) for s in _STRATEGIES}
    cost = sum(q_costs.values())
    verdict, reasons = decide(
        stats,
        citation_min=float(conditions["citation_support_min"]),
        cross_improve_pp=float(conditions["cross_doc_improve_pp"]),
        single_regress_pp=float(conditions["single_doc_regress_pp"]),
        overhead=Overhead(
            cost_registered=cost_registered,
            question_costs=[q_costs.get(str(q["id"]), 0.0) for q in questions],
            max_question_cost_cny=float(conditions["per_question_cost_cap_cny"]),
            missing_usage=missing_usage,
            maintenance_minutes=review_minutes,
            max_maintenance_minutes=float(conditions["wiki_maintenance_minutes_cap"]),
            record_problems=record_problems,
        ),
        sources_ok=sources_ok,
        config_issues_=[*coverage_gaps],
    )
    summary = summarize(verdicts)
    for strategy, item in summary.items():
        print(f"WIKI_STAT {strategy} {item}")
    print(
        f"WIKI_EVAL verdict={verdict} sources_changed={checks_changed} "
        f"cost_cny={cost:.4f} max_q_cost={max(q_costs.values(), default=0.0):.4f} "
        f"missing_usage={missing_usage} "
        f"judge_cost_cny={judge_cost:.4f} judge_missing_usage={judge_missing} "
        f"maintenance_minutes={review_minutes} price_version={PRICE_TABLE_VERSION} "
        f"switchovers={switches} "
        f"dataset={questions_file or 'retrieval_eval.yaml'} debug={conditions.get('debug')}"
    )
    if reasons:
        print(f"WIKI_EVAL reasons={reasons}")
    assert len(answers) == len(questions) * len(_STRATEGIES), "每题每种方式须各一份答案"
    assert sources_ok, "评测期间页面来源发生变化,本次结果作废"
