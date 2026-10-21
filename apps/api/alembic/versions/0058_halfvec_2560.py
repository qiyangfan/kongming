"""将派生向量列升级为 2560 维 halfvec 并重建 HNSW 索引。旧向量会清空；降级也不会恢复旧向量，需重新运行向量重建。"""

from alembic import op

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None

# (表, 无限定索引名, 部分索引谓词或 None);DROP INDEX 必须带 schema 限定——
# rag schema 不在 search_path,无限定名会静默不命中(IF EXISTS 吞掉不存在)
_TABLES = (
    ("rag.table_descriptions", "rag.idx_table_desc_embedding", None),
    ("rag.query_history", "rag.idx_query_history_embedding", None),
    ("public.user_memory", "public.idx_user_memory_embedding", "WHERE dormant = false"),
    (
        "rag.document_chunks",
        "rag.idx_document_chunks_embedding",
        "WHERE chunk_level = 'CHILD'",
    ),
)


def _index_ddl(table: str, index: str, where: str | None, *, half: bool) -> str:
    ops = "halfvec_cosine_ops" if half else "vector_cosine_ops"
    clause = f" {where}" if where else ""
    # CREATE INDEX 无限定名即落在表的 schema(与 baseline 建法一致)
    short = index.split(".", 1)[1]
    return f"CREATE INDEX {short} ON {table} USING hnsw (embedding {ops}){clause}"


def upgrade() -> None:
    for table, index, where in _TABLES:
        op.execute(f"DROP INDEX IF EXISTS {index}")
        # USING NULL:pgvector 注册了 vector→halfvec 函数 cast,但对 1536 维
        # 存量数据会维度校验失败(expected 2560 dimensions);旧向量按设计 D3
        # 丢弃(原文保留、重建恢复),USING NULL 即「全部置空」的显式表达
        op.execute(f"ALTER TABLE {table} ALTER COLUMN embedding TYPE halfvec(2560) USING NULL")
        op.execute(_index_ddl(table, index, where, half=True))


def downgrade() -> None:
    for table, index, where in _TABLES:
        op.execute(f"DROP INDEX IF EXISTS {index}")
        op.execute(f"ALTER TABLE {table} ALTER COLUMN embedding TYPE vector(1536) USING NULL")
        op.execute(_index_ddl(table, index, where, half=False))
