"""0003: 部门底座(rbac spec「部门管理」+ datascope DEPT 系支撑)

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-18

baseline 合并 V6 时缓建 dept(「dept_id 弃」=缓建非否决),004 执行路由
落地 data_scope 行级改写,补齐存储底座:
- sys_dept        部门表(parent_id 自引用,NULL=根;递归 CTE 查子孙)
- sys_user.dept_id 用户绑部门(NULL=未绑定,DEPT 系 scope 安全降级)
约定同 baseline:UUID v7 主键 / VARCHAR+CHECK / timestamptz / 纯加法可回滚。
"""

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
