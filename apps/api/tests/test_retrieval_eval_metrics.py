"""检索评测指标纯函数测试:Recall@k、全跳覆盖率、gold 片段→父块映射。

映射以 chunk_document 产物为输入(与真实语料重切后形态一致),
不依赖块 id,验证 (doc_name, parent_seq) join 键的确定性与边界。
"""

import pytest

from evals.retrieval_metrics import (
    Chunk,
    full_hop_coverage,
    gold_parent_keys,
    macro,
    recall_at_k,
)
from kongming.rag.chunker import chunk_document

_DOC = (
    "# 租赁政策\n\n会员租借影片须按期归还,逾期按日计费。\n\n"
    "# 会员等级\n\n等级由累计消费决定:金卡、银卡、铜卡三档。\n"
)


def _chunks() -> list[Chunk]:
    return chunk_document("会员手册.md", _DOC)


# ============ gold_parent_keys(片段→父块映射) ============


def test_gold_maps_snippets_to_parent_seqs() -> None:
    chunks = _chunks()
    parents = [c for c in chunks if c["level"] == "PARENT"]
    gold = gold_parent_keys(chunks, "会员手册.md", ["逾期按日计费", "金卡、银卡、铜卡"])
    assert gold == {
        ("会员手册.md", parents[0]["seq"]),
        ("会员手册.md", parents[1]["seq"]),
    }
    assert len(gold) == 2


def test_gold_duplicate_snippets_dedupe() -> None:
    gold = gold_parent_keys(_chunks(), "会员手册.md", ["逾期按日计费", "逾期按日计费"])
    assert len(gold) == 1


def test_gold_unknown_snippet_raises() -> None:
    with pytest.raises(ValueError, match="未落入任何父块"):
        gold_parent_keys(_chunks(), "会员手册.md", ["不存在的句子"])


def test_gold_empty_snippets_raises() -> None:
    with pytest.raises(ValueError, match="片段为空"):
        gold_parent_keys(_chunks(), "会员手册.md", [])


def test_gold_mapping_stable_across_rechunk() -> None:
    # 同名覆盖重传(同内容)后 seq 不变;前缀标题变化不影响映射
    before = gold_parent_keys(_chunks(), "会员手册.md", ["金卡、银卡、铜卡"])
    after = gold_parent_keys(
        chunk_document("会员手册.md", _DOC), "会员手册.md", ["金卡、银卡、铜卡"]
    )
    assert before == after


# ============ recall_at_k / full_hop_coverage ============


def test_recall_partial_hits() -> None:
    gold = {("d.md", 0), ("d.md", 4)}
    ranked = [("d.md", 0), ("d.md", 9), ("d.md", 4), ("d.md", 7)]
    assert recall_at_k(gold, ranked, k=3) == 1.0
    assert recall_at_k(gold, ranked, k=2) == 0.5
    assert recall_at_k(gold, ranked, k=4) == 1.0


def test_recall_empty_inputs() -> None:
    assert recall_at_k(set(), [("d.md", 0)], k=5) == 0.0
    assert recall_at_k({("d.md", 0)}, [], k=5) == 0.0


def test_full_hop_binary() -> None:
    gold = {("d.md", 0), ("d.md", 4)}
    assert full_hop_coverage(gold, [("d.md", 0), ("d.md", 4)], k=2) == 1.0
    assert full_hop_coverage(gold, [("d.md", 0), ("d.md", 4)], k=1) == 0.0
    assert full_hop_coverage({("d.md", 0)}, [("d.md", 9)], k=5) == 0.0


def test_macro_mean() -> None:
    assert macro([1.0, 0.5]) == 0.75
    assert macro([]) == 0.0
