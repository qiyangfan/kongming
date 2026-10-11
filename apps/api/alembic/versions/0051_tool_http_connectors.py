"""0051: 租户级 HTTP 连接器(099-http-connector-domain)

纯增一表(design D7 全列清单,ADR-004 口径):
- tool_http_connectors:租户自助注册的 HTTP 端点工具声明行——UUID v7 主键
  (应用侧生成器,列不带服务端缺省生成)、同租户 key 唯一、method/auth_type/
  enabled 均为 VARCHAR+CHECK(禁 SMALLINT 魔数/PG ENUM)、params/allowed_hosts
  JSONB、鉴权材料加密列 auth_secret_enc(TEXT NULLABLE,none 形态为 NULL;
  加密复用 webhook secret 同一 AES-GCM 单点,任何接口不回显明文)、
  requires_confirmation 写语义声明、timeout_ms 注册声明超时、timestamptz
  审计时间戳、created_by 创建人。无软删(硬删,identity 域外)。
**downgrade**:直接 DROP 表(纯增独立,无交叉依赖)。
"""

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
