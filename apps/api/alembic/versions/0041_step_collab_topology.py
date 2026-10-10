"""0041: 步骤协作拓扑列(091 接线)

- sys_agent_task_step.collab_topology:步骤声明协作拓扑(空对象=普通单 ReAct 步)。
  声明后该步执行即协作(编排循环按 step-start 决策携带的拓扑装配协作编排器),
  步骤终态由聚合结论驱动。归一/钳制在 api 侧写入前完成,库内只存合法载荷。

拓扑参数(091 spec「协作拓扑」):worker 数量上限 / 是否启用 critic / 分配策略;
bot_key 可空(worker 人格绑定)。部署级上限(配置 collab_max_workers)在
normalize_collab_topology 钳制,库内值恒在部署上限内。
"""

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
