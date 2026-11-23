# pyright: reportPrivateUsage=false
"""全量向量重建命令测试。

覆盖:dry-run 只报分批计划(零 embedding 调用、零写库);实跑按批 embed+
写回(2560 维假向量),可重跑(二次执行覆盖同一行);document_chunks 子块
重算 embedding+tsv、父块只重算 tsv,索引文本=文档名+正文。真实 PG
(kongming_test,迁移后 halfvec(2560) 列)。
"""

import importlib.util
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "rebuild_embeddings.py"


def _load_module() -> Any:
    spec = importlib.util.spec_from_file_location("rebuild_embeddings", _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeEmbedder:
    """剧本化 embedder:记录调用,恒返 2560 维向量。"""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[0.25] * 2560 for _ in texts]


async def _seed_history(session: AsyncSession, n: int) -> None:
    tid = (
        await session.execute(
            text(
                "INSERT INTO public.sys_tenant (id, tenant_key, tenant_name, status)"
                " VALUES (gen_random_uuid(), 'rb', 'rb', 'ACTIVE') RETURNING id"
            )
        )
    ).scalar_one()
    for i in range(n):
        await session.execute(
            text(
                "INSERT INTO rag.query_history (id, tenant_id, datasource_key, question, sql_text)"
                " VALUES (gen_random_uuid(), :t, 'default', :q, 'SELECT 1')"
            ),
            {"t": str(tid), "q": f"探针问句{i}"},
        )
    await session.commit()


async def test_dry_run_no_calls_no_writes(session: AsyncSession) -> None:
    """dry-run:分批计划可观测,零 embedding 调用、零写库。"""
    mod = _load_module()
    await _seed_history(session, 3)
    embedder = _FakeEmbedder()
    total, batches = await mod._rebuild_table(
        session, embedder, "query_history", batch_size=2, dry_run=True
    )
    assert (total, batches) == (3, 2)
    assert embedder.calls == []
    n_null = (
        await session.execute(
            text("SELECT count(*) FROM rag.query_history WHERE embedding IS NULL")
        )
    ).scalar_one()
    assert n_null == 3  # 未写库


async def test_rebuild_writes_vectors_and_rerunnable(session: AsyncSession) -> None:
    """实跑:按批 embed+写回(2560 维);二次执行覆盖同一行(可重跑)。"""
    mod = _load_module()
    await _seed_history(session, 3)
    embedder = _FakeEmbedder()
    total, batches = await mod._rebuild_table(
        session, embedder, "query_history", batch_size=2, dry_run=False
    )
    assert (total, batches) == (3, 2)
    assert [len(c) for c in embedder.calls] == [2, 1]  # 分批形态
    n_set = (
        await session.execute(
            text("SELECT count(*) FROM rag.query_history WHERE embedding IS NOT NULL")
        )
    ).scalar_one()
    assert n_set == 3
    # 重跑:覆盖同一行,批次仍按全量(缓存命中由 AgentRagClient 承担)
    embedder2 = _FakeEmbedder()
    await mod._rebuild_table(session, embedder2, "query_history", batch_size=2, dry_run=False)
    assert sum(len(c) for c in embedder2.calls) == 3
    n_set2 = (
        await session.execute(
            text("SELECT count(*) FROM rag.query_history WHERE embedding IS NOT NULL")
        )
    ).scalar_one()
    assert n_set2 == 3


async def test_document_chunks_rebuild_context_and_tsv(session: AsyncSession) -> None:
    """document_chunks:子块重算 embedding+tsv、父块只重算 tsv;content 原文不动。"""
    mod = _load_module()
    tid = (
        await session.execute(
            text(
                "INSERT INTO public.sys_tenant (id, tenant_key, tenant_name, status)"
                " VALUES (gen_random_uuid(), 'rbc', 'rbc', 'ACTIVE') RETURNING id"
            )
        )
    ).scalar_one()
    doc_name = "storex policy.md"
    doc = (
        await session.execute(
            text(
                "INSERT INTO rag.documents (id, tenant_id, datasource_key, doc_name,"
                " content_hash) VALUES (gen_random_uuid(), :t, 'default', :n, 'h')"
                " RETURNING id"
            ),
            {"t": str(tid), "n": doc_name},
        )
    ).scalar_one()
    chunks = ((1, "PARENT", "块一"), (2, "CHILD", "子块一"), (3, "CHILD", "子块二"))
    for seq, level, content in chunks:
        await session.execute(
            text(
                "INSERT INTO rag.document_chunks (id, document_id, tenant_id,"
                " datasource_key, chunk_level, seq, content)"
                " VALUES (gen_random_uuid(), :d, :t, 'default', :lv, :seq, :c)"
            ),
            {"d": str(doc), "t": str(tid), "lv": level, "seq": seq, "c": content},
        )
    await session.commit()
    embedder = _FakeEmbedder()
    total, batches = await mod._rebuild_table(
        session, embedder, "document_chunks", batch_size=10, dry_run=True
    )
    assert (total, batches) == (3, 1)  # 计数覆盖父块与子块
    assert embedder.calls == []  # dry-run 零调用零写库

    total, _ = await mod._rebuild_table(
        session, embedder, "document_chunks", batch_size=10, dry_run=False
    )
    assert total == 3
    texts = [text for call in embedder.calls for text in call]
    assert len(texts) == 2  # 仅子块进向量
    assert all(t.startswith(f"{doc_name}\n") for t in texts)

    rows = (
        await session.execute(
            text(
                "SELECT chunk_level, content, embedding IS NOT NULL AS has_vec,"
                " tsv @@ plainto_tsquery('simple', 'storex') AS hit"
                " FROM rag.document_chunks WHERE document_id=:d ORDER BY seq"
            ),
            {"d": str(doc)},
        )
    ).all()
    assert [r.has_vec for r in rows] == [False, True, True]  # 父块不背向量
    assert all(r.hit for r in rows)  # 全文索引含文档名词项
    assert all("storex" not in str(r.content) for r in rows)  # 原文无前缀
