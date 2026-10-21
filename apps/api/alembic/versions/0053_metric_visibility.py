"""为指标定义增加公共或部门可见范围，默认保持租户内公共。部门归属使用 RESTRICT 外键；降级会移除可见性字段。"""

from alembic import op

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE rag.metric_definitions ADD COLUMN visible_scope VARCHAR(8)"
        " NOT NULL DEFAULT 'ALL' CHECK (visible_scope IN ('ALL','DEPT'))"
    )
    op.execute(
        "ALTER TABLE rag.metric_definitions ADD COLUMN dept_id UUID"
        " REFERENCES public.sys_dept(id) ON DELETE RESTRICT"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE rag.metric_definitions DROP COLUMN IF EXISTS dept_id")
    op.execute("ALTER TABLE rag.metric_definitions DROP COLUMN IF EXISTS visible_scope")
