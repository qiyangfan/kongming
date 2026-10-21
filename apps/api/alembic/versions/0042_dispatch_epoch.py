"""为任务增加派发代数。回调必须携带对应代数，过期执行者的迟到回写会被拒绝。"""

from sqlalchemy import text

from alembic import op

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execute(
        text(
            "ALTER TABLE public.sys_agent_task ADD COLUMN dispatch_epoch INTEGER NOT NULL DEFAULT 0"
        )
    )


def downgrade() -> None:
    op.get_bind().execute(
        text("ALTER TABLE public.sys_agent_task DROP COLUMN IF EXISTS dispatch_epoch")
    )
