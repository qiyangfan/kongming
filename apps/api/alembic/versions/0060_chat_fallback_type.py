"""chat_message 增兜底轮分类诊断列 fallback_type(闲聊/澄清,nullable)。

兜底轮(复语直通/语义闲聊=CHITCHAT,歧义澄清早退=CLARIFY)复用既有
NO_DATA 中性形态通道,分类由独立诊断字段承载——四态 status 语义与
映射零变化(spec「兜底轮持久化与呈现」)。nullable=非兜底轮/存量行。
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0060"
down_revision: str | Sequence[str] | None = "0059"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "chat_message"
_CONSTRAINT = "chat_message_fallback_type_check"


def upgrade() -> None:
    op.execute("ALTER TABLE public.chat_message ADD COLUMN fallback_type VARCHAR(16)")
    op.create_check_constraint(_CONSTRAINT, _TABLE, "fallback_type IN ('CHITCHAT', 'CLARIFY')")


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.execute("ALTER TABLE public.chat_message DROP COLUMN fallback_type")
