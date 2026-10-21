"""新增租户 MCP server 配置表。请求头凭证使用 AES-GCM 加密，工具发现结果按 server_key 保存。"""

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

DDL = """
-- ============ mcp 域(public,028) ============
CREATE TABLE public.sys_mcp_server (
    id             UUID PRIMARY KEY,
    tenant_id      UUID NOT NULL,
    server_key     VARCHAR(64) NOT NULL,
    name           VARCHAR(128) NOT NULL,
    endpoint       VARCHAR(512) NOT NULL,
    headers_cipher TEXT NOT NULL DEFAULT '',        -- AES-GCM 密文;空=无 headers
    enabled        BOOLEAN NOT NULL DEFAULT TRUE,
    tools          JSONB NOT NULL DEFAULT '[]',     -- 发现快照
    last_sync_at   TIMESTAMPTZ,
    last_sync_error VARCHAR(500) NOT NULL DEFAULT '',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX ux_sys_mcp_server_tenant_key
    ON public.sys_mcp_server (tenant_id, server_key);
"""


def _statements(ddl: str) -> list[str]:
    """asyncpg 预备语句不许多命令(0014 同款):剥注释按分号切。"""
    no_comments = "\n".join(line.split("--", 1)[0] for line in ddl.splitlines())
    return [s.strip() for s in no_comments.split(";") if s.strip()]


def upgrade() -> None:
    for stmt in _statements(DDL):
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.sys_mcp_server")
