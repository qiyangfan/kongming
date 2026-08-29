"""044:chat_message.status 增第四态 NO_DATA(知识未覆盖如实说明)。

如实说明型回答(043 生成短路)既非执行失败亦非请求被拒——以 FAILED
持久化会伪装成失败形态(错误横幅/历史误导)。第四态经 drop+re-add
内联 CHECK 演进(列宽 VARCHAR(16) 恰容纳,存量行零触碰)。

Revision ID: 0028
Revises: 0027
Create Date: 2026-08-29
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "chat_message_status_check"
_TABLE = "chat_message"
_THREE = "status IN ('SUCCESS', 'FAILED', 'REJECTED')"
_FOUR = "status IN ('SUCCESS', 'FAILED', 'REJECTED', 'NO_DATA')"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_CONSTRAINT, _TABLE, _FOUR)


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_CONSTRAINT, _TABLE, _THREE)
