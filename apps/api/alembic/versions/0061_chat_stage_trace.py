"""chat_message 增节点阶段轨迹诊断列 stage_trace(JSONB,nullable)。

流式问答轮终态落库时随行持久化节点 running/done 有序数组(收集自既有
SSE stage 事件,采集面零新增);同步轮如实为空(spec「节点阶段轨迹
持久化」)。纯增量观测列,查询热路径不 SELECT。"""

from collections.abc import Sequence

from alembic import op

revision: str = "0061"
down_revision: str | Sequence[str] | None = "0060"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE public.chat_message ADD COLUMN stage_trace JSONB")


def downgrade() -> None:
    op.execute("ALTER TABLE public.chat_message DROP COLUMN stage_trace")
