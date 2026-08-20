"""0006: rag 知识域增量演进(009 RAG 三路召回+知识库)

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-20

rag 两表为 baseline 既有资产(0001 整合上游最终态一次建全),本迁移零建表:
- source_type CHECK 枚举替换:MARKDOWN → INTROSPECTION/AI_GENERATED/MANUAL_UPLOAD
  (对齐 knowledge spec 五级覆盖优先级;上游 introspection/introspection_llm/
  markdown/manual 四态的本仓映射),DEFAULT 同步改 INTROSPECTION;
- 召回过滤辅助索引:idx_td_tenant_ds / idx_qh_tenant_ds_status(baseline 未建,
  三路召回双过滤 WHERE 的支撑索引)。
手写迁移(autogenerate 不识别 vector/hnsw)。
**upgrade 前置条件**:若某环境 rag.table_descriptions 已有 source_type=
'MARKDOWN' 行,须先 UPDATE 为 'MANUAL_UPLOAD'(语义等价:人工上传)再替换
约束——本仓该表在 009 前无任何写入方,空表瞬时,先例 0005。
**downgrade**:仅还原自身增量(枚举/DEFAULT/两索引),不触碰 schema/表
(baseline 资产不可删);已有新枚举值行时须先清理(注记,同 upgrade 口径)。
"""

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
