"""0005: sys_datasource 补 name / default_schema 两列(008 动态数据源)

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-19

baseline 建表时漏建的两列(datasource spec 早已立约):
- name VARCHAR(128) NOT NULL —— 选项与列表展示名;
- default_schema VARCHAR(64) NOT NULL DEFAULT 'public' —— 动态路径 search_path 首段。
表在 baseline 后无任何写入方(空表),NOT NULL 不加回填默认值亦可瞬时达成;
不加 CHECK(key/default_schema 合法性由传输层 pattern + 领域校验收口,007 role_key 同款)。
**upgrade 前置条件**:若某环境 sys_datasource 已有手工插入行,name 无默认值会
响亮失败(不腐数据)——先 `SELECT count(*) FROM public.sys_datasource` 确认空表,
有行则先补值或清行(R1-O3 注记)。
**downgrade 前置条件**:若已注册动态数据源行,须先 DELETE 清行再DROP列
(列上有数据不影响 DROP,此处注记仅提示语义:回滚即放弃全部已注册数据源)。
"""

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
