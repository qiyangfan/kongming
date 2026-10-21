"""新增 Bot 定义表和 API key 表。API key 只存 SHA-256 哈希，明文仅在创建响应中返回。"""

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

DDL = """
-- ============ bot 域(public,025) ============
CREATE TABLE public.sys_bot_definition (
    id             UUID PRIMARY KEY,
    tenant_id      UUID NOT NULL,
    bot_key        VARCHAR(64) NOT NULL,
    name           VARCHAR(128) NOT NULL,
    system_prompt  TEXT NOT NULL,
    tools          JSONB NOT NULL DEFAULT '[]',        -- 工具名数组(注册表名)
    datasource_key VARCHAR(64) NOT NULL DEFAULT 'default',
    model          VARCHAR(64),                        -- 021 注册表名;空=主模型
    enabled        BOOLEAN NOT NULL DEFAULT true,
    is_public      BOOLEAN NOT NULL DEFAULT false,
    created_by     UUID NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_bot_tenant_key UNIQUE (tenant_id, bot_key)
);
CREATE INDEX idx_bot_tenant ON public.sys_bot_definition (tenant_id);
-- bot_key 全局唯一，公开嵌入页按 key 查找，避免不同租户出现同 key。
CREATE UNIQUE INDEX uq_bot_key_global ON public.sys_bot_definition (bot_key);

CREATE TABLE public.sys_api_key (
    id           UUID PRIMARY KEY,
    tenant_id    UUID NOT NULL,
    bot_id       UUID NOT NULL REFERENCES public.sys_bot_definition(id) ON DELETE CASCADE,
    key_hash     VARCHAR(64) NOT NULL UNIQUE,          -- sha256 hex(不存明文)
    name         VARCHAR(128) NOT NULL,
    scopes       JSONB NOT NULL DEFAULT '{}',          -- {bot_keys: [...]}
    revoked_at   TIMESTAMPTZ,
    last_used_at TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_api_key_bot ON public.sys_api_key (bot_id);
"""


def _statements(ddl: str) -> list[str]:
    no_comments = "\n".join(line.split("--", 1)[0] for line in ddl.splitlines())
    return [s.strip() for s in no_comments.split(";") if s.strip()]


def upgrade() -> None:
    for stmt in _statements(DDL):
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.sys_api_key")
    op.execute("DROP TABLE IF EXISTS public.sys_bot_definition")
