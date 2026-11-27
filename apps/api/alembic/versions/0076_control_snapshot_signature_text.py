"""快照签名列前向修正为 TEXT。

部分已存在的库在 0074 落地时列宽仍为 VARCHAR(512)；该宽度只够 2048 位 RSA，
4096 位密钥的 base64 签名会被截断。此修订对已是 TEXT 的库幂等，对旧库前向修正。
"""

from sqlalchemy import text

from alembic import op

revision = "0076"
down_revision = "0075"
branch_labels = None
depends_on = None

UPGRADE = """
ALTER TABLE control.snapshot
    ALTER COLUMN signature TYPE TEXT;
"""

DOWNGRADE = """
ALTER TABLE control.snapshot
    ALTER COLUMN signature TYPE VARCHAR(512);
"""


def _statements(ddl: str) -> list[str]:
    return [s.strip() for s in ddl.split(";") if s.strip()]


def upgrade() -> None:
    for stmt in _statements(UPGRADE):
        op.get_bind().execute(text(stmt))


def downgrade() -> None:
    for stmt in _statements(DOWNGRADE):
        op.get_bind().execute(text(stmt))
