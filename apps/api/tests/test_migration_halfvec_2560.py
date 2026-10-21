# pyright: reportPrivateUsage=false
"""0058 halfvec(2560) 迁移测试。

覆盖:四列升级为 halfvec(2560)+HNSW halfvec_cosine_ops 索引(含两条部分
索引谓词原样保留)、旧向量清空而原文保留、downgrade 恢复 vector(1536)
列与索引且同样不恢复旧向量。迁移演练走 conftest.migrated_tmp_db 一次性
临时库(dropped 列占位零累积)。
"""

import asyncio

from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from alembic import command
from tests.conftest import migrated_tmp_db

# (表, 索引名, 部分索引谓词)
_TABLES = (
    ("rag.table_descriptions", "idx_table_desc_embedding", None),
    ("rag.query_history", "idx_query_history_embedding", None),
    ("public.user_memory", "idx_user_memory_embedding", "dormant = false"),
    ("rag.document_chunks", "idx_document_chunks_embedding", "chunk_level = 'CHILD'"),
)


async def _run_alembic(session: AsyncSession, cfg: Config, target: str) -> None:
    await session.rollback()
    if target == "head":
        await asyncio.to_thread(command.upgrade, cfg, "head")
    else:
        await asyncio.to_thread(command.downgrade, cfg, target)


async def _column_type(session: AsyncSession, table: str) -> str:
    return str(
        (
            await session.execute(
                text(
                    "SELECT format_type(a.atttypid, a.atttypmod) FROM pg_attribute a"
                    " JOIN pg_class c ON c.oid = a.attrelid"
                    " JOIN pg_namespace n ON n.oid = c.relnamespace"
                    " WHERE n.nspname = split_part(:t, '.', 1)"
                    "  AND c.relname = split_part(:t, '.', 2) AND a.attname = 'embedding'"
                ),
                {"t": table},
            )
        ).scalar_one()
    )


def _pred_ok(where: str, pred: str) -> bool:
    """部分索引谓词在位(pg_get_expr 会带 ::text 转型,按列名+常量双包含验)。"""
    col = where.replace("WHERE", "").split("=")[0].strip()
    value = where.split("=")[1].strip().strip("'")
    return col in pred and value in pred


async def _index_ops(session: AsyncSession, index: str) -> tuple[str, str | None]:
    row = (
        await session.execute(
            text(
                "SELECT am.amname, pg_get_expr(i.indpred, i.indrelid)"
                " FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid"
                " JOIN pg_am am ON am.oid = c.relam WHERE c.relname = :n"
            ),
            {"n": index},
        )
    ).one()
    return str(row[0]), (str(row[1]) if row[1] is not None else None)


async def _seed_vectors(session: AsyncSession) -> None:
    """四表各插一行带 1536 维向量的数据(升级前形态,验证清空语义)。"""
    vec = "[" + ",".join(["0.1"] * 1536) + "]"
    tid = (
        await session.execute(
            text(
                "INSERT INTO public.sys_tenant (id, tenant_key, tenant_name, status)"
                " VALUES (gen_random_uuid(), 'probe', 'probe', 'ACTIVE') RETURNING id"
            )
        )
    ).scalar_one()
    await session.execute(
        text(
            "INSERT INTO rag.table_descriptions"
            " (id, tenant_id, datasource_key, schema_name, table_name, embedding)"
            " VALUES (gen_random_uuid(), :t, 'default', 'public',"
            "  'probe_t', CAST(:v AS vector))"
        ),
        {"t": str(tid), "v": vec},
    )
    await session.execute(
        text(
            "INSERT INTO rag.query_history (id, tenant_id, datasource_key, question,"
            " sql_text, embedding) VALUES (gen_random_uuid(), :t,"
            " 'default', '探针问句', 'SELECT 1', CAST(:v AS vector))"
        ),
        {"t": str(tid), "v": vec},
    )
    await session.execute(
        text(
            "INSERT INTO public.user_memory (id, user_id, tenant_id, content,"
            " confidence, source, embedding)"
            " VALUES (gen_random_uuid(), gen_random_uuid(), :t,"
            "  '探针记忆', 0.9, 'EXPLICIT', CAST(:v AS vector))"
        ),
        {"t": str(tid), "v": vec},
    )
    await session.execute(
        text(
            "INSERT INTO rag.documents (id, tenant_id, datasource_key, doc_name, content_hash)"
            " VALUES (gen_random_uuid(), :t, 'default', 'probe.md', 'h')"
        ),
        {"t": str(tid)},
    )
    await session.execute(
        text(
            "INSERT INTO rag.document_chunks (id, document_id, tenant_id,"
            " datasource_key, chunk_level, seq, content, embedding)"
            " SELECT gen_random_uuid(), d.id, d.tenant_id, d.datasource_key,"
            "  'CHILD', 1, '探针子块', CAST(:v AS vector) FROM rag.documents d"
            " WHERE d.doc_name = 'probe.md'"
        ),
        {"v": vec},
    )
    await session.commit()


