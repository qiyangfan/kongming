"""删除已退役 ticket 工具对应的两个权限点及其角色绑定，不影响其他授权或历史审计。降级只恢复权限定义，不恢复角色绑定。"""

from sqlalchemy import text

from alembic import op

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None

_RETIRED_CODES = ("tool:ticket:search", "tool:ticket:manage")

# 还原行与种子同口径(sort 值随 域登记;名称同 seed.py 退役前声明)
_RESTORE_ROWS = (
    ("tool:ticket:search", "工具-工单检索", 40),
    ("tool:ticket:manage", "工具-工单管理", 41),
)


def upgrade() -> None:
    op.get_bind().execute(
        text(
            "DELETE FROM public.sys_role_permission"
            " WHERE permission_id IN ("
            "   SELECT id FROM public.sys_permission"
            "    WHERE perm_code IN ('tool:ticket:search', 'tool:ticket:manage'))"
        )
    )
    op.get_bind().execute(
        text(
            "DELETE FROM public.sys_permission"
            " WHERE perm_code IN ('tool:ticket:search', 'tool:ticket:manage')"
        )
    )


def downgrade() -> None:
    for code, name, sort in _RESTORE_ROWS:
        op.get_bind().execute(
            text(
                "INSERT INTO public.sys_permission (id, perm_code, perm_name, sort)"
                " VALUES (gen_random_uuid(), :c, :n, :s) ON CONFLICT (perm_code) DO NOTHING"
            ),
            {"c": code, "n": name, "s": sort},
        )
