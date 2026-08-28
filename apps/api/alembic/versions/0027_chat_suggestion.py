"""0027: 对话冷启动建议问题缓存(039)

public.chat_suggestion 新表——按租户+数据源维度缓存 LLM 生成的建议问句
(空态 chips 一次生成永久复用)。
UNIQUE(tenant_id, datasource_key) 即查询路径(命中/落库幂等/级联删除均走它);
不设数据源外键:default 内置源无 sys_datasource 行,失效走 SuggestionCacheEvictor
端口级联(与 023 语义层同款)。downgrade 删表。
"""

from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None

DML = """
CREATE TABLE public.chat_suggestion (
    id             UUID PRIMARY KEY,
    tenant_id      UUID NOT NULL REFERENCES public.sys_tenant(id),
    datasource_key VARCHAR(64) NOT NULL,
    questions      JSONB NOT NULL,                     -- string[] 建议问句清单
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_chat_suggestion UNIQUE (tenant_id, datasource_key)
);
"""

DOWN_DML = """
DROP TABLE IF EXISTS public.chat_suggestion;
"""


def upgrade() -> None:
    for stmt in [s.strip() for s in DML.split(";") if s.strip()]:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in [s.strip() for s in DOWN_DML.split(";") if s.strip()]:
        op.execute(stmt)
