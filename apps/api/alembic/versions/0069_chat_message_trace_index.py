"""chat_message 增加 trace_id 索引:取消链路按本轮标识取该轮消息(取消收敛的查重、
已结束轮次的取消回答)。无索引时执行计划驱动自会话位图扫描,trace_id 只作过滤,
取消处理的延迟随该用户的历史消息量增长。索引只服务等值查询,不参与排序。
降级:删除该索引。

Revision ID: 0069
Revises: 0068
Create Date: 2026-11-24
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0069"
down_revision: str | Sequence[str] | None = "0068"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "idx_chat_message_trace",
        "chat_message",
        [sa.column("trace_id")],
        schema="public",
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index(
        "idx_chat_message_trace", table_name="chat_message", schema="public", if_exists=True
    )
