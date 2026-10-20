# pyright: reportPrivateUsage=false
"""全量向量重建命令测试(upgrade-rag-qwen3-halfvec-2560 任务 4.1)。

覆盖:dry-run 只报分批计划(零 embedding 调用、零写库);实跑按批 embed+
写回(2560 维假向量),可重跑(二次执行覆盖同一行);document_chunks 只重建
CHILD 行。真实 PG(kongming_test,迁移后 halfvec(2560) 列)。
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


async def test_document_chunks_only_child(session: AsyncSession) -> None:
    """document_chunks 只重建 CHILD 行(PARENT 不背向量)。"""
    mod = _load_module()
    tid = (
        await session.execute(
            text(
                "INSERT INTO public.sys_tenant (id, tenant_key, tenant_name, status)"
                " VALUES (gen_random_uuid(), 'rbc', 'rbc', 'ACTIVE') RETURNING id"
            )
        )
    ).scalar_one()
    doc = (
        await session.execute(
            text(
                "INSERT INTO rag.documents (id, tenant_id, datasource_key, doc_name,"
                " content_hash) VALUES (gen_random_uuid(), :t, 'default', 'p.md', 'h')"
                " RETURNING id"
            ),
            {"t": str(tid)},
        )
    ).scalar_one()
    for level in ("PARENT", "CHILD", "CHILD"):
        await session.execute(
            text(
                "INSERT INTO rag.document_chunks (id, document_id, tenant_id,"
                " datasource_key, chunk_level, seq, content)"
                " VALUES (gen_random_uuid(), :d, :t, 'default', :lv, 1, '块')"
            ),
            {"d": str(doc), "t": str(tid), "lv": level},
        )
    await session.commit()
    embedder = _FakeEmbedder()
    total, _ = await mod._rebuild_table(
        session, embedder, "document_chunks", batch_size=10, dry_run=False
    )
    assert total == 2  # 仅 CHILD
    assert sum(len(c) for c in embedder.calls) == 2
    n_parent_null = (
        await session.execute(
            text(
                "SELECT count(*) FROM rag.document_chunks"
                " WHERE chunk_level = 'PARENT' AND embedding IS NULL"
            )
        )
    ).scalar_one()
    assert n_parent_null == 1
