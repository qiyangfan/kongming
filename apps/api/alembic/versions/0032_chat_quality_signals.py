"""069-chat-quality-observability:chat_message 轮次质量信号四列(诊断信号)。

trace_id(三表关联唯一键)/ retry_count(selfheal 次数,现状丢弃)/
resolved_question(多轮改写结果,仅实际发生时非空)/ prompt_version(agent
prompt 资产指纹前 8 位,073 纯函数同源)。四字段为管理面诊断信号,SHALL NOT
进用户响应与 SSE(query-streaming「逐字段同构」契约不破)。存量行 null/0
零回算;created_at 聚合索引支撑质量汇总/趋势查询。

Revision ID: 0032
Revises: 0031
"""

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
