"""控制面消费者私有存储:last-known 快照、命令回执、用量 outbox、注册状态。

服务 schema 是私有实现,不是跨仓契约;降级整体删除 control schema。
"""

from sqlalchemy import text

from alembic import op

revision = "0070"
down_revision = "0069"
branch_labels = None
depends_on = None

UPGRADE = """
CREATE SCHEMA IF NOT EXISTS control;

CREATE TABLE control.snapshot (
    service_id  VARCHAR(64) PRIMARY KEY,
    version     BIGINT      NOT NULL,
    etag        VARCHAR(128) NOT NULL DEFAULT '',
    observed_at TIMESTAMPTZ NOT NULL,
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    sections    JSONB       NOT NULL,
    signature   VARCHAR(128) NOT NULL
);

CREATE TABLE control.command_receipt (
    idempotency_key VARCHAR(200) PRIMARY KEY,
    command_id      VARCHAR(200) NOT NULL,
    action          VARCHAR(64)  NOT NULL,
    action_version  INTEGER      NOT NULL,
    source          VARCHAR(16)  NOT NULL
                    CHECK (source IN ('control', 'local')),
    status          VARCHAR(16)  NOT NULL
                    CHECK (status IN ('EXECUTING', 'SUCCEEDED', 'FAILED', 'REJECTED')),
    result          JSONB        NOT NULL DEFAULT '{}'::jsonb,
    actor           VARCHAR(200) NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX ix_control_command_receipt_time
    ON control.command_receipt (updated_at DESC);

CREATE TABLE control.usage_outbox (
    event_id        VARCHAR(64) PRIMARY KEY,
    event_type      VARCHAR(64) NOT NULL,
    occurred_at     TIMESTAMPTZ NOT NULL,
    payload         JSONB       NOT NULL,
    attempts        INTEGER     NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    delivered_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_control_usage_outbox_pending
    ON control.usage_outbox (occurred_at)
    WHERE delivered_at IS NULL;

CREATE TABLE control.registration (
    service_id         VARCHAR(64)  NOT NULL,
    instance_id        VARCHAR(128) NOT NULL,
    version            VARCHAR(32)  NOT NULL,
    capabilities       JSONB        NOT NULL DEFAULT '[]'::jsonb,
    last_attempt_at    TIMESTAMPTZ,
    last_registered_at TIMESTAMPTZ,
    last_heartbeat_at  TIMESTAMPTZ,
    last_error         VARCHAR(300),
    PRIMARY KEY (service_id, instance_id)
);
"""

DOWNGRADE = """
DROP SCHEMA IF EXISTS control CASCADE;
"""


def _statements(ddl: str) -> list[str]:
    return [s.strip() for s in ddl.split(";") if s.strip()]


def upgrade() -> None:
    for stmt in _statements(UPGRADE):
        op.get_bind().execute(text(stmt))


def downgrade() -> None:
    for stmt in _statements(DOWNGRADE):
        op.get_bind().execute(text(stmt))
