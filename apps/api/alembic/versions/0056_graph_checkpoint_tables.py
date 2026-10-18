"""0056: 图级检查点与图内挂起(add-graph-checkpoint)

- sys_agent_graph_checkpoint:执行图推进快照(agent 经内部通道读写的不透明
  blob;控制面只存不取,不理解图框架序列化格式——控制面与图框架解耦);
  kind 区分快照行与中断挂起期的部分写入行。
- sys_agent_graph_confirm:图内人工确认请求(任务循环内高危动作挂图审批);
  状态机 pending→approved/denied/expired 单向 CAS,与任务审批门同源纪律
  (归属人-only/过期/幂等)。
"""

from sqlalchemy import text

from alembic import op

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execute(
        text(
            """
            CREATE TABLE public.sys_agent_graph_checkpoint (
                id uuid PRIMARY KEY,
                tenant_id uuid NOT NULL REFERENCES public.sys_tenant(id),
                thread_id text NOT NULL,
                checkpoint_ns text NOT NULL DEFAULT '',
                kind text NOT NULL CHECK (kind IN ('checkpoint', 'write')),
                checkpoint_id text,
                source_task_id text,
                source_step_id text,
                payload_type text NOT NULL DEFAULT '',
                payload bytea NOT NULL,
                created_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
    )
    op.get_bind().execute(
        text(
            "CREATE INDEX idx_agent_graph_ckpt_thread"
            " ON public.sys_agent_graph_checkpoint"
            " (tenant_id, thread_id, checkpoint_ns, created_at DESC)"
        )
    )
    op.get_bind().execute(
        text(
            "CREATE INDEX idx_agent_graph_ckpt_latest"
            " ON public.sys_agent_graph_checkpoint"
            " (tenant_id, thread_id, checkpoint_ns, created_at DESC)"
            " WHERE kind = 'checkpoint'"
        )
    )
    op.get_bind().execute(
        text(
            """
            CREATE TABLE public.sys_agent_graph_confirm (
                id uuid PRIMARY KEY,
                tenant_id uuid NOT NULL REFERENCES public.sys_tenant(id),
                task_id uuid NOT NULL REFERENCES public.sys_agent_task(id),
                step_id uuid NOT NULL,
                confirm_id text NOT NULL,
                tool_name text NOT NULL DEFAULT '',
                summary text NOT NULL DEFAULT '',
                thread_id text NOT NULL DEFAULT '',
                status text NOT NULL CHECK (status IN ('pending', 'approved', 'denied', 'expired')),
                decided_by uuid,
                decided_at timestamptz,
                expires_at timestamptz NOT NULL,
                created_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
    )
    op.get_bind().execute(
        text(
            "CREATE UNIQUE INDEX uq_agent_graph_confirm"
            " ON public.sys_agent_graph_confirm (tenant_id, confirm_id)"
        )
    )
    op.get_bind().execute(
        text(
            "CREATE INDEX idx_agent_graph_confirm_task"
            " ON public.sys_agent_graph_confirm (task_id, step_id, status)"
        )
    )


def downgrade() -> None:
    op.get_bind().execute(text("DROP TABLE IF EXISTS public.sys_agent_graph_confirm"))
    op.get_bind().execute(text("DROP TABLE IF EXISTS public.sys_agent_graph_checkpoint"))
