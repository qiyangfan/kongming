"""扩展知识来源类型并为租户、数据源过滤新增索引。若存在旧 MARKDOWN 来源行，升级前需将其迁移到对应新类型。"""

from sqlalchemy import text

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

DDL = """
ALTER TABLE rag.table_descriptions DROP CONSTRAINT
    table_descriptions_source_type_check;
ALTER TABLE rag.table_descriptions ADD CONSTRAINT table_descriptions_source_type_check
    CHECK (source_type IN ('INTROSPECTION', 'AI_GENERATED', 'MANUAL_UPLOAD',
                           'MANUAL_EDIT', 'FEEDBACK_REFINED'));
ALTER TABLE rag.table_descriptions ALTER COLUMN source_type
    SET DEFAULT 'INTROSPECTION';
CREATE INDEX idx_td_tenant_ds ON rag.table_descriptions (tenant_id, datasource_key);
CREATE INDEX idx_qh_tenant_ds_status ON rag.query_history
    (tenant_id, datasource_key, status);
"""

DOWNGRADE = """
DROP INDEX rag.idx_qh_tenant_ds_status;
DROP INDEX rag.idx_td_tenant_ds;
ALTER TABLE rag.table_descriptions ALTER COLUMN source_type SET DEFAULT 'MARKDOWN';
ALTER TABLE rag.table_descriptions DROP CONSTRAINT
    table_descriptions_source_type_check;
ALTER TABLE rag.table_descriptions ADD CONSTRAINT table_descriptions_source_type_check
    CHECK (source_type IN ('MARKDOWN', 'AI_GENERATED', 'MANUAL_EDIT',
                           'FEEDBACK_REFINED'));
"""


def _statements(ddl: str) -> list[str]:
    """拆分前剥离 -- 行注释再按分号切(同 baseline 约定)。"""
    no_comments = "\n".join(line.split("--", 1)[0] for line in ddl.splitlines())
    return [s.strip() for s in no_comments.split(";") if s.strip()]


def upgrade() -> None:
    for stmt in _statements(DDL):
        op.get_bind().execute(text(stmt))


def downgrade() -> None:
    for stmt in _statements(DOWNGRADE):
        op.get_bind().execute(text(stmt))
