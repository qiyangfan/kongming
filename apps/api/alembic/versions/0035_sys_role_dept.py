"""新增角色与部门关联表，使用外键保证部门引用有效，关联行天然去重。"""

from sqlalchemy import text

from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None

DDL = """
CREATE TABLE public.sys_role_dept (
    role_id  UUID NOT NULL REFERENCES public.sys_role(id),             -- 整包替换由应用层事务收口
    dept_id  UUID NOT NULL REFERENCES public.sys_dept(id),             -- 被引用的部门不可删(FK 即前置校验)
    PRIMARY KEY (role_id, dept_id)
);
-- 反查"角色圈了哪些部门"与按部门收敛角色集合都走这一侧
CREATE INDEX idx_sys_role_dept_dept ON public.sys_role_dept (dept_id);
"""


def _statements(ddl: str) -> list[str]:
    """拆分前剥离 -- 行注释再按分号切(同 baseline/0003 约定)。"""
    no_comments = "\n".join(line.split("--", 1)[0] for line in ddl.splitlines())
    return [s.strip() for s in no_comments.split(";") if s.strip()]


def upgrade() -> None:
    for stmt in _statements(DDL):
        op.get_bind().execute(text(stmt))


def downgrade() -> None:
    for stmt in _statements("DROP TABLE IF EXISTS public.sys_role_dept"):
        op.get_bind().execute(text(stmt))
