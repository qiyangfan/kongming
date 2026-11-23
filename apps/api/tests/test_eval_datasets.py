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


def _corpus(name: str) -> dict[str, str]:
    cfg = _dataset(_RETRIEVAL / name)
    root = _RETRIEVAL / str(cfg.get("corpus_dir", "corpus"))
    return {p.name: p.read_text(encoding="utf-8") for p in root.glob("*.md")}


def _parents(text: str, doc: str) -> list[dict[str, Any]]:
    return [cast("dict[str, Any]", c) for c in chunk_document(doc, text) if c["level"] == "PARENT"]


@pytest.mark.parametrize(
    "name",
    [
        "retrieval_eval.yaml",
        "formal_bm25.yaml",
        "retrieval_scale.yaml",
        "retrieval_scale_v1.yaml",
        "k8s_eval.yaml",
    ],
)
def test_gold_snippets_hit_exactly_one_parent(name: str) -> None:
    corpus = _corpus(name)
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


def _scale_gate_problems(name: str) -> list[str]:
    cfg = _dataset(_RETRIEVAL / name)
    questions = _questions(_RETRIEVAL / name)
    problems: list[str] = []
    categories = {str(q["category"]) for q in questions}
    missing = {"single_hop", "multi_hop_doc", "multi_hop_cross"} - categories
    if missing:
        problems.append(f"缺跳型 {missing}")
    if len(questions) < int(cfg["min_questions"]):
        problems.append(f"题量 {len(questions)} < {cfg['min_questions']}")
    multihop = [q for q in questions if str(q["category"]).startswith("multi_hop")]
    if len(multihop) < int(cfg["min_multihop"]):
        problems.append(f"多跳 {len(multihop)} < {cfg['min_multihop']}")
    return problems


@pytest.mark.parametrize("name", ["retrieval_scale.yaml", "retrieval_scale_v1.yaml"])
def test_retrieval_scale_dataset_gates(name: str) -> None:
    """实验室规模语料题集(含判别变体):题型覆盖、题量/多跳下限与负例集规模。"""
    assert _scale_gate_problems(name) == []
    negatives = _load(_RETRIEVAL / "negative_queries.yaml")["queries"]
    assert len(negatives) >= 10
    assert all(str(item.get("question") or "").strip() for item in negatives)


def test_retrieval_scale_v1_strips_store_names() -> None:
    """判别变体:门店手册正文不含店名(店名仅存于文档名),题集与正版同题。"""
    corpus = _corpus("retrieval_scale_v1.yaml")
    stores = {
        name.removeprefix("门店运营手册·").removesuffix(".md"): name
        for name in corpus
        if name.startswith("门店运营手册·")
    }
    assert len(stores) == 36
    leaked = {store: doc for store, doc in stores.items() if store in corpus[doc]}
    assert leaked == {}
    assert len(_questions(_RETRIEVAL / "retrieval_scale_v1.yaml")) == len(
        _questions(_RETRIEVAL / "retrieval_scale.yaml")
    )


def test_k8s_multihop_spans_distinct_parents() -> None:
    """真实文档多跳题:证据须落在不同父块,否则退化为单跳。"""
    corpus = _corpus("k8s_eval.yaml")
    problems: list[str] = []
    for question in _questions(_RETRIEVAL / "k8s_eval.yaml"):
        if not str(question["category"]).startswith("multi_hop"):
            continue
        keys: set[tuple[str, int]] = set()
        for gold in cast("list[dict[str, Any]]", question["gold"]):
            doc = str(gold["doc"])
            parents = _parents(corpus[doc], doc)
            for snippet in cast("list[str]", gold["snippets"]):
                keys.update((doc, i) for i, p in enumerate(parents) if snippet in str(p["content"]))
        if len(keys) < 2:
            problems.append(f"{question['id']} 证据父块={sorted(keys)}")
    assert problems == []
