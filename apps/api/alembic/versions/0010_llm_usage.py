"""0010: multi-model-routing 用量表(021)

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-23

sys_llm_usage:LLM 逐次调用用量(agent 经 /internal/llm-usage 上报,
fire-and-forget 统计口径非计费依据);tenant_id 由 api 按 tenant_key 反解,
反解失败 NULL(聚合归 unknown);trace_id 可空(titler/describer 无 trace)。
**downgrade**:删表(新域零存量)。
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
