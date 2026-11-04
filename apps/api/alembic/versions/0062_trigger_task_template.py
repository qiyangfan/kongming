"""sys_trigger_definition 增 task 目标:task_template 列 + target_type CHECK 扩展。

task_template 仅 target_type=task 使用(goal 文本模板/数据源/预算/协作拓扑/
确认模式/过滤条件/线程键字段,整体单列 JSONB);CHECK 约束同步扩展 task,
存量行零影响。
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0062"
down_revision: str | Sequence[str] | None = "0061"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE public.sys_trigger_definition ADD COLUMN task_template JSONB")
    op.execute(
        "ALTER TABLE public.sys_trigger_definition DROP CONSTRAINT ck_sys_trigger_def_target"
    )
    op.execute(
        "ALTER TABLE public.sys_trigger_definition"
        " ADD CONSTRAINT ck_sys_trigger_def_target"
        " CHECK (target_type IN ('workflow','bot','task'))"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE public.sys_trigger_definition DROP CONSTRAINT ck_sys_trigger_def_target"
    )
    op.execute(
        "ALTER TABLE public.sys_trigger_definition"
        " ADD CONSTRAINT ck_sys_trigger_def_target"
        " CHECK (target_type IN ('workflow','bot'))"
    )
    op.execute("ALTER TABLE public.sys_trigger_definition DROP COLUMN task_template")
