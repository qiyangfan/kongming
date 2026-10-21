"""0020: team-workspace 域两表 + 三处归属列

Revision ID: 0020
Revises: 0019
Create Date: 2026-08-24

sys_team/sys_team_member:团队与成员(role VARCHAR+CHECK);
sys_bot_definition/sys_workflow_def/user_memory  team_id
(NULL=个人,存量)。
**downgrade**:删列+删两表(新域零存量)。
"""

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

DDL = """
-- ============ team 域(public,032) ============
CREATE TABLE public.sys_team (
    id          UUID PRIMARY KEY,
    tenant_id   UUID NOT NULL,
    team_key    VARCHAR(64) NOT NULL,
    name        VARCHAR(128) NOT NULL,
    created_by  UUID NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX ux_sys_team_key ON public.sys_team (tenant_id, team_key);

CREATE TABLE public.sys_team_member (
    team_id     UUID NOT NULL,
    user_id     UUID NOT NULL,
    role        VARCHAR(8) NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT pk_sys_team_member PRIMARY KEY (team_id, user_id),
    CONSTRAINT ck_sys_team_member_role CHECK (role IN ('OWNER','MEMBER','VIEWER'))
);
CREATE INDEX ix_sys_team_member_user ON public.sys_team_member (user_id);

-- 三处归属列(纯增,NULL=个人)
ALTER TABLE public.sys_bot_definition ADD COLUMN IF NOT EXISTS team_id UUID;
ALTER TABLE public.sys_workflow_def ADD COLUMN IF NOT EXISTS team_id UUID;
ALTER TABLE public.user_memory ADD COLUMN IF NOT EXISTS team_id UUID;
CREATE INDEX IF NOT EXISTS ix_sys_bot_definition_team ON public.sys_bot_definition (team_id);
CREATE INDEX IF NOT EXISTS ix_sys_workflow_def_team ON public.sys_workflow_def (team_id);
CREATE INDEX IF NOT EXISTS ix_user_memory_team ON public.user_memory (team_id);
"""


def _statements(ddl: str) -> list[str]:
    no_comments = "\n".join(line.split("--", 1)[0] for line in ddl.splitlines())
    return [s.strip() for s in no_comments.split(";") if s.strip()]


def upgrade() -> None:
    for stmt in _statements(DDL):
        op.execute(stmt)


def downgrade() -> None:
    op.execute("ALTER TABLE public.user_memory DROP COLUMN IF EXISTS team_id")
    op.execute("ALTER TABLE public.sys_workflow_def DROP COLUMN IF EXISTS team_id")
    op.execute("ALTER TABLE public.sys_bot_definition DROP COLUMN IF EXISTS team_id")
    op.execute("DROP TABLE IF EXISTS public.sys_team_member")
    op.execute("DROP TABLE IF EXISTS public.sys_team")
