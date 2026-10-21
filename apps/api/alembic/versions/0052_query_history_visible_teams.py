"""为查询历史增加团队可见范围，召回时过滤调用者不可见的样本。存量样本默认为公共；降级会移除可见性限制。"""

from alembic import op

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE rag.query_history ADD COLUMN visible_teams text[] NOT NULL DEFAULT '{}'"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE rag.query_history DROP COLUMN IF EXISTS visible_teams")
