"""087 user_prefs 增内部人工版本(不改既有 JSON 列与历史迁移)。

Revision ID: 0036_user_prefs_manual_revision
Revises: 0035_sys_role_dept
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0036"
down_revision: str | Sequence[str] | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 仅内部使用(人工修改版本):不进公共契约/响应;存量行回填 0
    op.add_column(
        "user_prefs",
        sa.Column("manual_revision", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
    )


def downgrade() -> None:
    op.drop_column("user_prefs", "manual_revision")
