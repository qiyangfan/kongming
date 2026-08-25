"""document-rag 编排(024):上传分块落库 + small-to-big 检索。

与 RagService 并列的独立函数集(嵌入/召回复用既有端口形态);双通道隔离:
文档检索产物与 schema 通道(schema_hint/history_sql)互不混入。
"""

import hashlib
import logging
from typing import Any

from sqlalchemy.exc import IntegrityError

from kongming.rag.chunker import chunk_document
from kongming.rag.documents_repo import SqlDocumentRepo
from kongming.rag.models import rrf_fuse_ids
from kongming.rag.ports import EmbeddingPort

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


async def retrieve_docs(
    repo: SqlDocumentRepo,
    embedder: EmbeddingPort,
    *,
    tenant_id: str,
    datasource_key: str,
    question: str,
) -> list[dict[str, str]]:
    """small-to-big 检索:子块向量+全文 RRF → 父块回填(top-5);
    任何失败降级空列表(不阻断主链路)。"""
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
            top=_PARENT_TOP_K * 2,  # 子块级融合放宽,父块去重后截 top-5
        )
        # 子块 id → parent_id(保序)
        id_to_parent = {str(h["id"]): str(h["parent_id"]) for h in [*vector_hits, *fulltext_hits]}
        parent_ids: list[str] = []
        for cid in fused:
            pid = id_to_parent.get(str(cid))
            if pid and pid not in parent_ids:
                parent_ids.append(pid)
            if len(parent_ids) >= _PARENT_TOP_K:
                break
        parents = await repo.fetch_parents(tenant_id=tenant_id, parent_ids=parent_ids)
        by_id = {str(p["id"]): p for p in parents}
        return [
            {"source_ref": str(by_id[pid]["source_ref"]), "content": str(by_id[pid]["content"])}
            for pid in parent_ids
            if pid in by_id
        ]
    except Exception:
        log.warning("文档检索失败(降级空)", exc_info=True)
        return []
