"""baseline: 核心域一次性整合建全

Revision ID: 0001
Revises:
Create Date: 2026-08-16

整合前系统 50 个 Flyway 迁移的最终形态,消除全部补丁链(ADR-004):
- sys_user        ← V1 + V6(dept_id 弃) + V18(external_id/source/email 一次建全)
- table_descriptions ← V3 + V13(序列弃,UUID) + V16(source_type/tsv) + V20(datasource_key,约束直接建对)
- chat_message    ← V19 + V23(反馈4列) + V25(状态机4列) + V43(artifacts)
- sys_sql_audit   ← 含 datasource_key/trace_id
约定:UUID v7 主键 / 状态 VARCHAR+CHECK / created_at+updated_at / 软删仅 identity 域。
"""

from sqlalchemy import text

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

DDL = """
-- ============ 扩展 ============
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE SCHEMA IF NOT EXISTS rag;
-- rag schema 授权(角色由 pg-init 创建;schema 在此处才存在,故授权必须在此)
GRANT USAGE ON SCHEMA rag TO km_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA rag GRANT SELECT ON TABLES TO km_readonly;

-- ============ identity 域(public) ============
CREATE TABLE public.sys_user (
    id                 UUID PRIMARY KEY,
    username           VARCHAR(64)  NOT NULL UNIQUE,
    email              VARCHAR(128),
    password_hash      VARCHAR(200),                          -- argon2id;外部联邦源可为 NULL
    nickname           VARCHAR(64)  NOT NULL DEFAULT '',
    auth_source        VARCHAR(16)  NOT NULL DEFAULT 'LOCAL'
                       CHECK (auth_source IN ('LOCAL', 'GITHUB')),
    external_id        VARCHAR(128),                          -- 联邦身份标识(auth_source+external_id 唯一)
    status             VARCHAR(16)  NOT NULL DEFAULT 'ACTIVE'
                       CHECK (status IN ('ACTIVE', 'DISABLED')),
    password_changed_at TIMESTAMPTZ,
    last_login_at      TIMESTAMPTZ,
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ  NOT NULL DEFAULT now(),
    deleted_at         TIMESTAMPTZ,
    UNIQUE (auth_source, external_id)
);

CREATE TABLE public.sys_tenant (
    id          UUID PRIMARY KEY,
    tenant_key  VARCHAR(64)  NOT NULL UNIQUE,                 -- 如 sakila / olist
    tenant_name VARCHAR(100) NOT NULL,
    status      VARCHAR(16)  NOT NULL DEFAULT 'ACTIVE'
                CHECK (status IN ('ACTIVE', 'DISABLED')),
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE public.sys_user_tenant (
    user_id    UUID NOT NULL REFERENCES public.sys_user(id),
    tenant_id  UUID NOT NULL REFERENCES public.sys_tenant(id),
    granted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, tenant_id)
);

CREATE TABLE public.sys_role (
    id         UUID PRIMARY KEY,
    role_key   VARCHAR(100) NOT NULL UNIQUE,                  -- SUPER_ADMIN / DEVELOPER / ...
    role_name  VARCHAR(100) NOT NULL,
    data_scope VARCHAR(16)  NOT NULL DEFAULT 'SELF'
               CHECK (data_scope IN ('ALL', 'CUSTOM', 'DEPT', 'DEPT_AND_BELOW', 'SELF')),
    sql_scope  VARCHAR(16)  NOT NULL DEFAULT 'SELECT_ONLY'
               CHECK (sql_scope IN ('SELECT_ONLY', 'READ_DDL', 'DML_DDL')),
    sort       INTEGER      NOT NULL DEFAULT 0,
    status     VARCHAR(16)  NOT NULL DEFAULT 'ACTIVE'
               CHECK (status IN ('ACTIVE', 'DISABLED')),
    created_at TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE public.sys_permission (
    id         UUID PRIMARY KEY,
    parent_id  UUID REFERENCES public.sys_permission(id),
    perm_code  VARCHAR(100) NOT NULL UNIQUE,                  -- 如 chat:execute / system:user:manage
    perm_name  VARCHAR(100) NOT NULL,
    perm_type  VARCHAR(16)  NOT NULL DEFAULT 'API'
               CHECK (perm_type IN ('MENU', 'BUTTON', 'API')),
    sort       INTEGER      NOT NULL DEFAULT 0
);

CREATE TABLE public.sys_user_role (
    user_id UUID NOT NULL REFERENCES public.sys_user(id),
    role_id UUID NOT NULL REFERENCES public.sys_role(id),
    PRIMARY KEY (user_id, role_id)
);

CREATE TABLE public.sys_role_permission (
    role_id       UUID NOT NULL REFERENCES public.sys_role(id),
    permission_id UUID NOT NULL REFERENCES public.sys_permission(id),
    PRIMARY KEY (role_id, permission_id)
);

-- 动态数据源注册(密码密文,APP_ENCRYPT_KEY 加密;整合 V20 最终态)
CREATE TABLE public.sys_datasource (
    id             UUID PRIMARY KEY,
    datasource_key VARCHAR(64) NOT NULL UNIQUE,               -- 'default' / 'sakila' / 外部库
    tenant_id      UUID NOT NULL REFERENCES public.sys_tenant(id),
    host           VARCHAR(255) NOT NULL,
    port           INTEGER      NOT NULL DEFAULT 5432,
    database       VARCHAR(128) NOT NULL,
    username       VARCHAR(128) NOT NULL,                     -- 只读账号
    password_enc   TEXT         NOT NULL,                     -- AES-GCM 密文
    readonly       BOOLEAN      NOT NULL DEFAULT TRUE,
    status         VARCHAR(16)  NOT NULL DEFAULT 'ACTIVE'
                   CHECK (status IN ('ACTIVE', 'DISABLED')),
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- ============ rag 知识域 ============
-- 整合 V3+V13+V16+V20 最终态:source_type / tsv / datasource_key 一次建全,唯一约束直接建对
CREATE TABLE rag.table_descriptions (
    id             UUID PRIMARY KEY,
    tenant_id      UUID NOT NULL REFERENCES public.sys_tenant(id),
    datasource_key VARCHAR(64) NOT NULL DEFAULT 'default',
    schema_name    VARCHAR(64)  NOT NULL,
    table_name     VARCHAR(128) NOT NULL,
    table_comment  TEXT,
    columns_comment JSONB,
    sample_queries JSONB,                                     -- few-shot 内嵌
    source_type    VARCHAR(20) NOT NULL DEFAULT 'MARKDOWN'
                   CHECK (source_type IN ('MARKDOWN', 'AI_GENERATED', 'MANUAL_EDIT', 'FEEDBACK_REFINED')),
    embedding      vector(1536),
    tsv            tsvector,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, datasource_key, schema_name, table_name)
);
CREATE INDEX idx_table_desc_embedding ON rag.table_descriptions
    USING hnsw (embedding vector_cosine_ops);
CREATE INDEX idx_table_desc_tsv ON rag.table_descriptions USING gin (tsv);

-- 历史成功 SQL(语义召回 few-shot;整合 V21:datasource_key 内建)
CREATE TABLE rag.query_history (
    id             UUID PRIMARY KEY,
    tenant_id      UUID NOT NULL,
    datasource_key VARCHAR(64) NOT NULL DEFAULT 'default',
    question       TEXT NOT NULL,
    sql_text       TEXT NOT NULL,
    status         VARCHAR(16) NOT NULL DEFAULT 'SUCCESS'
                   CHECK (status IN ('SUCCESS', 'FAILED', 'REJECTED')),
    embedding      vector(1536),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_query_history_embedding ON rag.query_history
    USING hnsw (embedding vector_cosine_ops);

-- ============ chat 域(public) ============
CREATE TABLE public.chat_session (
    id             UUID PRIMARY KEY,
    user_id        UUID NOT NULL REFERENCES public.sys_user(id),
    tenant_id      UUID NOT NULL REFERENCES public.sys_tenant(id),
    datasource_key VARCHAR(64) NOT NULL DEFAULT 'default',
    title          VARCHAR(200) NOT NULL DEFAULT '新对话',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_chat_session_user ON public.chat_session (user_id, tenant_id, updated_at DESC);

-- 整合 V19+V23+V25+V43 最终态:结果、反馈、处置状态机、artifacts 一次建全
CREATE TABLE public.chat_message (
    id                   UUID PRIMARY KEY,
    session_id           UUID NOT NULL REFERENCES public.chat_session(id) ON DELETE CASCADE,
    role                 VARCHAR(16) NOT NULL CHECK (role IN ('USER', 'ASSISTANT')),
    question             TEXT,
    sql_text             TEXT,
    result_json          TEXT,                                -- columns/rows/insights/stats/chartType
    summary              TEXT,
    status               VARCHAR(16) NOT NULL DEFAULT 'SUCCESS'
                         CHECK (status IN ('SUCCESS', 'FAILED', 'REJECTED')),
    artifacts            JSONB,                               -- 沙箱产物/图表快照
    -- 反馈(V23)
    rating               VARCHAR(8)  CHECK (rating IN ('UP', 'DOWN')),
    feedback_comment     TEXT,
    corrected_sql        TEXT,
    feedback_time        TIMESTAMPTZ,
    -- 反馈处置状态机(V25)
    feedback_status      VARCHAR(16) NOT NULL DEFAULT 'NONE'
                         CHECK (feedback_status IN ('NONE', 'PENDING', 'ADOPTED', 'REJECTED', 'DISMISSED')),
    feedback_handler     UUID REFERENCES public.sys_user(id),
    feedback_handle_time TIMESTAMPTZ,
    feedback_handle_note TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_chat_message_session ON public.chat_message (session_id, created_at);
CREATE INDEX idx_chat_message_feedback ON public.chat_message (feedback_status)
    WHERE feedback_status IN ('PENDING', 'ADOPTED');

-- ============ audit 域(public) ============
CREATE TABLE public.sys_sql_audit (
    id             UUID PRIMARY KEY,
    trace_id       VARCHAR(64) NOT NULL,                      -- 贯穿 api→agent→执行
    user_id        UUID,
    tenant_id      UUID NOT NULL,
    datasource_key VARCHAR(64) NOT NULL DEFAULT 'default',
    question       TEXT,
    sql_text       TEXT NOT NULL,
    verdict        VARCHAR(16) NOT NULL
                   CHECK (verdict IN ('PASS', 'BLOCKED', 'ERROR')),
    block_reason   VARCHAR(200),
    row_count      INTEGER,
    duration_ms    INTEGER,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_sql_audit_user_time ON public.sys_sql_audit (user_id, created_at DESC);

CREATE TABLE public.sys_oper_log (
    id         UUID PRIMARY KEY,
    user_id    UUID,
    action     VARCHAR(64) NOT NULL,                          -- 如 datasource.create / knowledge.refine
    target     VARCHAR(128),
    detail     JSONB,
    ip         VARCHAR(50),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_oper_log_time ON public.sys_oper_log (created_at DESC);

-- ============ 种子:角色与超管 ============
-- 权限点/用户由应用 DataInit 在启动时幂等播种(与代码权限点同源),此处只锁角色骨架。
"""


def _statements(ddl: str) -> list[str]:
    """拆分前剥离 -- 行注释再按分号切(asyncpg 不接受单次多语句)。
    假设:语句字面量内无 '--' 与分号(本 DDL 成立)。"""
    no_comments = "\n".join(line.split("--", 1)[0] for line in ddl.splitlines())
    return [s.strip() for s in no_comments.split(";") if s.strip()]


def upgrade() -> None:
    for stmt in _statements(DDL):
        op.get_bind().execute(text(stmt))


def downgrade() -> None:
    for stmt in _statements(
        """
        DROP TABLE IF EXISTS public.sys_oper_log, public.sys_sql_audit,
                         public.chat_message, public.chat_session,
                         rag.query_history, rag.table_descriptions,
                         public.sys_datasource, public.sys_role_permission, public.sys_user_role,
                         public.sys_permission, public.sys_role, public.sys_user_tenant,
                         public.sys_tenant, public.sys_user CASCADE;
        DROP SCHEMA IF EXISTS rag;
        """
    ):
        op.get_bind().execute(text(stmt))
