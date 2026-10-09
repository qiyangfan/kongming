"""0039: 持久任务内核两表(090 task-kernel)

- sys_agent_task:任务行(归属/状态机/乐观锁版本/预算配置与消耗/事件时间线);
- sys_agent_task_step:步骤账本(证据驱动状态;检查点内嵌步骤行,不单独建表)。

约定同 baseline:UUID v7 主键、VARCHAR+CHECK、timestamptz、预算/检查点/事件
JSONB;事件取「任务行内 jsonb 数组」的简形(审查取简口径, CAS 版本行锁防并发)。
"""

from sqlalchemy import text

from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None

DDL = """
CREATE TABLE public.sys_agent_task (
    id             UUID PRIMARY KEY,
    tenant_id      UUID NOT NULL,
    user_id        UUID NOT NULL,
    session_id     UUID,
    goal           TEXT NOT NULL,
    status         VARCHAR(20) NOT NULL DEFAULT 'PLANNING'
                   CHECK (status IN ('PLANNING','RUNNING','AWAITING_APPROVAL','PAUSED',
                                     'INTERRUPTED','DONE','FAILED','CANCELLED')),
    version        INTEGER NOT NULL DEFAULT 0,
    budget_config  JSONB NOT NULL DEFAULT '{}',
    budget_used    JSONB NOT NULL DEFAULT '{}',
    datasource_key VARCHAR(64) NOT NULL DEFAULT 'default',
    trace_root     VARCHAR(64),
    events         JSONB NOT NULL DEFAULT '[]',
    fail_reason    TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_agent_task_owner ON public.sys_agent_task (tenant_id, user_id, created_at DESC);

CREATE TABLE public.sys_agent_task_step (
    id              UUID PRIMARY KEY,
    task_id         UUID NOT NULL REFERENCES public.sys_agent_task(id),
    ord             INTEGER NOT NULL,
    goal            TEXT NOT NULL,
    status          VARCHAR(20) NOT NULL DEFAULT 'PENDING'
                    CHECK (status IN ('PENDING','RUNNING','AWAITING_APPROVAL',
                                      'DONE','FAILED','SKIPPED','CANCELLED')),
    require_confirm BOOLEAN NOT NULL DEFAULT false,
    evidence        JSONB,
    fail_reason     TEXT,
    checkpoint      JSONB,
    started_at      TIMESTAMPTZ,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_agent_task_step_task ON public.sys_agent_task_step (task_id, ord);
"""


def _statements(ddl: str) -> list[str]:
    no_comments = "\n".join(line.split("--", 1)[0] for line in ddl.splitlines())
    return [s.strip() for s in no_comments.split(";") if s.strip()]


def upgrade() -> None:
    for stmt in _statements(DDL):
        op.get_bind().execute(text(stmt))


def downgrade() -> None:
    for stmt in _statements(
        """
        DROP TABLE IF EXISTS public.sys_agent_task_step;
        DROP TABLE IF EXISTS public.sys_agent_task;
        """
    ):
        op.get_bind().execute(text(stmt))
