"""评测题集数据完整性:gold 片段唯一落入父块、题型与门禁下限对齐(默认门禁)。

评测运行时的自检需要起栈;这里用同一纯函数在单测层兜底,题集或语料改动
造成片段失效/题型缺口时先于评测暴露。
"""

from collections import Counter
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

from kongming.rag.chunker import chunk_document

_EVALS = Path(__file__).resolve().parent.parent / "evals"
_RETRIEVAL = _EVALS / "retrieval"


def _load(path: Path) -> dict[str, Any]:
    loaded = cast("object", yaml.safe_load(path.read_text(encoding="utf-8")))
    assert isinstance(loaded, dict)
    return cast("dict[str, Any]", loaded)


def _questions(path: Path) -> list[dict[str, Any]]:
    return cast("list[dict[str, Any]]", _load(path)["questions"])


def _dataset(path: Path) -> dict[str, Any]:
    return cast("dict[str, Any]", _load(path)["dataset"])


def _corpus() -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in (_RETRIEVAL / "corpus").glob("*.md")}


def _parents(text: str, doc: str) -> list[dict[str, Any]]:
    return [cast("dict[str, Any]", c) for c in chunk_document(doc, text) if c["level"] == "PARENT"]


@pytest.mark.parametrize("name", ["retrieval_eval.yaml", "formal_bm25.yaml"])
def test_gold_snippets_hit_exactly_one_parent(name: str) -> None:
    corpus = _corpus()
    problems: list[str] = []
    for question in _questions(_RETRIEVAL / name):
        for gold in cast("list[dict[str, Any]]", question["gold"]):
            doc = str(gold["doc"])
            for snippet in cast("list[str]", gold["snippets"]):
                hits = sum(snippet in str(p["content"]) for p in _parents(corpus[doc], doc))
                if hits != 1:
                    problems.append(f"{question['id']} {doc} {snippet!r} 命中父块数={hits}")
    assert problems == []


def test_retrieval_eval_dataset_gates() -> None:
    """跳型覆盖、题量与多跳下限(与评测运行时自检同值)。"""
    cfg = _dataset(_RETRIEVAL / "retrieval_eval.yaml")
    questions = _questions(_RETRIEVAL / "retrieval_eval.yaml")
    categories = {str(q["category"]) for q in questions}
    assert {"single_hop", "multi_hop_doc", "multi_hop_cross"} <= categories
    assert len(questions) >= int(cfg["min_questions"])
    multihop = [q for q in questions if str(q["category"]).startswith("multi_hop")]
    assert len(multihop) >= int(cfg["min_multihop"])


def test_bm25_dataset_gates_and_subtypes() -> None:
    """题量/多跳下限与精确词 subtype 覆盖,门禁值与 bm25_eval.yaml 单源。"""
    cfg = _dataset(_RETRIEVAL / "formal_bm25.yaml")
    questions = _questions(_RETRIEVAL / "formal_bm25.yaml")
    required = cast("dict[str, int]", _load(_RETRIEVAL / "bm25_eval.yaml")["min_subtype_counts"])
    assert len(questions) >= int(cfg["min_questions"])
    multihop = [q for q in questions if str(q["category"]).startswith("multi_hop")]
    assert len(multihop) >= int(cfg["min_multihop"])
    counts: Counter[str] = Counter(
        str(q["subtype"]) for q in questions if "precise" in cast("list[str]", q["groups"])
    )
    gaps = {
        key: (counts.get(key, 0), need)
        for key, need in required.items()
        if counts.get(key, 0) < int(need)
    }
    assert gaps == {}
