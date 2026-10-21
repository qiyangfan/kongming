"""新增租户 HTTP 连接器表。鉴权材料加密存储，配置不使用软删除；降级会删除整张连接器表。"""

from sqlalchemy import text

from alembic import op

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    b = op.get_bind()
    b.execute(
        text(
            "CREATE TABLE public.tool_http_connectors ("
            "    id UUID PRIMARY KEY,"
            "    tenant_id UUID NOT NULL,"
            "    key VARCHAR(64) NOT NULL,"
            "    name VARCHAR(128) NOT NULL,"
            "    description VARCHAR(512) NOT NULL DEFAULT '',"
            "    method VARCHAR(8) NOT NULL,"
            "    path_template VARCHAR(1024) NOT NULL,"
            "    params JSONB NOT NULL DEFAULT '[]'::jsonb,"
            "    allowed_hosts JSONB NOT NULL,"
            "    auth_type VARCHAR(16) NOT NULL,"
            "    auth_secret_enc TEXT,"
            "    requires_confirmation BOOLEAN NOT NULL DEFAULT FALSE,"
            "    timeout_ms INTEGER NOT NULL,"
            "    enabled VARCHAR(16) NOT NULL DEFAULT 'ENABLED',"
            "    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),"
            "    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),"
            "    created_by UUID NOT NULL,"
            "    CONSTRAINT ux_tool_http_connectors_tenant_key UNIQUE (tenant_id, key),"
            "    CONSTRAINT ck_tool_http_connectors_method"
            "        CHECK (method IN ('GET','POST')),"
            "    CONSTRAINT ck_tool_http_connectors_auth_type"
            "        CHECK (auth_type IN ('none','bearer','api_key','basic')),"
            "    CONSTRAINT ck_tool_http_connectors_enabled"
            "        CHECK (enabled IN ('ENABLED','DISABLED')),"
            "    CONSTRAINT ck_tool_http_connectors_timeout CHECK (timeout_ms > 0)"
            ")"
        )
    )
    # 注入集按请求租户装配的点查索引(租户内行数小,单列即可)
    b.execute(
        text(
            "CREATE INDEX ix_tool_http_connectors_tenant ON public.tool_http_connectors (tenant_id)"
        )
    )


def downgrade() -> None:
    b = op.get_bind()
    b.execute(text("DROP TABLE IF EXISTS public.tool_http_connectors"))
