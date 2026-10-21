"""0044: 工具域启停持久化(落地)

域启停由进程内存态升为持久状态:多副本部署与重启不再丢失运维裁决。
- 表 sys_tool_domain_state:domain_id 主键,disabled 即运维意图;
- 启动装载(lifespan)→ 网关初始停用集;开关端点写穿(本地即时生效+
 持久化);未登记域=启用。
"""

from sqlalchemy import text

from alembic import op

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execute(
        text(
            """
            CREATE TABLE public.sys_tool_domain_state (
                domain_id VARCHAR(64) PRIMARY KEY,
                disabled BOOLEAN NOT NULL DEFAULT FALSE,
                updated_by VARCHAR(64) NOT NULL DEFAULT '',
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    )


def downgrade() -> None:
    op.get_bind().execute(text("DROP TABLE IF EXISTS public.sys_tool_domain_state"))
