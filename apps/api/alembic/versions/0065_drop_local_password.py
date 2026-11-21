"""去除本地密码与本地社交身份(ADR-0010:账户归虎符)。

sys_user 仍以 id 作为主体键,值改为虎符 sub;本迁移只摘掉密码列与密码生命周期列,
不动任何授权外键。既有行(旧本地 admin/五家社交 provider 建的影子账号)无法再登录,
保留是为了不破坏按 user_id 关联的历史审计与资产。
"""

from sqlalchemy import text

from alembic import op

revision = "0065"
down_revision = "0064"
branch_labels = None
depends_on = None

UPGRADE = """
ALTER TABLE public.sys_user
    DROP COLUMN IF EXISTS password_hash,
    DROP COLUMN IF EXISTS password_changed_at;
"""

DOWNGRADE = """
ALTER TABLE public.sys_user
    ADD COLUMN IF NOT EXISTS password_hash VARCHAR(200),
    ADD COLUMN IF NOT EXISTS password_changed_at TIMESTAMPTZ;
"""


def _statements(ddl: str) -> list[str]:
    return [s.strip() for s in ddl.split(";") if s.strip()]


def upgrade() -> None:
    for stmt in _statements(UPGRADE):
        op.get_bind().execute(text(stmt))


def downgrade() -> None:
    # 密码列可重加但内容不可恢复:降级后所有账号仍需在虎符侧重新设密或在孔明侧重置。
    for stmt in _statements(DOWNGRADE):
        op.get_bind().execute(text(stmt))
