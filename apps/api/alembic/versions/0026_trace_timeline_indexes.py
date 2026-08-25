"""0026: trace 时间线索引(038 R1)

Revision ID: 0026
Revises: 0025
Create Date: 2026-08-25

timeline 端点按 trace_id 查三表——sys_llm_usage/sys_sql_audit 此前无
trace_id 索引、sys_tool_audit 仅有 PENDING_CONFIRM 部分索引,每次查询
全表扫描,随审计数据线性增长确定性退化。补 (trace_id, created_at)
复合索引(等值+时间序排序双覆盖)。空表/低量表瞬时。
**downgrade**:删三索引。
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
