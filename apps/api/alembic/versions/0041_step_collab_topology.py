"""为任务步骤增加协作拓扑声明，并在写入前按部署配置限制 worker 数量。"""

from sqlalchemy import text

from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execute(
        text(
            "ALTER TABLE public.sys_agent_task_step"
            " ADD COLUMN collab_topology JSONB NOT NULL DEFAULT '{}'"
        )
    )
    # worker 行唯一键(防重派发/并发双写重复账;0040 仅普通索引)
    op.get_bind().execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_collab_worker_run_no"
            " ON public.sys_agent_collab_worker (run_id, worker_no)"
        )
    )


def downgrade() -> None:
    # asyncpg 单 prepared statement 不允许多命令:逐条执行
    op.get_bind().execute(text("DROP INDEX IF EXISTS public.uq_collab_worker_run_no"))
    op.get_bind().execute(
        text("ALTER TABLE public.sys_agent_task_step DROP COLUMN IF EXISTS collab_topology")
    )
