"""0038: sys_tool_audit  domain 列(工具网关,域标签)

D4 审计复用取最小面:域工具执行的域标签落既有审计表(同表同语义),
可空 VARCHAR——非域工具恒 NULL,零行为漂移;不加索引(域维度查询走
既有 tool_name 索引,域与工具名一一对应)。
"""

from sqlalchemy import text

from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execute(text("ALTER TABLE public.sys_tool_audit ADD COLUMN domain VARCHAR(32)"))


def downgrade() -> None:
    op.get_bind().execute(text("ALTER TABLE public.sys_tool_audit DROP COLUMN domain"))
