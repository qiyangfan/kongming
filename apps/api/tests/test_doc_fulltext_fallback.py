"""文档全文兜底轮零分命中剔除:部分命中(AND 零命中)不进融合与试跑。"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.rag.documents_repo import SqlDocumentRepo


async def _seed_chunk(session: AsyncSession, tenant: str, content: str) -> None:
    doc = (
        await session.execute(
            text(
                "INSERT INTO rag.documents (id, tenant_id, datasource_key, doc_name, content_hash)"
                " VALUES (gen_random_uuid(), :t, 'default', 'partial.md', 'h') RETURNING id"
            ),
            {"t": tenant},
        )
    ).scalar_one()
    await session.execute(
        text(
            "INSERT INTO rag.document_chunks (id, document_id, tenant_id, datasource_key,"
            " chunk_level, seq, content, tsv)"
            " VALUES (gen_random_uuid(), :d, :t, 'default', 'CHILD', 1, :c,"
            " to_tsvector('simple', :c))"
        ),
        {"d": str(doc), "t": tenant, "c": content},
    )
    await session.commit()


async def _tenant(session: AsyncSession, key: str) -> str:
    row = await session.execute(
        text(
            "INSERT INTO public.sys_tenant (id, tenant_key, tenant_name, status)"
            " VALUES (gen_random_uuid(), :k, :k, 'ACTIVE') RETURNING id"
        ),
        {"k": key},
    )
    await session.commit()
    return str(row.scalar_one())


async def test_partial_match_fallback_dropped(session: AsyncSession) -> None:
    """仅含问句部分内容词的命中(ts_rank 全问句为 0 量级)不返回。"""
    tid = await _tenant(session, "fb")
    await _seed_chunk(session, tid, "customer data")
    repo = SqlDocumentRepo(session)
    rows = await repo.recall_children_fulltext(
        tenant_id=tid, datasource_key="default", question="customer invoice", top_k=5
    )
    assert rows == []


async def test_full_match_still_returned(session: AsyncSession) -> None:
    """AND 命中路径不受影响。"""
    tid = await _tenant(session, "fb2")
    await _seed_chunk(session, tid, "customer data")
    repo = SqlDocumentRepo(session)
    rows = await repo.recall_children_fulltext(
        tenant_id=tid, datasource_key="default", question="customer data", top_k=5
    )
    assert len(rows) == 1 and rows[0]["id"]
