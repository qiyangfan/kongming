"""为数据源增加展示名称和默认 schema。升级要求现有数据源行已补齐名称；降级会移除这两列及其值。"""

from sqlalchemy import text

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

DDL = """
ALTER TABLE public.sys_datasource ADD COLUMN name VARCHAR(128) NOT NULL;
ALTER TABLE public.sys_datasource
    ADD COLUMN default_schema VARCHAR(64) NOT NULL DEFAULT 'public';
"""

DOWNGRADE = """
ALTER TABLE public.sys_datasource DROP COLUMN default_schema;
ALTER TABLE public.sys_datasource DROP COLUMN name;
"""


def _statements(ddl: str) -> list[str]:
    """拆分前剥离 -- 行注释再按分号切(同 baseline 约定)。"""
    no_comments = "\n".join(line.split("--", 1)[0] for line in ddl.splitlines())
    return [s.strip() for s in no_comments.split(";") if s.strip()]


def upgrade() -> None:
    for stmt in _statements(DDL):
        op.get_bind().execute(text(stmt))


def downgrade() -> None:
    for stmt in _statements(DOWNGRADE):
        op.get_bind().execute(text(stmt))
