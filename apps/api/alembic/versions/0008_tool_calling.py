"""新增工具执行审计表和系统公告表，分别记录工具操作与公告发布幂等键。"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

DDL = """
-- ============ toolcall 域(public,019) ============
CREATE TABLE public.sys_tool_audit (
    id              UUID PRIMARY KEY,
    trace_id        VARCHAR(64) NOT NULL,              -- 与 sys_sql_audit 同宽贯穿
    user_id         UUID NOT NULL,                     -- 工具执行必带真实主体(无系统调用豁免)
    tenant_id       UUID NOT NULL,
    tool_name       VARCHAR(64) NOT NULL,
    args_digest     TEXT,                              -- 参数摘要(脱敏+截断)
    args_json       JSONB,                             -- 原始参数(仅 PENDING_CONFIRM 行:确认后重放用)
    status          VARCHAR(24) NOT NULL
                    CHECK (status IN ('EXECUTED', 'REJECTED', 'ERROR', 'RATE_LIMITED',
                                      'PENDING_CONFIRM', 'CONFIRM_REJECTED',
                                      'CONFIRM_EXPIRED')),
    error_reason    TEXT,
    duration_ms     INTEGER,
    datasource_key  VARCHAR(64),
    channel         VARCHAR(24) NOT NULL DEFAULT 'chat',  -- 025:渠道标记(chat/web_embed/api)
    idempotency_key VARCHAR(128),                      -- 写工具幂等键(sha256 派生)
    expires_at      TIMESTAMPTZ,                       -- 仅 PENDING_CONFIRM(确认窗口)
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_tool_audit_tenant_time ON public.sys_tool_audit (tenant_id, created_at DESC);
CREATE INDEX idx_tool_audit_tool_time ON public.sys_tool_audit (tool_name, created_at DESC);
CREATE INDEX idx_tool_audit_user_time ON public.sys_tool_audit (user_id, created_at DESC);
-- 按 trace_id 和 status 过滤待确认调用；每轮问答都会执行此查询。
CREATE INDEX idx_tool_audit_trace ON public.sys_tool_audit (trace_id)
    WHERE status = 'PENDING_CONFIRM';
CREATE INDEX idx_tool_audit_idem ON public.sys_tool_audit (idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE TABLE public.sys_announcement (
    id              UUID PRIMARY KEY,
    tenant_id       UUID NOT NULL,
    title           VARCHAR(200) NOT NULL,
    content         TEXT NOT NULL,
    publisher_id    UUID,
    idempotency_key VARCHAR(128) NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_announcement_tenant_idem UNIQUE (tenant_id, idempotency_key)
);
"""


def _statements(ddl: str) -> list[str]:
    """拆分前剥离 -- 行注释再按分号切(与 0001 同款;asyncpg 不接受单次多语句)。"""
    no_comments = "\n".join(line.split("--", 1)[0] for line in ddl.splitlines())
    return [s.strip() for s in no_comments.split(";") if s.strip()]


def upgrade() -> None:
    for stmt in _statements(DDL):
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.sys_announcement")
    op.execute("DROP TABLE IF EXISTS public.sys_tool_audit")
