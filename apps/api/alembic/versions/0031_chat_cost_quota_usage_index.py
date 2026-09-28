"""062 问答成本控制:sys_llm_usage 配额计数索引(ADR-004 既有表加索引)。

配额「已用量」按 (tenant_id, created_at) 窗口 count 派生(日/月);既有索引
只有 created_at 与 (model, created_at),租户维度 count 会全表扫——一次建全。
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0031"
down_revision: str | Sequence[str] | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "idx_llm_usage_tenant_time",
        "sys_llm_usage",
        ["tenant_id", "created_at"],
        schema="public",
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index(
        "idx_llm_usage_tenant_time",
        table_name="sys_llm_usage",
        schema="public",
        if_exists=True,
    )
