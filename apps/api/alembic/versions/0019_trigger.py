"""0019: triggers-webhooks 域两表(031)

Revision ID: 0019
Revises: 0018
Create Date: 2026-08-24

sys_trigger_definition:触发器;type/target_type VARCHAR+CHECK;
webhook_path 随机 UUID 全局唯一(防枚举);secret_cipher AES-GCM;
next_fire_at 预计算(调度扫描用)。
sys_trigger_run:触发历史;status 三态 CHECK(SUCCESS/FAILED/SKIPPED)。
**downgrade**:删两表(新域零存量)。
"""

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

DDL = """
-- ============ trigger 域(public,031) ============
CREATE TABLE public.sys_trigger_definition (
    id              UUID PRIMARY KEY,
    tenant_id       UUID NOT NULL,
    tg_key          VARCHAR(64) NOT NULL,
    name            VARCHAR(128) NOT NULL,
    type            VARCHAR(8) NOT NULL,
    target_type     VARCHAR(8) NOT NULL,
    target_key      VARCHAR(64) NOT NULL,
    cron_expr       VARCHAR(64),
    min_interval_sec INTEGER NOT NULL DEFAULT 60,
    webhook_path    UUID,
    secret_cipher   TEXT NOT NULL DEFAULT '',
    adapter         VARCHAR(16) NOT NULL DEFAULT 'custom',
    notify_url      VARCHAR(512) NOT NULL DEFAULT '',
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    created_by      UUID NOT NULL,
    last_fired_at   TIMESTAMPTZ,
    next_fire_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_sys_trigger_def_type CHECK (type IN ('CRON','WEBHOOK')),
    CONSTRAINT ck_sys_trigger_def_target CHECK (target_type IN ('workflow','bot')),
    CONSTRAINT ck_sys_trigger_def_adapter CHECK (adapter IN ('custom','github'))
);
CREATE UNIQUE INDEX ux_sys_trigger_def_key
    ON public.sys_trigger_definition (tenant_id, tg_key);
CREATE UNIQUE INDEX ux_sys_trigger_def_webhook
    ON public.sys_trigger_definition (webhook_path);

CREATE TABLE public.sys_trigger_run (
    id          UUID PRIMARY KEY,
    tenant_id   UUID NOT NULL,
    trigger_id  UUID NOT NULL,
    status      VARCHAR(12) NOT NULL,
    error       VARCHAR(2000) NOT NULL DEFAULT '',
    duration_ms INTEGER NOT NULL DEFAULT 0,
    target_run_ref VARCHAR(64) NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_sys_trigger_run_status CHECK
        (status IN ('SUCCESS','FAILED','SKIPPED'))
);
CREATE INDEX ix_sys_trigger_run_trigger ON public.sys_trigger_run
    (trigger_id, created_at DESC);
"""


def _statements(ddl: str) -> list[str]:
    no_comments = "\n".join(line.split("--", 1)[0] for line in ddl.splitlines())
    return [s.strip() for s in no_comments.split(";") if s.strip()]


def upgrade() -> None:
    for stmt in _statements(DDL):
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.sys_trigger_run")
    op.execute("DROP TABLE IF EXISTS public.sys_trigger_definition")
