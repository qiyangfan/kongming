"""0035: 角色自定义部门集合底座(datascope CUSTOM)

Revision ID: 0035
Revises: 0034
Create Date: 2026-10-01

CUSTOM(自定义部门集合)在 baseline 的 data_scope CHECK 里已占位(0001:75),
但执行侧一直 fail-closed 拒绝——能力"已登记未实现"。本迁移补存储底座:
- sys_role_dept:角色 ↔ 部门的关联表(复合主键 + 两侧 FK + dept_id 索引)。

为什么用关联表而不是 sys_role.data_scope_dept_ids JSON 列:权威库里"部门集合"
是关系不是文档——FK 完整性(删被引用的部门必须被拦下)、反查"哪些角色圈了
这个部门"、集合语义天然去重都靠关系约束兑现;JSON 列三样都给不出,给不出
就是把悬空授权语义写进生产库(与 ADR-004「状态列禁 SMALLINT 魔数」同源纪律:
能用约束表达的不变量,不留给注释与应用层自觉)。
约定同 baseline:UUID 主键片段沿用 sys_user_role/sys_role_permission 形态
(关联表无独立 id)、无状态列故无需 CHECK、纯加法可回滚。
"""

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
