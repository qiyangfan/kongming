"""为知识条目增加可选团队归属；删除团队时条目转为租户公共内容。降级只移除归属字段，不改条目内容。"""

from alembic import op

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE rag.table_descriptions"
        " ADD COLUMN team_id UUID REFERENCES public.sys_team(id) ON DELETE SET NULL"
    )
    op.execute("CREATE INDEX idx_table_desc_team ON rag.table_descriptions (team_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS rag.idx_table_desc_team")
    op.execute("ALTER TABLE rag.table_descriptions DROP COLUMN IF EXISTS team_id")
