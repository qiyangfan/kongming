"""0045: 触发器失败重试策略(101-trigger-retry-policy)

- sys_trigger_definition 加 max_retries INTEGER NOT NULL DEFAULT 0
  (CHECK 0–5;0=不重试,存量行缺省零回归)。
- sys_trigger_run 加 retry_of_run_id UUID NULL(重试行追溯首跑)、
  attempt INTEGER NOT NULL DEFAULT 1(尝试序号冗余,历史查询免递归)、
  next_retry_at TIMESTAMPTZ NULL(挂起重试的到期时刻)。
- status CHECK 扩展 'PENDING':挂起重试为独立 run 行(调度重排形态),
  派发执行后原行落定终态,不覆盖先前失败行。
- 部分索引:调度 tick 扫描条件(挂起+到期)行数极小,部分索引即可。

**downgrade**:删索引/列,恢复三态 CHECK(PENDING 行先收敛为 FAILED,
不留悬挂调度态)。
"""

from sqlalchemy import text

from alembic import op

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    b = op.get_bind()
    b.execute(
        text(
            "ALTER TABLE public.sys_trigger_definition"
            " ADD COLUMN max_retries INTEGER NOT NULL DEFAULT 0"
        )
    )
    b.execute(
        text(
            "ALTER TABLE public.sys_trigger_definition"
            " ADD CONSTRAINT ck_sys_trigger_def_max_retries"
            " CHECK (max_retries BETWEEN 0 AND 5)"
        )
    )
    b.execute(
        text(
            "ALTER TABLE public.sys_trigger_run"
            " ADD COLUMN retry_of_run_id UUID,"
            " ADD COLUMN attempt INTEGER NOT NULL DEFAULT 1,"
            " ADD COLUMN next_retry_at TIMESTAMPTZ"
        )
    )
    # 挂起重试纳入 run 状态域(调度重排:挂起=独立 run 行,执行后原行落定)
    b.execute(text("ALTER TABLE public.sys_trigger_run DROP CONSTRAINT ck_sys_trigger_run_status"))
    b.execute(
        text(
            "ALTER TABLE public.sys_trigger_run"
            " ADD CONSTRAINT ck_sys_trigger_run_status"
            " CHECK (status IN ('SUCCESS','FAILED','SKIPPED','PENDING'))"
        )
    )
    b.execute(
        text(
            "CREATE INDEX ix_sys_trigger_run_retry_due"
            " ON public.sys_trigger_run (next_retry_at)"
            " WHERE status='PENDING' AND next_retry_at IS NOT NULL"
        )
    )


def downgrade() -> None:
    b = op.get_bind()
    b.execute(text("DROP INDEX IF EXISTS public.ix_sys_trigger_run_retry_due"))
    # 挂起行先收敛终态,再恢复三态 CHECK(不留悬挂调度态)
    b.execute(
        text(
            "UPDATE public.sys_trigger_run SET status='FAILED',"
            " error='迁移回退,挂起重试取消', next_retry_at=NULL"
            " WHERE status='PENDING'"
        )
    )
    b.execute(text("ALTER TABLE public.sys_trigger_run DROP CONSTRAINT ck_sys_trigger_run_status"))
    b.execute(
        text(
            "ALTER TABLE public.sys_trigger_run"
            " ADD CONSTRAINT ck_sys_trigger_run_status"
            " CHECK (status IN ('SUCCESS','FAILED','SKIPPED'))"
        )
    )
    b.execute(
        text(
            "ALTER TABLE public.sys_trigger_run"
            " DROP COLUMN IF EXISTS retry_of_run_id,"
            " DROP COLUMN IF EXISTS attempt,"
            " DROP COLUMN IF EXISTS next_retry_at"
        )
    )
    b.execute(
        text(
            "ALTER TABLE public.sys_trigger_definition"
            " DROP CONSTRAINT IF EXISTS ck_sys_trigger_def_max_retries"
        )
    )
    b.execute(text("ALTER TABLE public.sys_trigger_definition DROP COLUMN IF EXISTS max_retries"))
