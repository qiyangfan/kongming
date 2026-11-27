"""租户名宽度对齐锦官 X-01 快照。

锦官 tenant_directory.name 允许 128 字符；本表原为 100，超长会在身份缓存
应用时整份快照失败。只放宽宽度，不改语义。
"""

from sqlalchemy import text

from alembic import op

revision = "0075"
down_revision = "0074"
branch_labels = None
depends_on = None

UPGRADE = """
ALTER TABLE public.sys_tenant
    ALTER COLUMN tenant_name TYPE VARCHAR(128);
"""

DOWNGRADE = """
ALTER TABLE public.sys_tenant
    ALTER COLUMN tenant_name TYPE VARCHAR(100);
"""


def _statements(ddl: str) -> list[str]:
    return [s.strip() for s in ddl.split(";") if s.strip()]


def upgrade() -> None:
    for stmt in _statements(UPGRADE):
        op.get_bind().execute(text(stmt))


def downgrade() -> None:
    for stmt in _statements(DOWNGRADE):
        op.get_bind().execute(text(stmt))
