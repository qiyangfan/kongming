"""将用户名唯一性改为仅约束未软删用户。降级前需处理与未删除用户同名的软删记录。"""

from sqlalchemy import text

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

DDL = """
ALTER TABLE public.sys_user DROP CONSTRAINT sys_user_username_key;
CREATE UNIQUE INDEX uq_sys_user_username_alive
    ON public.sys_user (username) WHERE deleted_at IS NULL;
"""

DOWNGRADE = """
DROP INDEX IF EXISTS public.uq_sys_user_username_alive;
ALTER TABLE public.sys_user ADD CONSTRAINT sys_user_username_key UNIQUE (username);
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
