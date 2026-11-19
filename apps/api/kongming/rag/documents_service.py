"""document-rag 编排:上传分块落库 + small-to-big 检索。

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
from kongming.rag.entity_extract import (
    EntityExtractError,
    EntityExtractorPort,
    normalize_entity_name,
)
from kongming.rag.models import rrf_fuse_ids
from kongming.rag.ports import EmbeddingPort
from kongming.rag.rerank import RerankError, RerankPort, record_rerank_usage

log = logging.getLogger(__name__)

_CHILD_TOP_K = 8
_PARENT_TOP_K = 5
_EMBED_BATCH = 64  # agent /internal/embedding 单批上限(超限 422 静默死)
_ENTITY_LEXICAL_LIMIT = 20  # 词法兜底实体名上限(防高频词刷爆 join 键集)
_ENTITY_NAME_MAX = 200  # 伴表 name/name_normalized 列宽;超限名跳过防整批落库失败
_ENTITY_MIN_SHARED = 2  # 入池门槛:仅共享 ≥2 个查询实体的子块并入(单一实体共现非桥接)


class DocumentConflict(Exception):
    """同名并发上传唯一约束冲突(映射 409)。"""


class DocNotFound(Exception):
    """文档不存在或不在该租户(映射 404 同形)。"""


async def _embed_child_chunks(
    embedder: EmbeddingPort, child_texts: list[str]
) -> list[list[float] | None]:
    """按 _EMBED_BATCH 分批向量化子块正文;单批失败仅该批置空(降级纯全文路)。"""
    vectors: list[list[float] | None] = []
    # 按批循环(_embed_quietly 同款);单批失败仅该批降级
    for start in range(0, len(child_texts), _EMBED_BATCH):
        batch = child_texts[start : start + _EMBED_BATCH]
        try:
            got = await embedder.embed(batch)
            vectors.extend(got)
        except Exception:
            log.warning("文档 embedding 批次失败(该批降级纯全文路)", exc_info=True)
            vectors.extend([None] * len(batch))
    return vectors


async def _enrich_after_save(
    repo: SqlDocumentRepo,
    entity_extractor: EntityExtractorPort,
    *,
    tenant_id: str,
    doc_id: str,
) -> None:
    """落库后走 enrich 同一路径补抽实体;任何失败降级无实体,不阻断上传。"""
    # 实体行需要块 id,分块产物无 id
    try:
        await enrich_document(repo, entity_extractor, tenant_id=tenant_id, doc_id=doc_id)
    except DocNotFound:  # 防御:同事务刚落库的文档不该缺行
        log.warning("文档实体抽取跳过(文档行缺失)", exc_info=True)
    except Exception:  # 文档已落库成功,抽取编排任何失败不阻断上传
        log.warning("文档实体抽取失败(降级无实体,不阻断上传)", exc_info=True)


async def upload_document(
    repo: SqlDocumentRepo,
    embedder: EmbeddingPort,
    *,
    tenant_id: str,
    datasource_key: str,
    doc_name: str,
    text: str,
    entity_extractor: EntityExtractorPort | None = None,
    sentence_boundary: bool = False,
) -> tuple[str, int, bool]:
    """上传编排:分块→embedding(子块)→覆盖式落库(→实体抽取,可选)。

    返回 (doc_id, 块数, skipped);content_hash 相同 → skipped=True(幂等)。
    embedding 失败降级:子块无向量落库(仅全文路可召回)。实体抽取为部署级
    可选档:失败降级该文档无实体,不阻断上传。
    """
    digest = hashlib.sha256(text.encode()).hexdigest()
    existing = await repo.find_by_name(
        tenant_id=tenant_id, datasource_key=datasource_key, doc_name=doc_name
    )
    if existing is not None and existing["content_hash"] == digest:
        return str(existing["id"]), 0, True
    chunks = chunk_document(doc_name, text, sentence_boundary=sentence_boundary)
    child_texts = [str(c["content"]) for c in chunks if c["level"] == "CHILD"]
    vectors = await _embed_child_chunks(embedder, child_texts) if child_texts else []
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
    if entity_extractor is not None:
        await _enrich_after_save(repo, entity_extractor, tenant_id=tenant_id, doc_id=doc_id)
    return doc_id, len(chunks), False


def _group_parent_children(
    chunks: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    """扁平分块行 → (父块, 子块列表) 分组(落库序父块紧跟其子块)。

    兼容两种行键:分块产物 level / DB 行 chunk_level。"""
    groups: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    parent: dict[str, Any] | None = None
    children: list[dict[str, Any]] = []
    for c in chunks:
        level = c.get("chunk_level") or c.get("level")
        if level == "PARENT":
            if parent is not None:
                groups.append((parent, children))
            parent = c
            children = []
        else:
            children.append(c)
    if parent is not None:
        groups.append((parent, children))
    return groups


async def _collect_entity_rows(
    extractor: EntityExtractorPort, parent_content: str, children: list[dict[str, Any]]
) -> list[tuple[str, str, str]]:
    """单个父块的子块抽取并转为 (子块 id, 实体名, 规范化名) 行;超长名跳过。"""
    child_ids = [str(c["id"]) for c in children]
    child_texts = [str(c["content"]) for c in children]
    per_child = await extractor.extract(parent_content, child_texts)
    rows: list[tuple[str, str, str]] = []
    for cid, names in zip(child_ids, per_child, strict=True):
        for name in names:
            # 规范化收敛在服务层(抽取器已规范化,此处防御性兜底)
            normalized = normalize_entity_name(name)
            if normalized and len(name) <= _ENTITY_NAME_MAX and len(normalized) <= _ENTITY_NAME_MAX:
                rows.append((cid, name, normalized))
    return rows


async def _extract_doc_entities(
    repo: SqlDocumentRepo,
    extractor: EntityExtractorPort,
    *,
    tenant_id: str,
    datasource_key: str,
    doc_id: str,
    chunks: list[dict[str, Any]],
) -> int:
    """父块批量抽取→子块粒度落库(覆盖式);失败降级该文档无实体。

    先全量抽取进内存、失败即中止,再单次落库——不会出现半抽取形态;
    每次出站留痕由抽取器承担(任务 doc_entity)。返回实体行数。"""
    rows: list[tuple[str, str, str]] = []
    try:
        for parent, children in _group_parent_children(chunks):
            rows.extend(await _collect_entity_rows(extractor, str(parent["content"]), children))
    except EntityExtractError as e:
        log.warning("文档实体抽取失败(降级无实体,reason=%s)", e.reason)
        return 0
    except Exception:
        log.warning("文档实体抽取失败(降级无实体)", exc_info=True)
        return 0
    if not rows:
        return 0
    try:
        n = await repo.replace_entities(
            tenant_id=tenant_id,
            datasource_key=datasource_key,
            document_id=doc_id,
            rows=rows,
        )
        await repo.commit()
        return n
    except Exception:
        log.warning("文档实体落库失败(降级无实体)", exc_info=True)
        await repo.rollback()
        return 0


async def enrich_document(
    repo: SqlDocumentRepo,
    extractor: EntityExtractorPort,
    *,
    tenant_id: str,
    doc_id: str,
) -> int:
    """既有文档实体覆盖式补抽(先清后抽);文档不存在抛 DocNotFound。

    数据源取自文档行(与上传同源);抽取失败降级返回 0(可观测留痕)。"""
    data = await repo.fetch_doc_for_enrich(tenant_id=tenant_id, doc_id=doc_id)
    if data is None:
        raise DocNotFound(doc_id)
    doc = data["doc"]
    chunks = data["chunks"]
    return await _extract_doc_entities(
        repo,
        extractor,
        tenant_id=tenant_id,
        datasource_key=str(doc["datasource_key"]),
        doc_id=str(doc["id"]),
        chunks=chunks,
    )


def derive_citation_key(*, parent_id: str, doc_name: str) -> str:
    """稳定引用键(确定性派生;纯函数)。

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
    按「文档未命中」收口)。"""
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

    任何精排失败(超时/限流/不可用)降级 RRF-only 不阻断(单次
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


async def _entity_child_hits(
    repo: SqlDocumentRepo,
    extractor: EntityExtractorPort,
    *,
    tenant_id: str,
    datasource_key: str,
    question: str,
    quota: int,
    lexical_limit: int = _ENTITY_LEXICAL_LIMIT,
) -> list[dict[str, Any]]:
    """实体扩展路:问句实体识别(LLM)+词法兜底 → 共享实体 join 子块。

    识别失败降级词法兜底(可观测留痕由抽取器承担);合并实体集不足门槛
    时整路短路为空(单一实体共现非桥接,join 注定被门槛滤空);命中行按
    共享实体数排序,配额截断。"""
    names: set[str] = set()
    try:
        names.update(await extractor.query_entities(question))
    except EntityExtractError as e:
        log.warning("问句实体识别失败(降级词法兜底,reason=%s)", e.reason)
    except Exception:
        log.warning("问句实体识别失败(降级词法兜底)", exc_info=True)
    try:
        names.update(
            await repo.match_entity_names(
                tenant_id=tenant_id,
                datasource_key=datasource_key,
                question=question,
                limit=lexical_limit,
            )
        )
    except Exception:
        log.warning("实体词法兜底失败", exc_info=True)
    if len(names) < _ENTITY_MIN_SHARED:
        return []
    try:
        return await repo.recall_children_by_entities(
            tenant_id=tenant_id,
            datasource_key=datasource_key,
            names=sorted(names),
            limit=quota,
            min_shared=_ENTITY_MIN_SHARED,
        )
    except Exception:
        log.warning("实体 join 失败(降级两路)", exc_info=True)
        return []


async def _recall_paths(
    repo: SqlDocumentRepo,
    embedder: EmbeddingPort,
    *,
    tenant_id: str,
    datasource_key: str,
    question: str,
    child_top_k: int,
    entity_extractor: EntityExtractorPort | None,
    entity_quota: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """两路召回(向量失败降级)+可选实体扩展路;返回 (向量路, 全文路, 实体路) 命中。"""
    vector_hits: list[dict[str, Any]] = []
    try:
        vector = (await embedder.embed([question]))[0]
        vector_hits = await repo.recall_children_vector(
            tenant_id=tenant_id,
            datasource_key=datasource_key,
            vector=vector,
            top_k=child_top_k,
        )
    except Exception:
        log.warning("文档向量路失败(降级纯全文)", exc_info=True)
    fulltext_hits = await repo.recall_children_fulltext(
        tenant_id=tenant_id,
        datasource_key=datasource_key,
        question=question,
        top_k=child_top_k,
    )
    entity_hits: list[dict[str, Any]] = []
    if entity_extractor is not None:
        entity_hits = await _entity_child_hits(
            repo,
            entity_extractor,
            tenant_id=tenant_id,
            datasource_key=datasource_key,
            question=question,
            quota=entity_quota,
        )
    return vector_hits, fulltext_hits, entity_hits


async def _recall_stage(
    repo: SqlDocumentRepo,
    embedder: EmbeddingPort,
    *,
    tenant_id: str,
    datasource_key: str,
    question: str,
    reranker: RerankPort | None = None,
    child_top_k: int = _CHILD_TOP_K,
    entity_extractor: EntityExtractorPort | None = None,
    entity_quota: int = 8,
) -> dict[str, Any]:
    """共享召回段:两路召回(+可选实体扩展路)→RRF 融合→(可选精排)父块序。

    主检索(retrieve_docs)与试跑(probe_docs)同源:同配置同输入时父块序
    一致。两路命中行携带原生分(score:向量路=余弦距离、全文路=ts_rank,
    量纲互异,仅试跑逐路展示消费,主检索不读)。实体扩展路未注入时第三路
    为空列表,RRF 融合结果与未引入该机制一致(逐字零回归)。"""
    vector_hits, fulltext_hits, entity_hits = await _recall_paths(
        repo,
        embedder,
        tenant_id=tenant_id,
        datasource_key=datasource_key,
        question=question,
        child_top_k=child_top_k,
        entity_extractor=entity_extractor,
        entity_quota=entity_quota,
    )
    fused = rrf_fuse_ids(
        [
            [str(h["id"]) for h in vector_hits],
            [str(h["id"]) for h in fulltext_hits],
            [str(h["id"]) for h in entity_hits],
        ],
        top=reranker.pool_topk if reranker is not None else _PARENT_TOP_K * 2,
    )
    # 子块 id → parent_id(保序)
    id_to_parent = {
        str(h["id"]): str(h["parent_id"]) for h in [*vector_hits, *fulltext_hits, *entity_hits]
    }
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
    return {
        "vector_hits": vector_hits,
        "fulltext_hits": fulltext_hits,
        "entity_hits": entity_hits,
        "fused": fused,
        "id_to_parent": id_to_parent,
        "parent_ids": parent_ids,
    }


async def _stage_with_parents(
    repo: SqlDocumentRepo,
    embedder: EmbeddingPort,
    *,
    tenant_id: str,
    datasource_key: str,
    question: str,
    reranker: RerankPort | None,
    entity_extractor: EntityExtractorPort | None,
    entity_quota: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """执行共享召回段并按父块序取回父块行;返回 (召回段结果, 父块行)。"""
    stage = await _recall_stage(
        repo,
        embedder,
        tenant_id=tenant_id,
        datasource_key=datasource_key,
        question=question,
        reranker=reranker,
        entity_extractor=entity_extractor,
        entity_quota=entity_quota,
    )
    parents = await repo.fetch_parents(tenant_id=tenant_id, parent_ids=stage["parent_ids"])
    return stage, parents


def _retrieved_docs(parent_ids: list[str], parents: list[dict[str, Any]]) -> list[dict[str, str]]:
    """父块行按父块序投影为检索产物(引用键由父块主键派生);缺行的父块跳过。"""
    by_id = {str(p["id"]): p for p in parents}
    out: list[dict[str, str]] = []
    for pid in parent_ids:
        if pid not in by_id:
            continue
        p = by_id[pid]
        ref = str(p["source_ref"])
        # citation_key 由父块主键派生(改名/重切块不改变键)
        key = derive_citation_key(parent_id=pid, doc_name=ref.split(" §")[0])
        out.append(
            {
                "citation_key": key,
                "source_ref": ref,
                "content": str(p["content"]),
            }
        )
    return out


async def retrieve_docs(
    repo: SqlDocumentRepo,
    embedder: EmbeddingPort,
    *,
    tenant_id: str,
    datasource_key: str,
    question: str,
    reranker: RerankPort | None = None,
    entity_extractor: EntityExtractorPort | None = None,
    entity_quota: int = 8,
) -> list[dict[str, str]]:
    """检索子块并合并向量与全文结果，可选执行精排后回填父块；失败返回空列表。"""
    try:
        stage, parents = await _stage_with_parents(
            repo,
            embedder,
            tenant_id=tenant_id,
            datasource_key=datasource_key,
            question=question,
            reranker=reranker,
            entity_extractor=entity_extractor,
            entity_quota=entity_quota,
        )
        return _retrieved_docs(stage["parent_ids"], parents)
    except Exception:
        log.warning("文档检索失败(降级空)", exc_info=True)
        return []


def _probe_parent_rows(
    parent_ids: list[str], parents: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """试跑父块回填(名次=父块序位);缺行的父块跳过。"""
    by_id = {str(p["id"]): p for p in parents}
    parent_rows: list[dict[str, Any]] = []
    for rank, pid in enumerate(parent_ids, start=1):
        p = by_id.get(pid)
        if p is None:
            continue
        ref = str(p["source_ref"])
        doc_name = ref.split(" §")[0]
        parent_rows.append(
            {
                "parent_id": pid,
                "seq": int(p["seq"]),
                "doc_name": doc_name,
                "source_ref": ref,
                "citation_key": derive_citation_key(parent_id=pid, doc_name=doc_name),
                "content": str(p["content"]),
                "rank": rank,
            }
        )
    return parent_rows


def _probe_paths(
    stage: dict[str, Any], entity_extractor: EntityExtractorPort | None
) -> dict[str, list[dict[str, Any]]]:
    """试跑逐路命中(原生分);实体路仅在注入抽取器时出现。"""

    def _hits(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "child_id": str(h["id"]),
                "parent_id": str(h["parent_id"]),
                "score": float(h["score"]),
            }
            for h in rows
        ]

    paths: dict[str, list[dict[str, Any]]] = {
        "vector": _hits(stage["vector_hits"]),
        "fulltext": _hits(stage["fulltext_hits"]),
    }
    if entity_extractor is not None:
        # 实体路原生分=共享实体数(match_count;量纲独立,仅逐路展示)
        paths["entity"] = [
            {
                "child_id": str(h["id"]),
                "parent_id": str(h["parent_id"]),
                "score": float(h["match_count"]),
            }
            for h in stage["entity_hits"]
        ]
    return paths


async def probe_docs(
    repo: SqlDocumentRepo,
    embedder: EmbeddingPort,
    *,
    tenant_id: str,
    datasource_key: str,
    question: str,
    reranker: RerankPort | None = None,
    parent_top_k: int = _PARENT_TOP_K,
    entity_extractor: EntityExtractorPort | None = None,
    entity_quota: int = 8,
) -> dict[str, Any]:
    """检索试跑(只读零副作用):逐路命中+融合名次+父块回填,与主检索同源。

    与 retrieve_docs 共享 _recall_stage(同配置同输入父块序一致);异常不吞
    (区别于主检索的降级空收口,试跑失败应显式暴露);不落业务数据不失效
    缓存(出站级用量留痕 sys_llm_usage 与主检索同口径)。实体路仅在注入
    抽取器时出现在逐路面(未启用=无该路痕迹)。"""
    stage = await _recall_stage(
        repo,
        embedder,
        tenant_id=tenant_id,
        datasource_key=datasource_key,
        question=question,
        reranker=reranker,
        entity_extractor=entity_extractor,
        entity_quota=entity_quota,
    )
    parent_ids = stage["parent_ids"][:parent_top_k]
    parents = await repo.fetch_parents(tenant_id=tenant_id, parent_ids=parent_ids)
    fused = stage["fused"]
    id_to_parent = stage["id_to_parent"]
    return {
        "paths": _probe_paths(stage, entity_extractor),
        "fused_children": [
            {"child_id": str(cid), "parent_id": id_to_parent.get(str(cid)), "rank": rank}
            for rank, cid in enumerate(fused, start=1)
        ],
        "parents": _probe_parent_rows(parent_ids, parents),
    }
