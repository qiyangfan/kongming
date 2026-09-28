"""077-llm-usage-protocol-attribution:sys_llm_usage 增 protocol 列(多协议观测闭环)。

多协议出站已落地并归档(multi-protocol-llm,ADR-008:openai/anthropic/
responses),但观测面仍停留单协议时代口径——protocol 取值域只在 agent 进程
内存,不落库不可筛不可聚合。本迁移补最后一环:protocol VARCHAR(16) NULL,
取值域对齐注册表单源(ADR-008 不新造枚举);存量行 null 不回算(model 名
不可可靠映射协议,DashScope 代理同名模型是常态)。

Revision ID: 0033
Revises: 0032
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0033"
down_revision: str | Sequence[str] | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sys_llm_usage", sa.Column("protocol", sa.String(16), nullable=True))


def downgrade() -> None:
    op.drop_column("sys_llm_usage", "protocol")
