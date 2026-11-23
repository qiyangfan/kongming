"""BM25 关键词排序(评测实验)验证:纯函数手算、白名单、仓储变体与范围隔离。

仓储用例连真库(与 test_rag_fulltext_config 同型);BM25 排名与 ts_rank
的差异用「短段一次命中 vs 长段多次命中」构造,两轮兜底与空结果
覆盖原匹配规则的复用。
"""

import math
from typing import Any, cast

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.config import (
    DEFAULT_FULLTEXT_RANKER,
    Settings,
    get_settings,
    validated_fulltext_ranker,
)
from kongming.rag.bm25 import bm25_score, idf
from kongming.rag.documents_bm25 import Bm25DocumentRepo
from kongming.rag.documents_factory import build_document_repo
from kongming.rag.documents_repo import SqlDocumentRepo

# ============ 纯函数:手算例子与参数公式 ============


def test_idf_matches_lucene_formula() -> None:
    """idf = ln(1 + (N - df + 0.5) / (df + 0.5))。"""
    assert idf(3, 2) == pytest.approx(math.log(1.0 + 1.5 / 2.5))
    assert idf(10, 0) == pytest.approx(math.log(1.0 + 10.5 / 0.5))


def test_bm25_score_hand_computed() -> None:
    """k1=1.2、b=0.75、avgdl=20、dl=10、tf=3、idf=ln(1.6)。

    norm = 1.2·(0.25 + 0.75·0.5) = 0.75
    score = idf·tf·(k1+1)/(tf+norm) = ln(1.6)·6.6/3.75
    """
    score = bm25_score(
        term_freqs={"alpha": 3},
        doc_len=10,
        avg_doc_len=20.0,
        term_idf={"alpha": math.log(1.6)},
    )
    assert score == pytest.approx(math.log(1.6) * 6.6 / 3.75)


def test_bm25_score_guards_empty_lengths() -> None:
    """空段/空语料计 0,不做除零。"""
    assert bm25_score(term_freqs={"a": 1}, doc_len=0, avg_doc_len=2.0, term_idf={"a": 1.0}) == 0.0
    assert bm25_score(term_freqs={"a": 1}, doc_len=1, avg_doc_len=0.0, term_idf={"a": 1.0}) == 0.0
    assert bm25_score(term_freqs={"a": 0}, doc_len=1, avg_doc_len=1.0, term_idf={"a": 1.0}) == 0.0


# ============ 白名单与装配 ============


def test_ranker_default_and_whitelist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KM_RAG_FULLTEXT_RANKER", raising=False)
    s = Settings(environment="dev")
    assert s.rag_fulltext_ranker == DEFAULT_FULLTEXT_RANKER == "ts_rank"
    assert s.require_fulltext_ranker() == "ts_rank"
    bm25_settings = Settings(environment="dev", rag_fulltext_ranker="bm25")
    assert bm25_settings.require_fulltext_ranker() == "bm25"
    with pytest.raises(ValueError, match="非法值"):
        validated_fulltext_ranker("bmw25")
    with pytest.raises(RuntimeError, match="非法值"):
        Settings(environment="dev", rag_fulltext_ranker="bmw25").require_fulltext_ranker()


def test_factory_switches_by_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """env=bm25 装入实验仓储;缺省走原仓储;非法值拒启。"""
    monkeypatch.setenv("KM_RAG_FULLTEXT_RANKER", "bm25")
    get_settings.cache_clear()
    try:
        repo = build_document_repo(cast("Any", object()))
        assert isinstance(repo, Bm25DocumentRepo)
    finally:
        get_settings.cache_clear()
    monkeypatch.delenv("KM_RAG_FULLTEXT_RANKER", raising=False)
    get_settings.cache_clear()
    try:
        repo = build_document_repo(cast("Any", object()))
        assert type(repo) is SqlDocumentRepo
    finally:
        get_settings.cache_clear()
    monkeypatch.setenv("KM_RAG_FULLTEXT_RANKER", "bmw25")
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="非法值"):
            build_document_repo(cast("Any", object()))
    finally:
        get_settings.cache_clear()


def test_get_settings_rejects_invalid_ranker(monkeypatch: pytest.MonkeyPatch) -> None:
    """非法排序配置启动即拒(与分词/重排配置同口径)。"""
    monkeypatch.setenv("KM_RAG_FULLTEXT_RANKER", "bmw25")
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="非法值"):
            get_settings()
    finally:
        monkeypatch.delenv("KM_RAG_FULLTEXT_RANKER", raising=False)
        get_settings.cache_clear()


# ============ 仓储变体(真库) ============


async def _mk_tenant(s: AsyncSession, key: str) -> str:
    tid = (
        await s.execute(
            text(
                "INSERT INTO public.sys_tenant (id, tenant_key, tenant_name)"
                " VALUES (gen_random_uuid(), :k, :k) RETURNING id"
            ),
            {"k": key},
        )
    ).scalar_one()
    await s.commit()
    return str(tid)


def _child(seq: int, content: str) -> dict[str, object]:
    return {
        "seq": seq,
        "level": "CHILD",
        "parent_seq": None,
        "source_ref": f"测试.md §{seq}",
        "content": content,
    }


