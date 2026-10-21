"""0040: 多智能体协作两表

- sys_agent_collab_run:协作运行(宿主=任务步骤;拓扑 jsonb;状态机;裁决摘要);
- sys_agent_collab_worker:worker 账本(子目标/绑定/状态/结论/证据/用量摘要)。

审计复用既有工具审计(逐 worker 工具调用)+ 任务事件流,不建新审计表。
"""

from sqlalchemy import text

from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None

DDL = """
CREATE TABLE public.sys_agent_collab_run (
    id             UUID PRIMARY KEY,
    task_id        UUID NOT NULL REFERENCES public.sys_agent_task(id),
    step_id        UUID NOT NULL REFERENCES public.sys_agent_task_step(id),
    tenant_id      UUID NOT NULL,
    topology       JSONB NOT NULL DEFAULT '{}',
    status         VARCHAR(16) NOT NULL DEFAULT 'RUNNING'
                   CHECK (status IN ('RUNNING','DONE','FAILED','PARTIAL','CANCELLED')),
    verdict        TEXT,
    depth          INTEGER NOT NULL DEFAULT 1,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_collab_run_step ON public.sys_agent_collab_run (step_id);
CREATE INDEX idx_collab_run_task ON public.sys_agent_collab_run (task_id);

CREATE TABLE public.sys_agent_collab_worker (
    id          UUID PRIMARY KEY,
    run_id      UUID NOT NULL REFERENCES public.sys_agent_collab_run(id),
    worker_no   INTEGER NOT NULL,
    subgoal     TEXT NOT NULL,
    bot_key     VARCHAR(64),
    status      VARCHAR(16) NOT NULL DEFAULT 'PENDING'
                CHECK (status IN ('PENDING','RUNNING','DONE','FAILED','SKIPPED')),
    conclusion  JSONB,
    evidence    JSONB,
    usage       JSONB,
    trace_id    VARCHAR(64),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_collab_worker_run ON public.sys_agent_collab_worker (run_id, worker_no);
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
        DROP TABLE IF EXISTS public.sys_agent_collab_worker;
        DROP TABLE IF EXISTS public.sys_agent_collab_run;
        """
    ):
        op.get_bind().execute(text(stmt))
