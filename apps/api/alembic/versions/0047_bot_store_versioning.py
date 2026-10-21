"""新增 Bot 不可变上架版本快照及当前版本指针，并为已上架 Bot 回填首个快照。降级会删除指针和版本历史。"""

import json

from sqlalchemy import text

from alembic import op
from kongming.uuid7 import uuid7

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None

# 快照白名单键集必须与 kongming.store.snapshot.build_snapshot 保持一致。
DDL = """
CREATE TABLE public.sys_bot_version (
    id           UUID PRIMARY KEY,
    tenant_id    UUID NOT NULL,
    bot_id       UUID NOT NULL REFERENCES public.sys_bot_definition(id) ON DELETE CASCADE,
    version_no   INTEGER NOT NULL,
    snapshot     JSONB NOT NULL,
    published_by UUID NOT NULL,
    published_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_sys_bot_version_bot_no UNIQUE (bot_id, version_no),
    CONSTRAINT ck_sys_bot_version_no CHECK (version_no >= 1)
);
CREATE INDEX idx_sys_bot_version_bot
    ON public.sys_bot_version (bot_id, version_no DESC);

ALTER TABLE public.sys_bot_definition
    ADD COLUMN current_version_id UUID REFERENCES public.sys_bot_version(id);
"""

# 存量在架 Bot 补 v1 快照(=迁移时定义白名单字段)并指向;
# 未上架存量(published_at IS NULL)不补。published_by 兜底 created_by
# (历史行 published_by 理应有值,兜底只为 NOT NULL 约束不因脏数据断迁移)。
# 主键由 python 侧 uuid7 逐行生成(ADR-004 主键版本口径 v7 单源,与应用
# 写入路径同构;PG 无内置 v7 生成,gen_random_uuid 的 v4 形态不采用)。
_BACKFILL_SELECT = """
SELECT b.id AS bot_id, b.tenant_id,
       jsonb_build_object(
           'name', b.name,
           'greeting', b.greeting,
           'suggestions', b.suggestions,
           'system_prompt', b.system_prompt,
           'tools', b.tools
       ) AS snapshot,
       COALESCE(b.published_by, b.created_by) AS published_by,
       b.published_at
FROM public.sys_bot_definition b
WHERE b.published_at IS NOT NULL
"""

_BACKFILL_INSERT = """
INSERT INTO public.sys_bot_version
    (id, tenant_id, bot_id, version_no, snapshot, published_by, published_at)
VALUES (CAST(:id AS uuid), CAST(:t AS uuid), CAST(:b AS uuid), 1,
        CAST(:s AS jsonb), CAST(:u AS uuid), CAST(:p AS timestamptz))
"""

_POINT_CURRENT = """
UPDATE public.sys_bot_definition b
SET current_version_id = v.id
FROM public.sys_bot_version v
WHERE v.bot_id = b.id AND v.version_no = 1 AND b.published_at IS NOT NULL
"""


def _statements(ddl: str) -> list[str]:
    no_comments = "\n".join(line.split("--", 1)[0] for line in ddl.splitlines())
    return [s.strip() for s in no_comments.split(";") if s.strip()]


def upgrade() -> None:
    for stmt in _statements(DDL):
        op.execute(stmt)
    conn = op.get_bind()
    for row in conn.execute(text(_BACKFILL_SELECT)).mappings():
        conn.execute(
            text(_BACKFILL_INSERT),
            {
                "id": str(uuid7()),
                "t": str(row["tenant_id"]),
                "b": str(row["bot_id"]),
                "s": json.dumps(row["snapshot"]),
                "u": str(row["published_by"]),
                "p": row["published_at"],  # asyncpg 原生绑定 datetime(字符串会拒)
            },
        )
    op.execute(_POINT_CURRENT)


def downgrade() -> None:
    op.execute("ALTER TABLE public.sys_bot_definition DROP COLUMN IF EXISTS current_version_id")
    op.execute("DROP TABLE IF EXISTS public.sys_bot_version")
