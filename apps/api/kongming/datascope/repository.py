"""datascope 仓储:角色 scope / 用户部门 / CUSTOM 部门集合 / 递归部门树 / 业务库列探测。

控制面查询(sys_role/sys_user/sys_dept)走控制 session;
列存在性探测走业务库连接(执行目标库,默认路径即本库,动态池插槽预留)。
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession


class SqlDataScopeRepo:
    def __init__(self, s: AsyncSession) -> None:
        self._s = s

    async def user_role_scopes(self, user_id: str) -> list[str]:
        """用户全部启用角色的 data_scope 集(调用方负责收敛最宽松)。"""
        rows = (
            (
                await self._s.execute(
                    text(
                        "SELECT r.data_scope FROM public.sys_role r"
                        " JOIN public.sys_user_role ur ON ur.role_id = r.id"
                        " WHERE ur.user_id = :u AND r.status = 'ACTIVE'"
                    ),
                    {"u": user_id},
                )
            )
            .scalars()
            .all()
        )
        return [str(x) for x in rows]

    async def user_dept_id(self, user_id: str) -> tuple[bool, str | None]:
        """(用户存在且未软删, dept_id);dept_id=None 即未绑定部门。"""
        row = (
            (
                await self._s.execute(
                    text(
                        "SELECT dept_id FROM public.sys_user WHERE id = :u AND deleted_at IS NULL"
                    ),
                    {"u": user_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return False, None
        return True, (None if row["dept_id"] is None else str(row["dept_id"]))

    async def custom_dept_ids(self, user_id: str) -> list[str]:
        """该用户全部启用角色中 data_scope=CUSTOM 的部门集合(取并集,去重排序)。

        与 user_role_scopes 同一 ACTIVE 闸:禁用角色的集合不兑现。并集而非交集
        对齐 rbac「权限点取并集 + data_scope 取最宽松」的授权语义——授予即兑现。
        集合本身不递归子部门(递归是 DEPT_AND_BELOW 的语义,两者职责不混)。
        """
        rows = (
            (
                await self._s.execute(
                    text(
                        "SELECT DISTINCT rd.dept_id FROM public.sys_role_dept rd"
                        " JOIN public.sys_role r ON r.id = rd.role_id"
                        " JOIN public.sys_user_role ur ON ur.role_id = r.id"
                        " WHERE ur.user_id = :u AND r.status = 'ACTIVE'"
                        " AND r.data_scope = 'CUSTOM'"
                        " ORDER BY rd.dept_id"
                    ),
                    {"u": user_id},
                )
            )
            .scalars()
            .all()
        )
        return [str(x) for x in rows]

    async def dept_and_below(self, dept_id: str) -> list[str]:
        """递归查询指定部门及其全部子部门(含自身);停用部门不排除。
        UNION 去重,保证异常循环关系可终止。"""
        rows = (
            (
                await self._s.execute(
                    text(
                        "WITH RECURSIVE sub AS ("
                        "  SELECT id FROM public.sys_dept WHERE id = :d"
                        "  UNION"
                        "  SELECT d.id FROM public.sys_dept d"
                        "  JOIN sub s ON d.parent_id = s.id"
                        ") SELECT id FROM sub"
                    ),
                    {"d": dept_id},
                )
            )
            .scalars()
            .all()
        )
        return [str(x) for x in rows]


async def probe_column_type(
    conn: AsyncConnection, *, schemas: list[str], table: str, column: str
) -> str | None:
    """业务库列探测:命中返回 data_type(如 uuid/text),未命中 None。

    schema 双限定(tenant_<key> + public)——修跨 schema 同名表误判(上游)。
    """
    row = (
        (
            await conn.execute(
                text(
                    "SELECT data_type FROM information_schema.columns"
                    " WHERE table_schema = ANY(CAST(:schemas AS text[]))"
                    " AND table_name = :t AND column_name = :c LIMIT 1"
                ),
                {"schemas": schemas, "t": table, "c": column},
            )
        )
        .mappings()
        .one_or_none()
    )
    return None if row is None else str(row["data_type"])
