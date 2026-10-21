"""配额策略新增 USER 和 TEAM 范围。降级时若仍有这两类策略会拒绝执行，需先清理策略行。"""

from sqlalchemy import text

from alembic import op

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    b = op.get_bind()
    b.execute(text("ALTER TABLE public.sys_quota_policy DROP CONSTRAINT ck_sys_quota_policy_scope"))
    b.execute(
        text(
            "ALTER TABLE public.sys_quota_policy"
            " ADD CONSTRAINT ck_sys_quota_policy_scope"
            " CHECK (scope IN ('TENANT','BOT','USER','TEAM'))"
        )
    )
    b.execute(
        text("ALTER TABLE public.sys_quota_policy DROP CONSTRAINT ck_sys_quota_policy_scope_ref")
    )
    b.execute(
        text(
            "ALTER TABLE public.sys_quota_policy"
            " ADD CONSTRAINT ck_sys_quota_policy_scope_ref CHECK"
            " ((scope='TENANT' AND scope_ref IS NULL)"
            " OR (scope IN ('BOT','USER','TEAM') AND scope_ref IS NOT NULL))"
        )
    )


def downgrade() -> None:
    b = op.get_bind()
    # 存量守卫:新级策略行存在时拒降(收窄枚举会使行违约,静默删数据不可取)
    n = int(
        b.execute(
            text("SELECT count(*) FROM public.sys_quota_policy WHERE scope IN ('USER','TEAM')")
        ).scalar_one()
    )
    if n > 0:
        raise RuntimeError(
            f"sys_quota_policy 存在 {n} 条 USER/TEAM 级策略,拒绝降级;请先删除新级策略行再回退迁移"
        )
    b.execute(text("ALTER TABLE public.sys_quota_policy DROP CONSTRAINT ck_sys_quota_policy_scope"))
    b.execute(
        text(
            "ALTER TABLE public.sys_quota_policy"
            " ADD CONSTRAINT ck_sys_quota_policy_scope CHECK (scope IN ('TENANT','BOT'))"
        )
    )
    b.execute(
        text("ALTER TABLE public.sys_quota_policy DROP CONSTRAINT ck_sys_quota_policy_scope_ref")
    )
    b.execute(
        text(
            "ALTER TABLE public.sys_quota_policy"
            " ADD CONSTRAINT ck_sys_quota_policy_scope_ref CHECK"
            " ((scope='TENANT' AND scope_ref IS NULL)"
            " OR (scope='BOT' AND scope_ref IS NOT NULL))"
        )
    )
