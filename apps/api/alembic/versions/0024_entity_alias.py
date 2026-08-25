"""0024: 实体别名(037)

Revision ID: 0024
Revises: 0023
Create Date: 2026-08-24

rag.table_descriptions: 加 alias 列(VARCHAR(64) NULL)——
业务称谓短名(如「商品表」「影片分类表」)。
来源优先级:MANUAL_EDIT 手编>AI 提炼(009 润色管线同一次 LLM 调用附带)>
兜底(NULL 时消费层回退 table_comment/物理名)。
零存量改写;downgrade 删列。
"""

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None

DML = """
ALTER TABLE rag.table_descriptions
    ADD COLUMN alias VARCHAR(64);
"""

DOWN_DML = """
ALTER TABLE rag.table_descriptions
    DROP COLUMN IF EXISTS alias;
"""


def upgrade() -> None:
    for stmt in [s.strip() for s in DML.split(";") if s.strip()]:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in [s.strip() for s in DOWN_DML.split(";") if s.strip()]:
        op.execute(stmt)
