"""user_identities provider 改为 KEYCLOAK(虎符退役)。

HUFU 行原位改名为 KEYCLOAK(保留用户绑定);已删除的社交 provider 行清理。
约束收敛为只允许 KEYCLOAK,代码侧不再产生其它值。
"""

from sqlalchemy import text

from alembic import op

revision = "0067"
down_revision = "0066"
branch_labels = None
depends_on = None

UPGRADE = """
UPDATE public.user_identities SET provider = 'KEYCLOAK' WHERE provider = 'HUFU';
DELETE FROM public.user_identities
WHERE provider IN ('GITHUB', 'GOOGLE', 'WECOM', 'DINGTALK', 'FEISHU');
ALTER TABLE public.user_identities
    DROP CONSTRAINT IF EXISTS user_identities_provider_check;
ALTER TABLE public.user_identities
    ADD CONSTRAINT user_identities_provider_check CHECK (provider IN ('KEYCLOAK'));
"""

DOWNGRADE = """
UPDATE public.user_identities SET provider = 'HUFU' WHERE provider = 'KEYCLOAK';
ALTER TABLE public.user_identities
    DROP CONSTRAINT IF EXISTS user_identities_provider_check;
ALTER TABLE public.user_identities
    ADD CONSTRAINT user_identities_provider_check
    CHECK (provider IN ('GITHUB', 'GOOGLE', 'WECOM', 'DINGTALK', 'FEISHU', 'HUFU'));
"""


def _statements(ddl: str) -> list[str]:
    return [s.strip() for s in ddl.split(";") if s.strip()]


def upgrade() -> None:
    for stmt in _statements(UPGRADE):
        op.get_bind().execute(text(stmt))


def downgrade() -> None:
    for stmt in _statements(DOWNGRADE):
        op.get_bind().execute(text(stmt))
