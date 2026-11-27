"""退役 billing/portal 遗留权限点,避免角色继续持有不可用授权。"""

from sqlalchemy import text

from alembic import op

revision = "0073"
down_revision = "0072"
branch_labels = None
depends_on = None

UPGRADE = """
DELETE FROM public.sys_role_permission
WHERE permission_id IN (
    SELECT id FROM public.sys_permission
    WHERE perm_code IN ('developer:portal', 'system:billing:manage')
);
DELETE FROM public.sys_permission
WHERE perm_code IN ('developer:portal', 'system:billing:manage');
"""

DOWNGRADE = """
INSERT INTO public.sys_permission (id, perm_code, perm_name, sort)
VALUES
    (gen_random_uuid(), 'developer:portal', '开发者门户', 27),
    (gen_random_uuid(), 'system:billing:manage', '计费管理', 28)
ON CONFLICT (perm_code) DO NOTHING;

INSERT INTO public.sys_role_permission (role_id, permission_id)
SELECT r.id, p.id FROM public.sys_role r
JOIN public.sys_permission p
  ON p.perm_code IN ('developer:portal', 'system:billing:manage')
WHERE r.role_key = 'SUPER_ADMIN'
ON CONFLICT DO NOTHING;

INSERT INTO public.sys_role_permission (role_id, permission_id)
SELECT r.id, p.id FROM public.sys_role r
JOIN public.sys_permission p ON p.perm_code = 'developer:portal'
WHERE r.role_key = 'DEVELOPER'
ON CONFLICT DO NOTHING;
"""


def _statements(ddl: str) -> list[str]:
    return [s.strip() for s in ddl.split(";") if s.strip()]


def upgrade() -> None:
    for stmt in _statements(UPGRADE):
        op.get_bind().execute(text(stmt))


def downgrade() -> None:
    for stmt in _statements(DOWNGRADE):
        op.get_bind().execute(text(stmt))
