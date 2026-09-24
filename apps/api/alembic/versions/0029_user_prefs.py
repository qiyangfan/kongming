"""059 user_prefs 一次建全(ADR-004)。

Revision ID: 0029_user_prefs
Revises: 0028_chat_no_data_status
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0029"
down_revision: str | Sequence[str] | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_prefs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("prefs", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("user_id", "tenant_id", name="uq_user_prefs_user_tenant"),
    )
    # user_memory category 值域补 NOTE/LEGACY(059;CHECK 与既有对齐扩展)
    # asyncpg 不允许多语句一次 prepare,拆两条
    op.execute(
        "ALTER TABLE public.user_memory DROP CONSTRAINT IF EXISTS user_memory_category_check"
    )
    op.execute(
        "ALTER TABLE public.user_memory ADD CONSTRAINT user_memory_category_check"
        " CHECK (lower(category) IN ('general','preference','fact','goal','note','legacy'))"
    )


def downgrade() -> None:
    op.drop_table("user_prefs")
    op.execute(
        "ALTER TABLE public.user_memory DROP CONSTRAINT IF EXISTS user_memory_category_check"
    )
    op.execute(
        "ALTER TABLE public.user_memory ADD CONSTRAINT user_memory_category_check"
        " CHECK (lower(category) IN ('general','preference','fact','goal'))"
    )
