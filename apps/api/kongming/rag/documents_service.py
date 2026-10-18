"""document-rag 编排(024):上传分块落库 + small-to-big 检索。

与 RagService 并列的独立函数集(嵌入/召回复用既有端口形态);双通道隔离:
文档检索产物与 schema 通道(schema_hint/history_sql)互不混入。
"""

import hashlib
import logging
import re
import time
from collections.abc import Mapping
from typing import Any

from sqlalchemy.exc import IntegrityError

from kongming.rag.chunker import chunk_document
from kongming.rag.documents_repo import SqlDocumentRepo
from kongming.rag.models import rrf_fuse_ids
from kongming.rag.ports import EmbeddingPort
from kongming.rag.rerank import RerankError, RerankPort, record_rerank_usage

log = logging.getLogger(__name__)

_CHILD_TOP_K = 8
_PARENT_TOP_K = 5
_EMBED_BATCH = 64  # agent /internal/embedding 单批上限(R1-H1:超限 422 静默死)


class DocumentConflict(Exception):
    """同名并发上传唯一约束冲突(R1-L2:映射 409)。"""


async def upload_document(
    repo: SqlDocumentRepo,
    embedder: EmbeddingPort,
    *,
    tenant_id: str,
    datasource_key: str,
    doc_name: str,
    text: str,
) -> tuple[str, int, bool]:
    """上传编排:分块→embedding(子块)→覆盖式落库。

    返回 (doc_id, 块数, skipped);content_hash 相同 → skipped=True(幂等)。
    embedding 失败降级:子块无向量落库(仅全文路可召回,与 009 同哲学)。
    """
    digest = hashlib.sha256(text.encode()).hexdigest()
    existing = await repo.find_by_name(
        tenant_id=tenant_id, datasource_key=datasource_key, doc_name=doc_name
    )
    if existing is not None and existing["content_hash"] == digest:
        return str(existing["id"]), 0, True
    chunks = chunk_document(doc_name, text)
    child_texts = [str(c["content"]) for c in chunks if c["level"] == "CHILD"]
    vectors: list[list[float] | None] = []
    if child_texts:
        # R1-H1:按批循环(009 _embed_quietly 同款);单批失败仅该批降级
        for start in range(0, len(child_texts), _EMBED_BATCH):
            batch = child_texts[start : start + _EMBED_BATCH]
            try:
                got = await embedder.embed(batch)
                vectors.extend(got)
            except Exception:
                log.warning("文档 embedding 批次失败(该批降级纯全文路)", exc_info=True)
                vectors.extend([None] * len(batch))
    try:
        doc_id = await repo.replace_document(
            tenant_id=tenant_id,
            datasource_key=datasource_key,
            doc_name=doc_name,
            content_hash=digest,
            chunks=chunks,
            vectors=vectors,
        )
        await repo.commit()
    except IntegrityError:
        await repo.rollback()  # 并发兜底
        raise DocumentConflict(doc_name) from None
    return doc_id, len(chunks), False


def derive_citation_key(*, parent_id: str, doc_name: str) -> str:
    """068:稳定引用键(确定性派生;纯函数)。

    形态=可读前缀(文档名去扩展名)+ `-` + 定长哈希后缀(父块主键 SHA-256
    前 4 位十六进制)。同一父块任何一次召回派生结果恒定(与命中名次/路数/
    请求次序无关);由主键派生故改名/重切块不改变键(历史引用不腐坏)。
    """
    hasher = hashlib.sha256(parent_id.encode("utf-8")).hexdigest()[:4].upper()
    prefix = re.sub(r"\.[^.]+$", "", doc_name).strip() or "文档"
    return f"{prefix}-{hasher}"


def _rrf_parent_ids(fused: list[str], id_to_parent: Mapping[str, str]) -> list[str]:
    """RRF 名次→父块首现去重截断(未启用精排的既有路径;精排降级同源复用)。"""
    parent_ids: list[str] = []
    for cid in fused:
        pid = id_to_parent.get(str(cid))
        if pid and pid not in parent_ids:
            parent_ids.append(pid)
        if len(parent_ids) >= _PARENT_TOP_K:
            break
    return parent_ids


def rank_parents_by_score(
    pool_ids: list[str],
    scores: list[float],
    id_to_parent: Mapping[str, str],
    *,
    threshold: float,
    top_k: int,
) -> list[str]:
    """精排分→父块序(纯函数;同父多子取最高分,阈值先滤候选,平分保 RRF
    首现序——sorted 稳定性)。低于阈值的候选被过滤;过滤后空=空列表(调用方
    按既有「文档未命中」收口)。"""
    parent_scores: dict[str, float] = {}
    for cid, sc in zip(pool_ids, scores, strict=True):
        if sc < threshold:
            continue
        pid = id_to_parent.get(cid)
        if pid is None:
            continue
        cur = parent_scores.get(pid)
        parent_scores[pid] = sc if cur is None else max(cur, sc)
    ranked = sorted(parent_scores.items(), key=lambda kv: kv[1], reverse=True)
    return [pid for pid, _ in ranked[:top_k]]


