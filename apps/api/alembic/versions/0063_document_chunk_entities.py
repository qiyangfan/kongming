"""新增文档实体伴表(事件-实体检索扩展):实体×子块词法 join 索引。

SAG 对照借鉴的单库化形态:实体=轻量词法索引(无实体向量),查询时以
规范化名 join 子块动态实例化关联,不预建全局图;文档/分块删除级联清理。
"""

from alembic import op

revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None

DDL = """
CREATE TABLE rag.document_chunk_entities (
    id              UUID PRIMARY KEY,
    document_id     UUID NOT NULL REFERENCES rag.documents(id) ON DELETE CASCADE,
    chunk_id        UUID NOT NULL REFERENCES rag.document_chunks(id) ON DELETE CASCADE,
    tenant_id       UUID NOT NULL,
    datasource_key  VARCHAR(64) NOT NULL,
    name            VARCHAR(200) NOT NULL,
    name_normalized VARCHAR(200) NOT NULL,
    name_tsv        tsvector NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_doc_entities_chunk_name UNIQUE (chunk_id, name_normalized)
);
CREATE INDEX idx_doc_entities_join ON rag.document_chunk_entities
    (tenant_id, datasource_key, name_normalized);
CREATE INDEX idx_doc_entities_name_tsv ON rag.document_chunk_entities USING gin (name_tsv);
"""


def _statements(ddl: str) -> list[str]:
    no_comments = "\n".join(line.split("--", 1)[0] for line in ddl.splitlines())
    return [s.strip() for s in no_comments.split(";") if s.strip()]


def upgrade() -> None:
    for stmt in _statements(DDL):
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS rag.document_chunk_entities")
