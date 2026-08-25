"""0021: bot-store 域(033)

Revision ID: 0021
Revises: 0020
Create Date: 2026-08-24

sys_bot_definition 纯增上架四列(greeting/suggestions JSONB);
sys_bot_favorite/sys_bot_rating:互动两表,一人一 Bot 唯一约束。
**downgrade**:删两表+回滚四列(新域零存量)。
"""

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

DDL = """
-- ============ store 域(public,033) ============
ALTER TABLE public.sys_bot_definition ADD COLUMN IF NOT EXISTS greeting VARCHAR(500) NOT NULL DEFAULT '';
ALTER TABLE public.sys_bot_definition ADD COLUMN IF NOT EXISTS suggestions JSONB NOT NULL DEFAULT '[]';
ALTER TABLE public.sys_bot_definition ADD COLUMN IF NOT EXISTS published_at TIMESTAMPTZ;
ALTER TABLE public.sys_bot_definition ADD COLUMN IF NOT EXISTS published_by UUID;
ALTER TABLE public.sys_bot_definition ADD COLUMN IF NOT EXISTS use_count INTEGER NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS ix_sys_bot_definition_published
    ON public.sys_bot_definition (tenant_id, published_at DESC) WHERE published_at IS NOT NULL;

CREATE TABLE public.sys_bot_favorite (
    id          UUID PRIMARY KEY,
    tenant_id   UUID NOT NULL,
    user_id     UUID NOT NULL,
    bot_id      UUID NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX ux_sys_bot_favorite ON public.sys_bot_favorite (user_id, bot_id);

CREATE TABLE public.sys_bot_rating (
    id          UUID PRIMARY KEY,
    tenant_id   UUID NOT NULL,
    user_id     UUID NOT NULL,
    bot_id      UUID NOT NULL,
    score       INTEGER NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_sys_bot_rating_score CHECK (score BETWEEN 1 AND 5)
);
CREATE UNIQUE INDEX ux_sys_bot_rating ON public.sys_bot_rating (user_id, bot_id);
"""


def _statements(ddl: str) -> list[str]:
    no_comments = "\n".join(line.split("--", 1)[0] for line in ddl.splitlines())
    return [s.strip() for s in no_comments.split(";") if s.strip()]


def upgrade() -> None:
    for stmt in _statements(DDL):
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.sys_bot_rating")
    op.execute("DROP TABLE IF EXISTS public.sys_bot_favorite")
    op.execute("ALTER TABLE public.sys_bot_definition DROP COLUMN IF EXISTS use_count")
    op.execute("ALTER TABLE public.sys_bot_definition DROP COLUMN IF EXISTS published_by")
    op.execute("ALTER TABLE public.sys_bot_definition DROP COLUMN IF EXISTS published_at")
    op.execute("ALTER TABLE public.sys_bot_definition DROP COLUMN IF EXISTS suggestions")
    op.execute("ALTER TABLE public.sys_bot_definition DROP COLUMN IF EXISTS greeting")
