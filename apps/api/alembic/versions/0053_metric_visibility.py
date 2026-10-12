"""0053: 指标定义可见性(110-metric-visibility-data-scope)

指标口径是提示词资产,可能含敏感业务信息(如「VIP 客户复购率」的口径暴露
客群规则)。注入面此前仅按 tenant+datasource 过滤,租户内全量可见。本迁移
给指标定义补可见范围维度:

- `visible_scope VARCHAR(8) NOT NULL DEFAULT 'ALL' CHECK IN ('ALL','DEPT')`:
  ALL=租户内公共(缺省,存量零回归);DEPT=仅归属部门集合内调用者可见。
- `dept_id UUID NULL REFERENCES public.sys_dept(id)`:DEPT 档的归属部门
  (RESTRICT——部门删除被引用即拒,保守零级联意外);ALL 档恒 NULL。
- 零数据回填:缺省即 ALL 档=既有行为。

注入过滤(应用层,semantic/repository.list_metrics 可见性参数 + service.enrich
按调用者 data_scope 解析):调用者有效范围=启用角色最宽松档;ALL=全量;
DEPT/DEPT_AND_BELOW/CUSTOM=ALL 档 ∪ 部门集合内 DEPT 档;SELF/无身份=仅 ALL 档;
CUSTOM 空集合读面降级(仅 ALL 档,不阻断召回)。enrich 在检索上下文缓存之外,
缓存键零改动。

**downgrade**:仅删两列(定义行保留;可见性语义随列消失,回退代码版本时
注入面回到租户内全量的旧行为)。
"""

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