async def _insert(
    repo: SqlDocumentRepo,
    *,
    tenant_id: str,
    doc_name: str,
    chunks: list[dict[str, object]],
    datasource_key: str = "default",
) -> None:
    await repo.replace_document(
        tenant_id=tenant_id,
        datasource_key=datasource_key,
        doc_name=doc_name,
        content_hash=doc_name,
        chunks=chunks,
        vectors=[],
    )
    await repo.commit()


async def test_bm25_ordering_differs_from_ts_rank(session: AsyncSession) -> None:
    """短段一次命中 vs 长段多次命中:ts_rank 选长段,BM25 长度归一选短段。"""
    tid = await _mk_tenant(session, "bm25_order")
    base = SqlDocumentRepo(session)
    bm25 = Bm25DocumentRepo(session)
    await _insert(base, tenant_id=tid, doc_name="短.md", chunks=[_child(1, "alpha")])
    long_text = "alpha alpha alpha " + "filler " * 200
    await _insert(base, tenant_id=tid, doc_name="长.md", chunks=[_child(1, long_text)])
    chunk_doc = {
        str(r["cid"]): str(r["doc_name"])
        for r in (
            await session.execute(
                text(
                    "SELECT c.id AS cid, d.doc_name FROM rag.document_chunks c"
                    " JOIN rag.documents d ON d.id = c.document_id"
                    " WHERE d.tenant_id=:t"
                ),
                {"t": tid},
            )
        )
        .mappings()
        .all()
    }

    base_top = await base.recall_children_fulltext(
        tenant_id=tid, datasource_key="default", question="alpha", top_k=1
    )
    bm25_top = await bm25.recall_children_fulltext(
        tenant_id=tid, datasource_key="default", question="alpha", top_k=1
    )
    # ts_rank 按词频,长段在前;BM25 按 tf/(段长归一),短段进入首位
    assert chunk_doc[str(base_top[0]["id"])] == "长.md"
    assert chunk_doc[str(bm25_top[0]["id"])] == "短.md"
    both = await bm25.recall_children_fulltext(
        tenant_id=tid, datasource_key="default", question="alpha", top_k=2
    )
    assert both[0]["score"] > both[1]["score"]


async def test_bm25_tie_breaks_by_seq(session: AsyncSession) -> None:
    """同分(同内容同长)按段落序号升序。"""
    tid = await _mk_tenant(session, "bm25_tie")
    repo = Bm25DocumentRepo(session)
    await _insert(
        repo,
        tenant_id=tid,
        doc_name="同分.md",
        chunks=[_child(2, "alpha"), _child(1, "alpha")],
    )
    hits = await repo.recall_children_fulltext(
        tenant_id=tid, datasource_key="default", question="alpha", top_k=5
    )
    assert [h["source_ref"] for h in hits] == ["测试.md §1", "测试.md §2"]


async def test_bm25_scope_isolated_from_other_tenant_and_datasource(
    session: AsyncSession,
) -> None:
    """范围外同关键词资料不改变候选与得分(跨租户、跨数据源)。"""
    tid = await _mk_tenant(session, "bm25_scope_a")
    other = await _mk_tenant(session, "bm25_scope_b")
    repo = Bm25DocumentRepo(session)
    await _insert(repo, tenant_id=tid, doc_name="A.md", chunks=[_child(1, "alpha")])
    await _insert(repo, tenant_id=tid, doc_name="B.md", chunks=[_child(1, "alpha beta")])
    before = await repo.recall_children_fulltext(
        tenant_id=tid, datasource_key="default", question="alpha", top_k=10
    )
    await _insert(
        repo,
        tenant_id=other,
        doc_name="X.md",
        chunks=[_child(1, "alpha " * 50)],
    )
    after = await repo.recall_children_fulltext(
        tenant_id=tid, datasource_key="default", question="alpha", top_k=10
    )
    assert [(h["id"], h["score"]) for h in before] == [(h["id"], h["score"]) for h in after]

    await _insert(
        repo,
        tenant_id=tid,
        datasource_key="ds2",
        doc_name="Y.md",
        chunks=[_child(1, "alpha " * 50)],
    )
    after_ds = await repo.recall_children_fulltext(
        tenant_id=tid, datasource_key="default", question="alpha", top_k=10
    )
    assert [(h["id"], h["score"]) for h in after_ds] == [(h["id"], h["score"]) for h in before]


async def test_bm25_keeps_two_round_matching(session: AsyncSession) -> None:
    """AND 零命中落内容词兜底轮;兜底候选同样只按范围内资料评分。"""
    tid = await _mk_tenant(session, "bm25_rounds")
    repo = Bm25DocumentRepo(session)
    await _insert(repo, tenant_id=tid, doc_name="A.md", chunks=[_child(1, "alpha")])
    await _insert(repo, tenant_id=tid, doc_name="B.md", chunks=[_child(1, "beta")])
    hits = await repo.recall_children_fulltext(
        tenant_id=tid, datasource_key="default", question="alpha gamma", top_k=5
    )
    assert len(hits) == 1 and hits[0]["source_ref"] == "测试.md §1"

    empty = await repo.recall_children_fulltext(
        tenant_id=tid, datasource_key="default", question="nothing", top_k=5
    )
    assert empty == []
