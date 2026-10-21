"""新增表关系和指标定义表。内省更新不得覆盖人工关系；指标 SQL 模板仅供提示词使用，不会执行。"""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

DDL = """
-- ============ semantic 域(rag schema,023) ============
CREATE TABLE rag.table_relations (
    id             UUID PRIMARY KEY,
    tenant_id      UUID NOT NULL,
    datasource_key VARCHAR(64) NOT NULL,
    source_table   VARCHAR(128) NOT NULL,              -- schema.table 全名
    target_table   VARCHAR(128) NOT NULL,
    join_keys      JSONB NOT NULL,                     -- [{left_col, right_col}]
    source         VARCHAR(16) NOT NULL DEFAULT 'introspection'
                   CHECK (source IN ('introspection', 'manual')),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_table_relations UNIQUE (tenant_id, datasource_key, source_table, target_table)
);
CREATE INDEX idx_table_relations_ds ON rag.table_relations (tenant_id, datasource_key);

CREATE TABLE rag.metric_definitions (
    id             UUID PRIMARY KEY,
    tenant_id      UUID NOT NULL,
    datasource_key VARCHAR(64) NOT NULL,
    metric_name    VARCHAR(128) NOT NULL,
    sql_template   TEXT NOT NULL,                      -- 仅 prompt 注入,永不执行
    business_terms TEXT[] NOT NULL DEFAULT '{}',
    enabled        BOOLEAN NOT NULL DEFAULT true,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_metric_definitions UNIQUE (tenant_id, datasource_key, metric_name)
);
CREATE INDEX idx_metric_definitions_ds ON rag.metric_definitions (tenant_id, datasource_key);
"""


def _statements(ddl: str) -> list[str]:
    no_comments = "\n".join(line.split("--", 1)[0] for line in ddl.splitlines())
    return [s.strip() for s in no_comments.split(";") if s.strip()]


def upgrade() -> None:
    for stmt in _statements(DDL):
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS rag.metric_definitions")
    op.execute("DROP TABLE IF EXISTS rag.table_relations")
