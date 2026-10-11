"""0046: 配额策略 scope 扩展 USER/TEAM(103-quota-user-team-scope)

sys_quota_policy 纯枚举面扩展:ck_sys_quota_policy_scope 由
('TENANT','BOT') 放宽为 ('TENANT','BOT','USER','TEAM');
ck_sys_quota_policy_scope_ref 互斥分支同步扩展(BOT/USER/TEAM 一律
scope_ref NOT NULL,TENANT 强制 NULL)——零数据迁移,存量 TENANT/BOT
行不受影响。USER 级=scope_ref 绑定租户内用户,TEAM 级=绑定租户内团队
(用量聚合联表解析,零用量表 schema 变更,见 change design)。

**downgrade**:带存量守卫——存在 USER/TEAM 行时拒绝降级(RuntimeError),
须先人工清理新级策略行再回退;守卫通过后恢复 0023 形态双约束。
"""

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
