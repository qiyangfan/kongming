"""chat_message.status 增第五态 CANCELLED(用户显式取消的流式问答轮)。

被用户主动放弃的一轮既非执行失败亦非无数据——复用 FAILED 会污染失败率。
沿用第四态的 drop+re-add 内联 CHECK 演进(列宽 VARCHAR(16) 恰容纳,存量行零触碰)。
降级前须先处理 CANCELLED 行(删除或人工改写),否则四态 CHECK 会拒绝降级。

Revision ID: 0068
Revises: 0067
Create Date: 2026-11-24
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0068"
down_revision: str | None = "0067"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "chat_message_status_check"
_TABLE = "chat_message"
_FOUR = "status IN ('SUCCESS', 'FAILED', 'REJECTED', 'NO_DATA')"
_FIVE = "status IN ('SUCCESS', 'FAILED', 'REJECTED', 'NO_DATA', 'CANCELLED')"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_CONSTRAINT, _TABLE, _FIVE)


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_CONSTRAINT, _TABLE, _FOUR)
