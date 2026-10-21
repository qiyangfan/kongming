"""LLM 调用用量表。

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-23

Agent 通过 /internal/llm-usage 上报逐次用量，仅供统计，不作为计费依据。
tenant_id 按 tenant_key 解析，失败时为空；trace_id 允许为空。
**downgrade**:删除用量表。
"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

DDL = """
-- ============ llmusage 域(public,021) ============
CREATE TABLE public.sys_llm_usage (
    id               UUID PRIMARY KEY,
    trace_id         VARCHAR(64),
    tenant_id        UUID,                             -- tenant_key 反解;失败 NULL
    model            VARCHAR(64) NOT NULL,
    task             VARCHAR(24) NOT NULL,             -- SQL_GEN/UNDERSTAND/.../EMBED
    prompt_tokens    INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens     INTEGER NOT NULL DEFAULT 0,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_llm_usage_time ON public.sys_llm_usage (created_at DESC);
CREATE INDEX idx_llm_usage_model_time ON public.sys_llm_usage (model, created_at DESC);
"""


def _statements(ddl: str) -> list[str]:
    no_comments = "\n".join(line.split("--", 1)[0] for line in ddl.splitlines())
    return [s.strip() for s in no_comments.split(";") if s.strip()]


def upgrade() -> None:
    for stmt in _statements(DDL):
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.sys_llm_usage")
