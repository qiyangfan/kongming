"""0002: user_identities 多身份表(ADR-006 连接器架构)

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-17

- 新增 public.user_identities(user_id, provider, external_id):一用户可绑多路联邦;
  provider CHECK 五家一次建全(ADR-004:已规划能力的列/约束一次建全;国内三家 003 接入)。
- sys_user(auth_source, external_id) 数据平移后删列:身份绑定单一事实源收敛到
  user_identities;「是否可账密登录」由 password_hash 是否为空承载。
"""

from sqlalchemy import text

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

DDL = """
CREATE TABLE public.user_identities (
    id          UUID PRIMARY KEY,
    user_id     UUID NOT NULL REFERENCES public.sys_user(id),
    provider    VARCHAR(16) NOT NULL
                CHECK (provider IN ('GITHUB', 'GOOGLE', 'WECOM', 'DINGTALK', 'FEISHU')),
    external_id VARCHAR(128) NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (provider, external_id),
    UNIQUE (user_id, provider)
);
CREATE INDEX idx_user_identities_user ON public.user_identities (user_id);

-- 数据平移:旧列非空即联邦账号(生成 UUID 用 pgcrypto,baseline 已建扩展)
INSERT INTO public.user_identities (id, user_id, provider, external_id)
SELECT gen_random_uuid(), id, auth_source, external_id
FROM public.sys_user
WHERE external_id IS NOT NULL;

ALTER TABLE public.sys_user DROP COLUMN auth_source, DROP COLUMN external_id;
"""

DOWNGRADE_DDL = """
-- CHECK 按 0002 后的五家现实放开(而非恢复 0001 的 ('LOCAL','GITHUB')):
-- 存在 Google/国内家身份数据时,原样恢复会让 UPDATE 违反约束、回滚必失败
ALTER TABLE public.sys_user
    ADD COLUMN auth_source VARCHAR(16) NOT NULL DEFAULT 'LOCAL'
    CHECK (auth_source IN ('LOCAL', 'GITHUB', 'GOOGLE', 'WECOM', 'DINGTALK', 'FEISHU')),
    ADD COLUMN external_id VARCHAR(128);

-- 回迁:一用户多身份时取最早绑定的一条(旧模型单身份,尽力而为)
UPDATE public.sys_user u
SET auth_source = i.provider, external_id = i.external_id
FROM public.user_identities i
WHERE i.user_id = u.id
  AND i.created_at = (SELECT min(created_at) FROM public.user_identities WHERE user_id = u.id);

ALTER TABLE public.sys_user ADD CONSTRAINT sys_user_auth_source_external_id_key
    UNIQUE (auth_source, external_id);

DROP TABLE public.user_identities;
"""


def _statements(ddl: str) -> list[str]:
    no_comments = "\n".join(line.split("--", 1)[0] for line in ddl.splitlines())
    return [s.strip() for s in no_comments.split(";") if s.strip()]


def upgrade() -> None:
    for stmt in _statements(DDL):
        op.get_bind().execute(text(stmt))


def downgrade() -> None:
    for stmt in _statements(DOWNGRADE_DDL):
        op.get_bind().execute(text(stmt))
