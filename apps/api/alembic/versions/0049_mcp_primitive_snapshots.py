"""0049: mcp server 配置行原语清单快照列(109-mcp-resources-prompts)

sys_mcp_server 纯增两列(design D5:快照列而非新表,量级小、生命周期同
server 行):
- resources JSONB:resources/list 发现快照([{uri, name, description,
  mimeType}]);既是详情面资源清单,也是 read_resource 工具的 uri 白名单
  (design D2:白名单来自探测快照,同工具「同步后可见」口径)。
- prompts JSONB:prompts/list 发现快照([{name, description, arguments}]);
  仅作清单展示,模板渲染预览实时拉取不落库(design D3)。
存量行默认 '[]' 零影响;**downgrade**:删两列(清单随列消失,配置不动)。
"""

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
