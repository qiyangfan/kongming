"""文档检索质量评测指标纯函数(retrieval-eval spec):零依赖、零 IO。

gold 以「文档名 + 必含文本片段」表达,运行时经分块产物确定性映射到
父块集合 (doc_name, parent_seq)——映射不依赖块 id,语料重切(同内容
覆盖重传)后自动跟随。判定一律在父块级(回填单位即引用单位);
ranked 为试跑端点返回的父块键名次序。
"""

from collections.abc import Sequence
from typing import cast

GoldKey = tuple[str, int]  # (doc_name, parent_seq)
Chunk = dict[str, object]  # 分块产物行(chunk_document 返回形态)


def gold_parent_keys(doc_chunks: list[Chunk], doc_name: str, snippets: list[str]) -> set[GoldKey]:
    """片段集 → 父块键集合;片段须唯一落入某父块,否则评测集非法(拒绝启动)。"""
    if not snippets:
        raise ValueError(f"{doc_name}: gold 片段为空")
    parents = [c for c in doc_chunks if c.get("level") == "PARENT"]
    keys: set[GoldKey] = set()
    for s in snippets:
        hit = next((p for p in parents if s in str(p.get("content", ""))), None)
        if hit is None:
            raise ValueError(f"{doc_name}: 片段未落入任何父块: {s[:30]!r}")
        keys.add((doc_name, int(cast("int", hit["seq"]))))
    return keys


def recall_at_k(gold: set[GoldKey], ranked: Sequence[GoldKey], k: int) -> float:
    """父块级 Recall@k = gold 命中数 / gold 总数;gold 空集计 0(非法集已在映射侧拦截)。"""
    if not gold or k <= 0:
        return 0.0
    return len(gold.intersection(ranked[:k])) / len(gold)


def full_hop_coverage(gold: set[GoldKey], ranked: Sequence[GoldKey], k: int) -> bool:
    """全跳覆盖率:gold 父块是否全部进入 top-k(漏跳即 0,衡量证据链完整性)。"""
    if not gold or k <= 0:
        return False
    return gold.issubset(set(ranked[:k]))


def macro(scores: list[float]) -> float:
    """宏观平均;空输入计 0(调用方以评测集自检下限保证非空)。"""
    return sum(scores) / len(scores) if scores else 0.0
