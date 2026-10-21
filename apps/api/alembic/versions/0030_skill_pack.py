"""skill_pack + skill_pack_binding 一次建全(ADR-004)。

Revision ID: 0030
Revises: 0029
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0030"
down_revision: str | Sequence[str] | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "skill_pack",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("pack_key", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("description", sa.String(500), server_default=""),
        sa.Column("content", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(16), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.CheckConstraint("status IN ('ACTIVE','RETIRED')", name="skill_pack_status_check"),
        sa.UniqueConstraint("tenant_id", "pack_key", "version", name="uq_skill_pack_key_ver"),
    )
    op.create_table(
        "skill_pack_binding",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("pack_id", sa.String(36), nullable=False),
        sa.Column("datasource_key", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("pack_id", "datasource_key", name="uq_skill_pack_binding"),
    )


def downgrade() -> None:
    op.drop_table("skill_pack_binding")
    op.drop_table("skill_pack")
