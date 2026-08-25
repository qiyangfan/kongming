"""0015: cloud-code-sandbox 执行审计表(027)

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-23

sys_sandbox_run:沙箱执行审计(sys_tool_audit 的补充维度,经 trace_id 可
JOIN);code_text 全文留存(≤8000,与 sys_sql_audit 留 sql_text 同哲学=
排障需要);status VARCHAR+CHECK 四终态(ADR-004:禁魔数/禁 PG ENUM)。
**downgrade**:删表(新域零存量)。
"""

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

DDL = """
-- ============ sandbox 域(public,027) ============
CREATE TABLE public.sys_sandbox_run (
    id             UUID PRIMARY KEY,
    tenant_id      UUID NOT NULL,
    user_id        UUID NOT NULL,
    trace_id       VARCHAR(64) NOT NULL,
    code_text      TEXT NOT NULL,                  -- 全文(传输层 ≤8000)
    data_rows      INTEGER NOT NULL DEFAULT 0,     -- 注入 df 行列(0=纯计算)
    data_cols      INTEGER NOT NULL DEFAULT 0,
    stdout_tail    VARCHAR(2000) NOT NULL DEFAULT '',
    result_tail    VARCHAR(2000) NOT NULL DEFAULT '',
    images_count   INTEGER NOT NULL DEFAULT 0,
    duration_ms    INTEGER NOT NULL DEFAULT 0,
    exit_code      INTEGER,                        -- 被杀/协议缺失可空
    status         VARCHAR(16) NOT NULL,
    error_tail     VARCHAR(2000) NOT NULL DEFAULT '',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_sys_sandbox_run_status CHECK
        (status IN ('SUCCESS','ERROR','TIMEOUT','RESOURCE_LIMIT'))
);
CREATE INDEX ix_sys_sandbox_run_tenant_time
    ON public.sys_sandbox_run (tenant_id, created_at DESC);
CREATE INDEX ix_sys_sandbox_run_trace ON public.sys_sandbox_run (trace_id);
"""


def _statements(ddl: str) -> list[str]:
    """asyncpg 预备语句不许多命令(0014 同款):剥注释按分号切。"""
    no_comments = "\n".join(line.split("--", 1)[0] for line in ddl.splitlines())
    return [s.strip() for s in no_comments.split(";") if s.strip()]


def upgrade() -> None:
    for stmt in _statements(DDL):
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.sys_sandbox_run")