async def _rerank_parent_ids(
    repo: SqlDocumentRepo,
    reranker: RerankPort,
    *,
    tenant_id: str,
    datasource_key: str,
    question: str,
    fused: list[str],
    id_to_parent: Mapping[str, str],
    fallback: list[str],
) -> list[str]:
    """精排级:融合后候选池扩量→子块正文打分→父块最高分截断 top-5。

    任何精排失败(超时/限流/不可用)降级 RRF-only 不阻断(design D4:单次
    调用粒度,不做熔断);成功/失败均留痕 sys_llm_usage(latency 只计出站
    打分本身,不含子块正文取回)。候选池无正文可打(全缺)按 RRF-only。"""
    pool = [str(cid) for cid in fused[: reranker.pool_topk]]
    texts_by_id = await repo.fetch_child_texts(
        tenant_id=tenant_id, datasource_key=datasource_key, child_ids=pool
    )
    pool = [cid for cid in pool if texts_by_id.get(cid, "").strip()]
    if not pool:
        return fallback
    started = time.monotonic()
    try:
        scores = await reranker.score(query=question, documents=[texts_by_id[c] for c in pool])
    except RerankError as e:
        log.warning("精排失败降级 RRF-only(reason=%s)", e.reason)
        await record_rerank_usage(
            model=reranker.model,
            tenant_id=tenant_id,
            latency_ms=int((time.monotonic() - started) * 1000),
            status="ERROR",
        )
        return fallback
    await record_rerank_usage(
        model=reranker.model,
        tenant_id=tenant_id,
        latency_ms=int((time.monotonic() - started) * 1000),
        status="SUCCESS",
    )
    return rank_parents_by_score(
        pool,
        scores,
        id_to_parent,
        threshold=reranker.score_threshold,
        top_k=_PARENT_TOP_K,
    )


async def retrieve_docs(
    repo: SqlDocumentRepo,
    embedder: EmbeddingPort,
    *,
    tenant_id: str,
    datasource_key: str,
    question: str,
    reranker: RerankPort | None = None,
) -> list[dict[str, str]]:
    """small-to-big 检索:子块向量+全文 RRF → (可选精排)→ 父块回填(top-5);
    任何失败降级空列表(不阻断主链路)。

    reranker=None 时与既有行为逐字一致(RRF 名次直接截断,零回归);
    注入精排级时候选池扩量(pool_topk)重排,引用键派生与名次无关(D5 零改动)。
    """
    try:
        vector_hits: list[dict[str, Any]] = []
        try:
            vector = (await embedder.embed([question]))[0]
            vector_hits = await repo.recall_children_vector(
                tenant_id=tenant_id,
                datasource_key=datasource_key,
                vector=vector,
                top_k=_CHILD_TOP_K,
            )
        except Exception:
            log.warning("文档向量路失败(降级纯全文)", exc_info=True)
        fulltext_hits = await repo.recall_children_fulltext(
            tenant_id=tenant_id,
            datasource_key=datasource_key,
            question=question,
            top_k=_CHILD_TOP_K,
        )
        fused = rrf_fuse_ids(
            [
                [str(h["id"]) for h in vector_hits],
                [str(h["id"]) for h in fulltext_hits],
            ],
            top=reranker.pool_topk if reranker is not None else _PARENT_TOP_K * 2,
        )
        # 子块 id → parent_id(保序)
        id_to_parent = {str(h["id"]): str(h["parent_id"]) for h in [*vector_hits, *fulltext_hits]}
        if reranker is not None and fused:
            parent_ids = await _rerank_parent_ids(
                repo,
                reranker,
                tenant_id=tenant_id,
                datasource_key=datasource_key,
                question=question,
                fused=fused,
                id_to_parent=id_to_parent,
                fallback=_rrf_parent_ids(fused, id_to_parent),
            )
        else:
            parent_ids = _rrf_parent_ids(fused, id_to_parent)
        parents = await repo.fetch_parents(tenant_id=tenant_id, parent_ids=parent_ids)
        by_id = {str(p["id"]): p for p in parents}
        out: list[dict[str, str]] = []
        for pid in parent_ids:
            if pid not in by_id:
                continue
            p = by_id[pid]
            ref = str(p["source_ref"])
            # 068:citation_key 由父块主键派生(改名/重切块不改变键)
            key = derive_citation_key(parent_id=pid, doc_name=ref.split(" §")[0])
            out.append(
                {
                    "citation_key": key,
                    "source_ref": ref,
                    "content": str(p["content"]),
                }
            )
        return out
    except Exception:
        log.warning("文档检索失败(降级空)", exc_info=True)
        return []
