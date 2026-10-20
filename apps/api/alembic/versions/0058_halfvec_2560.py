"""0058: 向量列升级 halfvec(2560)(upgrade-rag-qwen3-halfvec-2560)

四类派生向量(表说明/历史问句/文档子块/用户记忆)统一切半精度 2560 维:
- 列类型 vector(1536) → halfvec(2560),HNSW 索引换 halfvec_cosine_ops;
- 旧向量维度不匹配且可低成本重建,升级即清空(USING NULL),原文与业务
  字段全部保留;降级恢复 vector(1536) 列与索引,同样不恢复旧向量
  (设计 D3:两方向都不转换派生数据,恢复召回应重跑重建命令)。
- halfvec HNSW 上限 4000 维(vector 上限 2000),2560 维只有 halfvec 可索引。
"""

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
