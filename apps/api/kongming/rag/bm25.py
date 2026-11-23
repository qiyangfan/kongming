"""BM25 关键词排序纯函数(对齐 Apache Lucene BM25Similarity 缺省参数)。

评测实验专用,生产保持 ts_rank。词频与段长由调用方从入库 tsv 的
位置信息取得,统计范围由调用方限定在所选租户与数据源。
"""

import math
from collections.abc import Mapping

K1 = 1.2
B = 0.75


def idf(n_docs: int, df: int) -> float:
    """Lucene 稀有词权重:ln(1 + (N - df + 0.5) / (df + 0.5))。"""
    return math.log(1.0 + (n_docs - df + 0.5) / (df + 0.5))


def bm25_score(
    *,
    term_freqs: Mapping[str, int],
    doc_len: int,
    avg_doc_len: float,
    term_idf: Mapping[str, float],
) -> float:
    """单段 BM25 得分:Σ idf(t)·tf·(k1+1)/(tf + k1·(1-b+b·dl/avgdl))。"""
    if doc_len <= 0 or avg_doc_len <= 0:
        return 0.0
    norm = K1 * (1.0 - B + B * (doc_len / avg_doc_len))
    return sum(
        term_idf.get(term, 0.0) * (tf * (K1 + 1.0)) / (tf + norm)
        for term, tf in term_freqs.items()
        if tf > 0
    )
