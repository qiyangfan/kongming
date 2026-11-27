"""控制面身份应用版本与保留清理索引。

身份缓存按服务记录已应用的最高快照版本,多实例并发应用时只前进不后退;
delivered_at 索引供用量 outbox 保留清理使用。
"""

from sqlalchemy import text

from alembic import op

revision = "0072"
down_revision = "0071"
branch_labels = None
depends_on = None

UPGRADE = """
CREATE TABLE control.identity_state (
    service_id VARCHAR(64) PRIMARY KEY,
    version    BIGINT      NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_control_usage_outbox_delivered
    ON control.usage_outbox (delivered_at)
    WHERE delivered_at IS NOT NULL;
"""

DOWNGRADE = """
DROP INDEX IF EXISTS control.ix_control_usage_outbox_delivered;
DROP TABLE IF EXISTS control.identity_state;
"""


def _statements(ddl: str) -> list[str]:
    return [s.strip() for s in ddl.split(";") if s.strip()]


def upgrade() -> None:
    for stmt in _statements(UPGRADE):
        op.get_bind().execute(text(stmt))


def downgrade() -> None:
    for stmt in _statements(DOWNGRADE):
        op.get_bind().execute(text(stmt))
