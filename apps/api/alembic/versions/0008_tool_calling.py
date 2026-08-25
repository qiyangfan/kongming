"""0008: tool-calling 域两表(019)

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-22

sys_tool_audit:工具执行审计(六层防御 L5)——成功/拒绝/失败/限流/待确认
全量落;写工具确认的挂起态复用本表(status=PENDING_CONFIRM + expires_at +
idempotency_key),confirm_id 即行 uuid(天然防枚举,归属校验先决),免新表。
sys_announcement:系统公告(create_system_announcement 写工具落点),
(tenant_id, idempotency_key) 唯一=幂等防重复发布的物理闸。
手写迁移(与 0006/0007 同因:autogenerate 之外的手写 DDL 风格沿用 baseline)。
**downgrade**:删两表(新能力域零存量,回滚即整体移除)。
"""

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
-- R1-S5:pending 聚合按 trace_id+status 过滤,每轮 react 问答响应跑一次
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
