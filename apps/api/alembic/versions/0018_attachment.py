"""0018: multimodal-input 附件表

Revision ID: 0018
Revises: 0017
Create Date: 2026-08-23

sys_attachment:图片附件;status VARCHAR+CHECK 双态(UPLOADED/CONSUMED,
一次性消费语义);文件本体落 var/attachments/(库内只存元数据)。
**downgrade**:删表(新域零存量)。
"""

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

DDL = """
-- ============ attachment 域(public,030) ============
CREATE TABLE public.sys_attachment (
    id          UUID PRIMARY KEY,
    tenant_id   UUID NOT NULL,
    user_id     UUID NOT NULL,
    filename    VARCHAR(256) NOT NULL,
    mime        VARCHAR(32) NOT NULL,
    size_bytes  INTEGER NOT NULL,
    status      VARCHAR(12) NOT NULL DEFAULT 'UPLOADED',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    consumed_at TIMESTAMPTZ,
    CONSTRAINT ck_sys_attachment_status CHECK
        (status IN ('UPLOADED','CONSUMED'))
);
CREATE INDEX ix_sys_attachment_user ON public.sys_attachment (tenant_id, user_id, created_at DESC);

-- chat_message 纯增附件引用摘要列(030 消息回显)
ALTER TABLE public.chat_message ADD COLUMN IF NOT EXISTS attachments JSONB NOT NULL DEFAULT '[]';
"""


def _statements(ddl: str) -> list[str]:
    no_comments = "\n".join(line.split("--", 1)[0] for line in ddl.splitlines())
    return [s.strip() for s in no_comments.split(";") if s.strip()]


def upgrade() -> None:
    for stmt in _statements(DDL):
        op.execute(stmt)


def downgrade() -> None:
    op.execute("ALTER TABLE public.chat_message DROP COLUMN IF EXISTS attachments")
    op.execute("DROP TABLE IF EXISTS public.sys_attachment")
