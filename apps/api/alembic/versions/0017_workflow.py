"""0017: workflow-orchestration 域两表(029)

Revision ID: 0017
Revises: 0016
Create Date: 2026-08-23

sys_workflow_def:工作流定义,版本冻结(tenant+wf_key+version 唯一,
旧版本无更新路径);definition JSONB={nodes:[{id,type,config,depends_on}]}。
sys_workflow_run:执行历史;status VARCHAR+CHECK 四终态(ADR-004)。
**downgrade**:删两表(新域零存量)。
"""

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

DDL = """
-- ============ workflow 域(public,029) ============
CREATE TABLE public.sys_workflow_def (
    id          UUID PRIMARY KEY,
    tenant_id   UUID NOT NULL,
    wf_key      VARCHAR(64) NOT NULL,
    name        VARCHAR(128) NOT NULL,
    version     INTEGER NOT NULL,
    definition  JSONB NOT NULL,
    enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    created_by  UUID NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX ux_sys_workflow_def_key_version
    ON public.sys_workflow_def (tenant_id, wf_key, version);
CREATE INDEX ix_sys_workflow_def_tenant ON public.sys_workflow_def (tenant_id, wf_key);

CREATE TABLE public.sys_workflow_run (
    id          UUID PRIMARY KEY,
    tenant_id   UUID NOT NULL,
    wf_key      VARCHAR(64) NOT NULL,
    version     INTEGER NOT NULL,
    trigger_source VARCHAR(8) NOT NULL,
    inputs      JSONB NOT NULL DEFAULT '{}',
    status      VARCHAR(16) NOT NULL,
    node_runs   JSONB NOT NULL DEFAULT '[]',
    error       VARCHAR(2000) NOT NULL DEFAULT '',
    duration_ms INTEGER NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_sys_workflow_run_status CHECK
        (status IN ('SUCCESS','FAILED','TIMEOUT','STEP_LIMIT')),
    CONSTRAINT ck_sys_workflow_run_trigger CHECK
        (trigger_source IN ('MANUAL','API'))
);
CREATE INDEX ix_sys_workflow_run_key ON public.sys_workflow_run
    (tenant_id, wf_key, created_at DESC);
"""


def _statements(ddl: str) -> list[str]:
    """asyncpg 预备语句不许多命令(0014 同款):剥注释按分号切。"""
    no_comments = "\n".join(line.split("--", 1)[0] for line in ddl.splitlines())
    return [s.strip() for s in no_comments.split(";") if s.strip()]


def upgrade() -> None:
    for stmt in _statements(DDL):
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.sys_workflow_run")
    op.execute("DROP TABLE IF EXISTS public.sys_workflow_def")
