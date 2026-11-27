"""扩展控制快照签名列以容纳 RSA-SHA256 签名。

X-01 快照签名是 RSA-PKCS1v15 的 base64 值(2048 位约 344 字符,4096 位约 684),
原 VARCHAR(128) 只够旧 HMAC 形态;改用 TEXT,避免签名密钥位数受列宽限制。
"""

from sqlalchemy import text

from alembic import op

revision = "0074"
down_revision = "0073"
branch_labels = None
depends_on = None

UPGRADE = """
ALTER TABLE control.snapshot
    ALTER COLUMN signature TYPE TEXT;
"""

DOWNGRADE = """
ALTER TABLE control.snapshot
    ALTER COLUMN signature TYPE VARCHAR(128);
"""


def _statements(ddl: str) -> list[str]:
    return [s.strip() for s in ddl.split(";") if s.strip()]


def upgrade() -> None:
    for stmt in _statements(UPGRADE):
        op.get_bind().execute(text(stmt))


def downgrade() -> None:
    for stmt in _statements(DOWNGRADE):
        op.get_bind().execute(text(stmt))
