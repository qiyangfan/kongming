"""为 LLM 用量增加 protocol 字段。存量记录保持 NULL，因为模型名称无法可靠推断出站协议。"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0033"
down_revision: str | Sequence[str] | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sys_llm_usage", sa.Column("protocol", sa.String(16), nullable=True))


def downgrade() -> None:
    op.drop_column("sys_llm_usage", "protocol")
