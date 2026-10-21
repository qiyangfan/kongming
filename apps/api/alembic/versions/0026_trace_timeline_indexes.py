"""为 trace 时间线查询添加索引。

Revision ID: 0026
Revises: 0025
Create Date: 2026-08-25

trace 时间线查询按 trace_id 筛选并按 created_at 排序，因此为三张审计表添加
(trace_id, created_at) 复合索引。
**downgrade**:删除三项索引。
"""

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None

DDL = """
CREATE INDEX idx_llm_usage_trace_time
    ON public.sys_llm_usage (trace_id, created_at);
CREATE INDEX idx_sql_audit_trace_time
    ON public.sys_sql_audit (trace_id, created_at);
CREATE INDEX idx_tool_audit_trace_time
    ON public.sys_tool_audit (trace_id, created_at);
"""


def _statements(ddl: str) -> list[str]:
    no_comments = "\n".join(line.split("--", 1)[0] for line in ddl.splitlines())
    return [s.strip() for s in no_comments.split(";") if s.strip()]


def upgrade() -> None:
    for stmt in _statements(DDL):
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS public.idx_llm_usage_trace_time")
    op.execute("DROP INDEX IF EXISTS public.idx_sql_audit_trace_time")
    op.execute("DROP INDEX IF EXISTS public.idx_tool_audit_trace_time")
