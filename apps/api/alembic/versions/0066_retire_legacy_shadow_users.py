"""退役旧本地/社交影子账号(ADR-0010)。

0065 只摘掉密码列,既有影子行仍可能保留 ACTIVE 与角色/租户绑定。单一账户下它们
不可登录也不应继续持有授权,因此软删并解绑;历史业务行仍按原 user_id 保留供审计。
"""

from sqlalchemy import text

from alembic import op

revision = "0066"
down_revision = "0065"
branch_labels = None
depends_on = None

UPGRADE = """
UPDATE public.sys_user u
SET deleted_at = now(), status = 'DISABLED'
WHERE u.deleted_at IS NULL
  AND NOT EXISTS (
    SELECT 1 FROM public.user_identities i
    WHERE i.user_id = u.id AND i.provider = 'HUFU'
  );

DELETE FROM public.sys_user_role ur
USING public.sys_user u
WHERE ur.user_id = u.id
  AND u.status = 'DISABLED'
  AND NOT EXISTS (
    SELECT 1 FROM public.user_identities i
    WHERE i.user_id = u.id AND i.provider = 'HUFU'
  );

DELETE FROM public.sys_user_tenant ut
USING public.sys_user u
WHERE ut.user_id = u.id
  AND u.status = 'DISABLED'
  AND NOT EXISTS (
    SELECT 1 FROM public.user_identities i
    WHERE i.user_id = u.id AND i.provider = 'HUFU'
  );
"""

DOWNGRADE = """
UPDATE public.sys_user SET deleted_at = NULL, status = 'ACTIVE'
WHERE username = 'admin';
"""


def _statements(ddl: str) -> list[str]:
    return [s.strip() for s in ddl.split(";") if s.strip()]


def upgrade() -> None:
    for stmt in _statements(UPGRADE):
        op.get_bind().execute(text(stmt))


def downgrade() -> None:
    for stmt in _statements(DOWNGRADE):
        op.get_bind().execute(text(stmt))
