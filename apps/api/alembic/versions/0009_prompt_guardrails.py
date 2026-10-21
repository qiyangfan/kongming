"""新增护栏触发审计表和规则覆盖表。原始触发内容不入审计，规则定义仍以代码注册表为准。"""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

DDL = """
-- ============ guardrail 域(public,020) ============
CREATE TABLE public.sys_guardrail_audit (
    id             UUID PRIMARY KEY,
    trace_id       VARCHAR(64) NOT NULL,
    user_id        UUID,
    tenant_id      UUID NOT NULL,
    guardrail_type VARCHAR(16) NOT NULL
                   CHECK (guardrail_type IN ('input', 'output', 'tool_intent')),
    rule_name      VARCHAR(64) NOT NULL,
    action         VARCHAR(16) NOT NULL
                   CHECK (action IN ('blocked', 'masked')),
    content_digest TEXT,                               -- 脱敏摘要(截断 500)
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_guard_audit_tenant_time ON public.sys_guardrail_audit (tenant_id, created_at DESC);
CREATE INDEX idx_guard_audit_type_time ON public.sys_guardrail_audit (guardrail_type, created_at DESC);
CREATE INDEX idx_guard_audit_user_time ON public.sys_guardrail_audit (user_id, created_at DESC);
CREATE INDEX idx_guard_audit_trace ON public.sys_guardrail_audit (trace_id);

CREATE TABLE public.sys_guardrail_rule (
    rule_name   VARCHAR(64) PRIMARY KEY,               -- 与代码注册表对齐
    enabled     BOOLEAN NOT NULL,
    action      VARCHAR(16) CHECK (action IN ('blocked', 'masked')),
    threshold   INTEGER,                               -- 仅 tool_intent 类规则用
    updated_by  UUID,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def _statements(ddl: str) -> list[str]:
    """拆分前剥离 -- 行注释再按分号切(与 0001/0008 同款)。"""
    no_comments = "\n".join(line.split("--", 1)[0] for line in ddl.splitlines())
    return [s.strip() for s in no_comments.split(";") if s.strip()]


def upgrade() -> None:
    for stmt in _statements(DDL):
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.sys_guardrail_rule")
    op.execute("DROP TABLE IF EXISTS public.sys_guardrail_audit")
