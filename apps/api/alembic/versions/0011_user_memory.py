"""0011: agent-memory 域表

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-23

user_memory:用户长期记忆(user_id+tenant_id 双维归属;HNSW 部分索引仅
覆盖 dormant=false——休眠记忆不参与召回,索引不承担死重);embedding
可空(显式形成同步路径 embedding 失败不阻断,编辑时重算补齐)。
**downgrade**:删表(新域零存量)。
"""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

DDL = """
-- ============ memory 域(public,022) ============
CREATE TABLE public.user_memory (
    id          UUID PRIMARY KEY,
    user_id     UUID NOT NULL,
    tenant_id   UUID NOT NULL,
    content     TEXT NOT NULL,
    category    VARCHAR(32) NOT NULL DEFAULT 'general',
    confidence  NUMERIC(3,2) NOT NULL,               -- 0.00-1.00
    source      VARCHAR(16) NOT NULL
                CHECK (source IN ('EXPLICIT', 'IMPLICIT')),
    embedding   vector(1536),                        -- 可空(异步补算)
    last_hit_at TIMESTAMPTZ,
    dormant     BOOLEAN NOT NULL DEFAULT false,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_user_memory_user_tenant ON public.user_memory (user_id, tenant_id);
CREATE INDEX idx_user_memory_embedding ON public.user_memory
    USING hnsw (embedding vector_cosine_ops) WHERE dormant = false;
"""


def _statements(ddl: str) -> list[str]:
    no_comments = "\n".join(line.split("--", 1)[0] for line in ddl.splitlines())
    return [s.strip() for s in no_comments.split(";") if s.strip()]


def upgrade() -> None:
    for stmt in _statements(DDL):
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.user_memory")