async def test_migration_0058_upgrade_halfvec_2560() -> None:
    """升级:四列 halfvec(2560)+HNSW halfvec_cosine_ops(部分索引谓词保留),
    旧向量清空、原文与业务字段保留。"""
    async with migrated_tmp_db() as (session, cfg):
        # 先降到 0057(vector(1536) 形态)再造 1536 维存量数据,再升 head
        await _run_alembic(session, cfg, "0057")
        await _seed_vectors(session)
        n_vec = (
            await session.execute(
                text("SELECT count(*) FROM rag.table_descriptions WHERE embedding IS NOT NULL")
            )
        ).scalar_one()
        assert n_vec == 1

        await _run_alembic(session, cfg, "head")

        for table, index, where in _TABLES:
            assert await _column_type(session, table) == "halfvec(2560)", table
            ops, pred = await _index_ops(session, index)
            assert ops == "hnsw", index
            # 索引表达式含 halfvec_cosine_ops
            expr = (
                await session.execute(
                    text(
                        "SELECT pg_get_indexdef(i.indexrelid) FROM pg_index i"
                        " JOIN pg_class c ON c.oid = i.indexrelid WHERE c.relname = :n"
                    ),
                    {"n": index},
                )
            ).scalar_one()
            assert "halfvec_cosine_ops" in str(expr), index
            if where is not None:
                assert pred is not None and _pred_ok(where, pred), (index, pred)
        # 旧向量清空、原文保留
        assert (
            await session.execute(
                text("SELECT count(*) FROM rag.table_descriptions WHERE embedding IS NULL")
            )
        ).scalar_one() == 1
        assert (
            await session.execute(text("SELECT table_name FROM rag.table_descriptions"))
        ).scalar_one() == "probe_t"
        assert (
            await session.execute(text("SELECT content FROM rag.document_chunks"))
        ).scalar_one() == "探针子块"


async def test_migration_0058_downgrade_restores_vector_1536() -> None:
    """降级:恢复 vector(1536) 列与 vector_cosine_ops 索引,同样不恢复旧向量。"""
    async with migrated_tmp_db() as (session, cfg):
        await _run_alembic(session, cfg, "0057")
        await _seed_vectors(session)
        await _run_alembic(session, cfg, "head")
        await _run_alembic(session, cfg, "0057")

        for table, index, where in _TABLES:
            assert await _column_type(session, table) == "vector(1536)", table
            expr = (
                await session.execute(
                    text(
                        "SELECT pg_get_indexdef(i.indexrelid) FROM pg_index i"
                        " JOIN pg_class c ON c.oid = i.indexrelid WHERE c.relname = :n"
                    ),
                    {"n": index},
                )
            ).scalar_one()
            assert "vector_cosine_ops" in str(expr), index
            if where is not None:
                _, pred = await _index_ops(session, index)
                assert pred is not None and _pred_ok(where, pred), (index, pred)
        # 原文仍在,向量不恢复(设计 D3)
        assert (
            await session.execute(
                text("SELECT count(*) FROM rag.table_descriptions WHERE embedding IS NULL")
            )
        ).scalar_one() == 1
