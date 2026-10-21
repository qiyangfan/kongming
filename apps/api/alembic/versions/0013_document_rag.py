"""新增文档与父子分块表。向量只存于子块，文档删除时级联清理分块。"""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

DDL = """
-- ============ document-rag 域(rag schema,024) ============
CREATE TABLE rag.documents (
    id             UUID PRIMARY KEY,
    tenant_id      UUID NOT NULL,
    datasource_key VARCHAR(64) NOT NULL,
    doc_name       VARCHAR(200) NOT NULL,
    content_hash   VARCHAR(64) NOT NULL,               -- SHA-256(幂等)
    block_count    INTEGER NOT NULL DEFAULT 0,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_documents_tdq UNIQUE (tenant_id, datasource_key, doc_name)
);
CREATE INDEX idx_documents_ds ON rag.documents (tenant_id, datasource_key);

CREATE TABLE rag.document_chunks (
    id             UUID PRIMARY KEY,
    document_id    UUID NOT NULL REFERENCES rag.documents(id) ON DELETE CASCADE,
    tenant_id      UUID NOT NULL,
    datasource_key VARCHAR(64) NOT NULL,
    chunk_level    VARCHAR(8) NOT NULL
                   CHECK (chunk_level IN ('PARENT', 'CHILD')),
    parent_id      UUID,                               -- CHILD 指父块行 id
    seq            INTEGER NOT NULL,
    source_ref     VARCHAR(200),
    content        TEXT NOT NULL,
    tsv            tsvector,
    embedding      vector(1536),                       -- 仅 CHILD
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_document_chunks_doc ON rag.document_chunks (document_id, seq);
CREATE INDEX idx_document_chunks_embedding ON rag.document_chunks
    USING hnsw (embedding vector_cosine_ops) WHERE chunk_level = 'CHILD';
CREATE INDEX idx_document_chunks_tsv ON rag.document_chunks USING gin (tsv);
"""


def _statements(ddl: str) -> list[str]:
    no_comments = "\n".join(line.split("--", 1)[0] for line in ddl.splitlines())
    return [s.strip() for s in no_comments.split(";") if s.strip()]


def upgrade() -> None:
    for stmt in _statements(DDL):
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS rag.document_chunks")
    op.execute("DROP TABLE IF EXISTS rag.documents")
