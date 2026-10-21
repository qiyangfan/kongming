"""新增部门表和用户部门关联字段，为部门数据范围提供存储。"""

from sqlalchemy import text

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

DDL = """
CREATE TABLE public.sys_dept (
    id         UUID PRIMARY KEY,
    parent_id  UUID REFERENCES public.sys_dept(id),           -- NULL=根部门
    dept_name  VARCHAR(100) NOT NULL,
    tenant_id  UUID NOT NULL REFERENCES public.sys_tenant(id),
    sort       INTEGER      NOT NULL DEFAULT 0,
    status     VARCHAR(16)  NOT NULL DEFAULT 'ACTIVE'
               CHECK (status IN ('ACTIVE', 'DISABLED')),
    created_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX idx_sys_dept_parent ON public.sys_dept (parent_id);
CREATE INDEX idx_sys_dept_tenant ON public.sys_dept (tenant_id);

ALTER TABLE public.sys_user
    ADD COLUMN dept_id UUID REFERENCES public.sys_dept(id);
CREATE INDEX idx_sys_user_dept ON public.sys_user (dept_id);
"""


def _statements(ddl: str) -> list[str]:
    """拆分前剥离 -- 行注释再按分号切(同 baseline 约定)。"""
    no_comments = "\n".join(line.split("--", 1)[0] for line in ddl.splitlines())
    return [s.strip() for s in no_comments.split(";") if s.strip()]


def upgrade() -> None:
    for stmt in _statements(DDL):
        op.get_bind().execute(text(stmt))


def downgrade() -> None:
    for stmt in _statements(
        """
        ALTER TABLE public.sys_user DROP COLUMN IF EXISTS dept_id;
        DROP TABLE IF EXISTS public.sys_dept;
        """
    ):
        op.get_bind().execute(text(stmt))
