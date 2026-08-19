"""0004: 用户名唯一约束软删感知(rbac spec「用户名唯一性(软删过滤)」)

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-19

baseline 的 username UNIQUE 是全表硬约束,软删记录仍占名,
「存在已软删的同名用户,创建同名新用户应成功」在 schema 层不可达。
换成部分唯一索引(仅未软删行参与唯一)——软删+唯一的标准解法(ADR-004 演进)。
纯约束形态变更,无数据迁移。
**downgrade 前置条件**:还原全表 UNIQUE 前,须先清除「已软删且同名」的冗余行
(合法数据形态会令 ADD CONSTRAINT 失败):
  DELETE FROM sys_user a USING sys_user b
    WHERE a.deleted_at IS NOT NULL AND b.deleted_at IS NULL AND a.username = b.username;
"""

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
