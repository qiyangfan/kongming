"""0057: 退役 ticket 权限点(remove-ticket-domain)

ticket 工具域退役后,`tool:ticket:search` 与 `tool:ticket:manage` 两个权限点
失去行为主体。启动种子只增不删,仅删代码种子不能消除数据库里的失效授权
入口,故以精确范围数据迁移清理:先删引用这两个权限的 sys_role_permission
绑定,再删 sys_permission 行。不触碰任何其他权限/角色绑定,不扫描
sys_tool_audit(历史工单审计记录保留)。

**downgrade**:仅还原两个权限行(目录完整性),角色绑定不还原——设计明示
「只回滚应用不能找回已删除的绑定」,完整恢复须用迁移前数据库备份。
"""

from sqlalchemy import text

from alembic import op

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None

_RETIRED_CODES = ("tool:ticket:search", "tool:ticket:manage")

# 还原行与种子同口径(sort 值随 100 域登记;名称同 seed.py 退役前声明)
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
