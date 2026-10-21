"""为 assistant 问答轮次增加 trace、重试次数、解析后问题和提示词版本等诊断字段；这些字段不进入用户响应。"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0032"
down_revision: str | Sequence[str] | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("chat_message", sa.Column("trace_id", sa.String(64), nullable=True))
    op.add_column(
        "chat_message",
        sa.Column("retry_count", sa.SmallInteger(), nullable=False, server_default="0"),
    )
    op.add_column("chat_message", sa.Column("resolved_question", sa.Text(), nullable=True))
    op.add_column("chat_message", sa.Column("prompt_version", sa.String(8), nullable=True))
    op.create_index(
        "idx_chat_message_created",
        "chat_message",
        [sa.column("created_at")],
        schema="public",
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index(
        "idx_chat_message_created", table_name="chat_message", schema="public", if_exists=True
    )
    op.drop_column("chat_message", "prompt_version")
    op.drop_column("chat_message", "resolved_question")
    op.drop_column("chat_message", "retry_count")
    op.drop_column("chat_message", "trace_id")
