"""0022: developer-portal-billing 域三表

Revision ID: 0022
Revises: 0021
Create Date: 2026-08-24

sys_quota_policy:租户三维配额策略(dimension/period CHECK);
sys_billing_plan:套餐(金额 INTEGER 厘,禁浮点);
sys_invoice:周期账单(明细 JSONB,PENDING 出账语义)。
**downgrade**:删三表(新域零存量)。
"""

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

DDL = """
-- ============ billing 域(public,034) ============
CREATE TABLE public.sys_quota_policy (
    id          UUID PRIMARY KEY,
    tenant_id   UUID NOT NULL,
    dimension   VARCHAR(8) NOT NULL,
    period      VARCHAR(8) NOT NULL DEFAULT 'MONTHLY',
    limit_value BIGINT NOT NULL,
    warn_pct    INTEGER NOT NULL DEFAULT 80,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_sys_quota_policy_dimension CHECK
        (dimension IN ('TOKENS','CALLS','COST')),
    CONSTRAINT ck_sys_quota_policy_period CHECK (period IN ('MONTHLY')),
    CONSTRAINT ck_sys_quota_policy_warn CHECK (warn_pct BETWEEN 1 AND 100)
);
CREATE UNIQUE INDEX ux_sys_quota_policy_dim
    ON public.sys_quota_policy (tenant_id, dimension, period);

CREATE TABLE public.sys_billing_plan (
    id              UUID PRIMARY KEY,
    plan_key        VARCHAR(64) NOT NULL UNIQUE,
    name            VARCHAR(128) NOT NULL,
    monthly_tokens  BIGINT NOT NULL DEFAULT 0,   -- 0=不限
    monthly_calls   BIGINT NOT NULL DEFAULT 0,
    price_per_mtok  INTEGER NOT NULL DEFAULT 0,  -- 厘/千 token
    price_per_call  INTEGER NOT NULL DEFAULT 0,  -- 厘/次
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE public.sys_tenant ADD COLUMN IF NOT EXISTS plan_id UUID;

CREATE TABLE public.sys_invoice (
    id          UUID PRIMARY KEY,
    tenant_id   UUID NOT NULL,
    period      VARCHAR(7) NOT NULL,              -- YYYY-MM
    usage_json  JSONB NOT NULL,
    amount      BIGINT NOT NULL DEFAULT 0,        -- 厘
    status      VARCHAR(12) NOT NULL DEFAULT 'PENDING',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_sys_invoice_status CHECK (status IN ('PENDING','PAID','VOID'))
);
CREATE UNIQUE INDEX ux_sys_invoice_period ON public.sys_invoice (tenant_id, period);
"""


def _statements(ddl: str) -> list[str]:
    no_comments = "\n".join(line.split("--", 1)[0] for line in ddl.splitlines())
    return [s.strip() for s in no_comments.split(";") if s.strip()]


def upgrade() -> None:
    for stmt in _statements(DDL):
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.sys_invoice")
    op.execute("DROP TABLE IF EXISTS public.sys_billing_plan")
    op.execute("DROP TABLE IF EXISTS public.sys_quota_policy")
    op.execute("ALTER TABLE public.sys_tenant DROP COLUMN IF EXISTS plan_id")
