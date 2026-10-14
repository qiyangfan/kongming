"""0055: judge 评审对象类型收编圆桌(115)

- judge_run.object_type CHECK 增 'roundtable':圆桌运行成为独立评审对象
  (RoundtableRubric v1;对象取数=拓扑形态为 roundtable 的协作运行)。
"""

from sqlalchemy import text

from alembic import op

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execute(
        text("ALTER TABLE public.judge_run DROP CONSTRAINT IF EXISTS judge_run_object_type_check")
    )
    op.get_bind().execute(
        text(
            "ALTER TABLE public.judge_run"
            " ADD CONSTRAINT judge_run_object_type_check CHECK ("
            " object_type IN ('report', 'task', 'collab', 'answer', 'roundtable'))"
        )
    )


def downgrade() -> None:
    op.get_bind().execute(
        text("ALTER TABLE public.judge_run DROP CONSTRAINT IF EXISTS judge_run_object_type_check")
    )
    op.get_bind().execute(
        text(
            "ALTER TABLE public.judge_run"
            " ADD CONSTRAINT judge_run_object_type_check CHECK ("
            " object_type IN ('report', 'task', 'collab', 'answer'))"
        )
    )
