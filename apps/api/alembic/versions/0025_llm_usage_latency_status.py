"""0025: LLM 用量可观测(038)

Revision ID: 0025
Revises: 0024
Create Date: 2026-08-25

sys_llm_usage:加 latency_ms/status 两列——失败调用也采集(038 起),
失败率与延迟成为可聚合指标。存量行 DEFAULT 'SUCCESS' 语义成立
(021 起失败调用本就不落记录)。
**downgrade**:删两列(新列无外键引用,瞬时)。
"""

from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None

DDL = """
ALTER TABLE public.sys_llm_usage
    ADD COLUMN latency_ms INTEGER,
    ADD COLUMN status VARCHAR(16) NOT NULL DEFAULT 'SUCCESS'
        CHECK (status IN ('SUCCESS', 'ERROR'));
"""


def _statements(ddl: str) -> list[str]:
    no_comments = "\n".join(line.split("--", 1)[0] for line in ddl.splitlines())
    return [s.strip() for s in no_comments.split(";") if s.strip()]


def upgrade() -> None:
    for stmt in _statements(DDL):
        op.execute(stmt)


def downgrade() -> None:
    op.execute(
        "ALTER TABLE public.sys_llm_usage"
        " DROP COLUMN IF EXISTS latency_ms,"
        " DROP COLUMN IF EXISTS status"
    )
