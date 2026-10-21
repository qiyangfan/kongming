"""新增支付单并扩展配额范围；为 Bot 用量记录归属与幂等序号。降级会恢复配额索引形态。"""

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

DDL = """
-- ============ 支付单(035) ============
CREATE TABLE public.sys_payment (
    id          UUID PRIMARY KEY,
    invoice_id  UUID NOT NULL,
    tenant_id   UUID NOT NULL,
    channel     VARCHAR(12) NOT NULL DEFAULT 'MOCK',
    amount      BIGINT NOT NULL,                -- 厘,快照自账单
    status      VARCHAR(12) NOT NULL DEFAULT 'CREATED',
    trade_no    VARCHAR(64),
    expires_at  TIMESTAMPTZ NOT NULL,
    paid_at     TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_sys_payment_channel CHECK (channel IN ('MOCK')),
    CONSTRAINT ck_sys_payment_status CHECK
        (status IN ('CREATED','PAID','CANCELLED','EXPIRED'))
);
-- 每账单仅一张在途(未决)支付单
CREATE UNIQUE INDEX ux_sys_payment_invoice_open
    ON public.sys_payment (invoice_id) WHERE status='CREATED';
CREATE INDEX idx_sys_payment_tenant
    ON public.sys_payment (tenant_id, created_at DESC);

-- ============ 配额策略扩 scope(Bot 维度,035) ============
ALTER TABLE public.sys_quota_policy
    ADD COLUMN scope VARCHAR(8) NOT NULL DEFAULT 'TENANT';
ALTER TABLE public.sys_quota_policy
    ADD COLUMN scope_ref UUID;                  -- BOT 时=bot_id
ALTER TABLE public.sys_quota_policy
    ADD CONSTRAINT ck_sys_quota_policy_scope CHECK (scope IN ('TENANT','BOT'));
ALTER TABLE public.sys_quota_policy
    ADD CONSTRAINT ck_sys_quota_policy_scope_ref CHECK
        ((scope='TENANT' AND scope_ref IS NULL)
         OR (scope='BOT' AND scope_ref IS NOT NULL));
DROP INDEX ux_sys_quota_policy_dim;
CREATE UNIQUE INDEX ux_sys_quota_policy_dim ON public.sys_quota_policy
    (tenant_id, dimension, period, scope,
     COALESCE(scope_ref, '00000000-0000-0000-0000-000000000000'::uuid));

-- ============ 用量表扩 bot 归属与幂等序号(035) ============
ALTER TABLE public.sys_llm_usage ADD COLUMN bot_id UUID;
ALTER TABLE public.sys_llm_usage ADD COLUMN seq INTEGER NOT NULL DEFAULT 0;
-- 幂等唯一索引**仅约束 bot 同步采集路径**(bot_id 非空):chat 渠道
-- fire-and-forget 存量行为(同 trace 多行 seq=0)不受唯一约束,零回归;
-- 回填 WHERE 与索引谓词对齐(存量 bot_id 全 NULL,无行受影响)
WITH ranked AS (
    SELECT id,
           ROW_NUMBER() OVER (PARTITION BY trace_id ORDER BY created_at, id) - 1
               AS rn
    FROM public.sys_llm_usage
    WHERE trace_id IS NOT NULL AND bot_id IS NOT NULL
)
UPDATE public.sys_llm_usage u SET seq = ranked.rn
FROM ranked WHERE u.id = ranked.id;
CREATE UNIQUE INDEX ux_llm_usage_trace_seq
    ON public.sys_llm_usage (trace_id, seq)
    WHERE trace_id IS NOT NULL AND bot_id IS NOT NULL;
"""

DOWN_DDL = """
DROP INDEX IF EXISTS ux_llm_usage_trace_seq;
ALTER TABLE public.sys_llm_usage DROP COLUMN IF EXISTS bot_id;
ALTER TABLE public.sys_llm_usage DROP COLUMN IF EXISTS seq;
DROP INDEX IF EXISTS ux_sys_quota_policy_dim;
-- BOT 和 TENANT 范围可同时存在同维度策略，先删除 BOT 行再回建。
-- 三列唯一索引,否则 down 在 23505 上失败(非幂等)
DELETE FROM public.sys_quota_policy WHERE scope = 'BOT';
CREATE UNIQUE INDEX ux_sys_quota_policy_dim
    ON public.sys_quota_policy (tenant_id, dimension, period);
ALTER TABLE public.sys_quota_policy
    DROP CONSTRAINT IF EXISTS ck_sys_quota_policy_scope_ref;
ALTER TABLE public.sys_quota_policy
    DROP CONSTRAINT IF EXISTS ck_sys_quota_policy_scope;
ALTER TABLE public.sys_quota_policy DROP COLUMN IF EXISTS scope_ref;
ALTER TABLE public.sys_quota_policy DROP COLUMN IF EXISTS scope;
DROP TABLE IF EXISTS public.sys_payment;
"""


def _statements(ddl: str) -> list[str]:
    no_comments = "\n".join(line.split("--", 1)[0] for line in ddl.splitlines())
    return [s.strip() for s in no_comments.split(";") if s.strip()]


def upgrade() -> None:
    for stmt in _statements(DDL):
        op.execute(stmt)


def downgrade() -> None:
    for stmt in _statements(DOWN_DDL):
        op.execute(stmt)
