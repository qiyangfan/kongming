"""本地计费控制面退役:四张控制表改名 legacy_,保留历史行只读。

计划/配额策略/账单/支付状态改由锦官拥有;kongming 不再读写这些表。
改名而非删除——历史数据保留可追溯,降级可原样恢复表名。
"""

from sqlalchemy import text

from alembic import op

revision = "0071"
down_revision = "0070"
branch_labels = None
depends_on = None

TABLES = ("sys_quota_policy", "sys_billing_plan", "sys_invoice", "sys_payment")


def _rename(source: str, target: str) -> None:
    op.get_bind().execute(
        text(
            "DO $$ BEGIN"  # noqa: S608  # 表名来自本文件常量元组,无外部输入
            " IF EXISTS (SELECT 1 FROM pg_tables WHERE schemaname='public'"
            f" AND tablename='{source}')"
            " THEN"
            f" ALTER TABLE public.{source} RENAME TO {target};"
            " END IF;"
            " END $$;"
        )
    )


def upgrade() -> None:
    for name in TABLES:
        _rename(name, f"legacy_{name.removeprefix('sys_')}")


def downgrade() -> None:
    for name in TABLES:
        _rename(f"legacy_{name.removeprefix('sys_')}", name)
