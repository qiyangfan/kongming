"""0054: 圆桌轮次转写表(115)

- sys_agent_collab_turn:黑板转写账(圆桌拓扑运行的轮次行;追加只写,无 UPDATE
  路径)——发言/查证/弃权按 (run, round, worker) 唯一,主席判定占 worker_no=0。

账本复用 091 协作账(collab_run/worker);审计复用既有工具审计(逐参与者工具
调用)+ 任务事件流(开桌/每轮完成/判定/收口),不建新审计表。
"""

from sqlalchemy import text

from alembic import op

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None

DDL = """
CREATE TABLE public.sys_agent_collab_turn (
    id         UUID PRIMARY KEY,
    run_id     UUID NOT NULL REFERENCES public.sys_agent_collab_run(id),
    round_no   INTEGER NOT NULL,
    worker_no  INTEGER NOT NULL,
    kind       VARCHAR(16) NOT NULL
               CHECK (kind IN ('speech','verification','abstain','verdict')),
    content    JSONB,
    evidence   JSONB,
    usage      JSONB,
    trace_id   VARCHAR(64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_collab_turn_run_round_no
    ON public.sys_agent_collab_turn (run_id, round_no, worker_no);
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
        DROP INDEX IF EXISTS public.uq_collab_turn_run_round_no;
        DROP TABLE IF EXISTS public.sys_agent_collab_turn;
        """
    ):
        op.get_bind().execute(text(stmt))
