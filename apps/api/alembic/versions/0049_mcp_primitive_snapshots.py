"""为 MCP server 保存 resources 与 prompts 的发现快照。resource URI 作为读取白名单；prompt 模板预览仍实时获取。"""

from alembic import op

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE public.sys_mcp_server ADD COLUMN resources JSONB NOT NULL DEFAULT '[]'")
    op.execute("ALTER TABLE public.sys_mcp_server ADD COLUMN prompts JSONB NOT NULL DEFAULT '[]'")


def downgrade() -> None:
    op.execute("ALTER TABLE public.sys_mcp_server DROP COLUMN IF EXISTS resources")
    op.execute("ALTER TABLE public.sys_mcp_server DROP COLUMN IF EXISTS prompts")
