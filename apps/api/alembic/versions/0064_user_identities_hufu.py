"""user_identities 放开 HUFU provider(只增约束,不改历史行)。"""

from sqlalchemy import text

from alembic import op

revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None

UPGRADE = """
ALTER TABLE public.user_identities
    DROP CONSTRAINT IF EXISTS user_identities_provider_check;
ALTER TABLE public.user_identities
    ADD CONSTRAINT user_identities_provider_check
    CHECK (provider IN ('GITHUB', 'GOOGLE', 'WECOM', 'DINGTALK', 'FEISHU', 'HUFU'));
"""

DOWNGRADE = """
ALTER TABLE public.user_identities
    DROP CONSTRAINT IF EXISTS user_identities_provider_check;
ALTER TABLE public.user_identities
    ADD CONSTRAINT user_identities_provider_check
    CHECK (provider IN ('GITHUB', 'GOOGLE', 'WECOM', 'DINGTALK', 'FEISHU'));
"""


def _statements(ddl: str) -> list[str]:
    return [s.strip() for s in ddl.split(";") if s.strip()]


def upgrade() -> None:
    for stmt in _statements(UPGRADE):
        op.get_bind().execute(text(stmt))


def downgrade() -> None:
    # 降级会收紧 CHECK:存在 HUFU 绑定时拒绝自动删除,避免静默丢失身份映射。
    bind = op.get_bind()
    if bind.execute(
        text("SELECT 1 FROM public.user_identities WHERE provider='HUFU' LIMIT 1")
    ).first():
        raise RuntimeError("存在 HUFU 身份绑定,拒绝自动降级;请先显式清理或迁移")
    for stmt in _statements(DOWNGRADE):
        bind.execute(text(stmt))
