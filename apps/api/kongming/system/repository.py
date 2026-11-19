"""system 持久化仓储,将 SQLAlchemy 查询映射到领域端口。

显式 SQL(text)同 auth 域约定:表少且稳定,SQL 即文档;
行 → 领域 dataclass 的映射集中在 _to_* 函数。

方法按能力簇拆入 mixin,SqlSystemRepo 只做组合;各 mixin 通过 _SystemRepoBase
共享会话与审计写入器。
"""

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.repository import SqlOperLogRepo
from kongming.sql_util import contains_like_pattern
from kongming.system.models import (
    Dept,
    PermItem,
    RoleListItem,
    TenantItem,
    UserListItem,
)

# 关键字对用户名/昵称不区分大小写包含匹配(rbac spec「用户列表查询」);
# ESCAPE '\' + contains_like_pattern 转义使 %/_/\\ 按字面匹配(搜索语义不失真)
_USER_FILTER = (
    "u.deleted_at IS NULL"
    " AND (:kw = '' OR u.username ILIKE :p ESCAPE '\\'"
    " OR u.nickname ILIKE :p ESCAPE '\\')"
)


def parse_json_agg(value: str | list[Any]) -> list[Any]:
    """json_agg 结果经 asyncpg 返回 str,归一化解析(其他驱动可能直接给 list)。"""
    if isinstance(value, str):
        return list(json.loads(value))
    return list(value)


def _to_user_item(row: dict[str, Any]) -> UserListItem:
    keys = parse_json_agg(row.get("role_keys") or [])
    return UserListItem(
        id=str(row["id"]),
        username=str(row["username"]),
        nickname=str(row["nickname"]),
        email=None if row["email"] is None else str(row["email"]),
        status=str(row["status"]),
        dept_id=None if row["dept_id"] is None else str(row["dept_id"]),
        dept_name=None if row["dept_name"] is None else str(row["dept_name"]),
        tenant_id=None if row["tenant_id"] is None else str(row["tenant_id"]),
        role_keys=[str(k) for k in keys],
        created_at=row["created_at"],
    )


def _to_role_item(row: dict[str, Any]) -> RoleListItem:
    perms = parse_json_agg(row.get("perm_ids") or [])
    depts = parse_json_agg(row.get("dept_ids") or [])
    return RoleListItem(
        id=str(row["id"]),
        role_key=str(row["role_key"]),
        role_name=str(row["role_name"]),
        data_scope=str(row["data_scope"]),
        sql_scope=str(row["sql_scope"]),
        sort=int(row["sort"]),
        builtin=str(row["role_key"]) in ("SUPER_ADMIN", "DEVELOPER"),
        perm_ids=[str(p) for p in perms],
        dept_ids=[str(d) for d in depts],
    )


def _to_perm(row: dict[str, Any]) -> PermItem:
    return PermItem(
        id=str(row["id"]),
        perm_code=str(row["perm_code"]),
        perm_name=str(row["perm_name"]),
        sort=int(row["sort"]),
    )


def _to_dept(row: dict[str, Any]) -> Dept:
    return Dept(
        id=str(row["id"]),
        parent_id=None if row["parent_id"] is None else str(row["parent_id"]),
        dept_name=str(row["dept_name"]),
        tenant_id=str(row["tenant_id"]),
        sort=int(row["sort"]),
        status=str(row["status"]),
    )


def _to_tenant(row: dict[str, Any]) -> TenantItem:
    return TenantItem(
        id=str(row["id"]),
        tenant_key=str(row["tenant_key"]),
        tenant_name=str(row["tenant_name"]),
    )


class _SystemRepoBase:
    """会话与审计写入器持有者,各能力 mixin 经它共享同一事务。"""

    def __init__(self, s: AsyncSession) -> None:
        self._s = s
        self._oper = SqlOperLogRepo(s)  # 审计与业务同会话=同事务(S2)


class _UserRepoMixin(_SystemRepoBase):
    """用户读写与角色/租户绑定。"""

    async def count_users(self, keyword: str) -> int:
        n = await self._s.scalar(
            text(
                f"SELECT count(*) FROM public.sys_user u WHERE {_USER_FILTER}"  # noqa: S608  # 常量模板,参数化绑定
            ),
            {"kw": keyword, "p": contains_like_pattern(keyword)},
        )
        return int(n or 0)

    async def list_users(self, keyword: str, limit: int, offset: int) -> list[UserListItem]:
        rows = (
            await self._s.execute(
                text(
                    "SELECT u.id, u.username, u.nickname, u.email, u.status, u.created_at,"  # noqa: S608
                    " u.dept_id, d.dept_name, tn.tenant_id,"
                    " coalesce(json_agg(DISTINCT r.role_key)"
                    "           FILTER (WHERE r.role_key IS NOT NULL), '[]') AS role_keys"
                    " FROM public.sys_user u"
                    " LEFT JOIN public.sys_dept d ON d.id = u.dept_id"
                    # 管理面单租户绑定形态:取绑定中 tenant_key 最小的一个作回显
                    " LEFT JOIN LATERAL ("
                    "   SELECT st.id AS tenant_id FROM public.sys_user_tenant ut"
                    "   JOIN public.sys_tenant st ON st.id = ut.tenant_id"
                    "   WHERE ut.user_id = u.id ORDER BY st.tenant_key LIMIT 1"
                    " ) tn ON TRUE"
                    " LEFT JOIN public.sys_user_role ur ON ur.user_id = u.id"
                    " LEFT JOIN public.sys_role r ON r.id = ur.role_id"
                    f" WHERE {_USER_FILTER}"
                    " GROUP BY u.id, d.dept_name, tn.tenant_id"
                    " ORDER BY u.created_at DESC, u.id DESC"
                    " LIMIT :lim OFFSET :off"
                ),
                {
                    "kw": keyword,
                    "p": contains_like_pattern(keyword),
                    "lim": limit,
                    "off": offset,
                },
            )
        ).mappings()
        return [_to_user_item(dict(r)) for r in rows]

    async def get_user(self, user_id: str) -> dict[str, Any] | None:
        row = (
            (
                await self._s.execute(
                    text(
                        "SELECT id, username, email, nickname, status, dept_id, password_hash"
                        " FROM public.sys_user WHERE id=:i AND deleted_at IS NULL"
                    ),
                    {"i": user_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        return dict(row) if row else None

    async def username_taken(self, username: str) -> bool:
        n = await self._s.scalar(
            text("SELECT count(*) FROM public.sys_user WHERE username=:u AND deleted_at IS NULL"),
            {"u": username},
        )
        return int(n or 0) > 0

    async def insert_user(
        self,
        *,
        user_id: str,
        username: str,
        email: str | None,
        password_hash: str | None,
        nickname: str,
        status: str,
        dept_id: str | None,
    ) -> None:
        await self._s.execute(
            text(
                "INSERT INTO public.sys_user"
                " (id, username, email, password_hash, nickname, status, dept_id)"
                " VALUES (:id, :u, :e, :ph, :n, :st, :d)"
            ),
            {
                "id": user_id,
                "u": username,
                "e": email,
                "ph": password_hash,
                "n": nickname,
                "st": status,
                "d": dept_id,
            },
        )

    async def update_user(
        self,
        user_id: str,
        *,
        email: str | None,
        nickname: str,
        status: str,
        dept_id: str | None,
        password_hash: str | None,
    ) -> None:
        # 全量字段覆盖(表单全量语义);password_hash None=不改(COALESCE)
        await self._s.execute(
            text(
                "UPDATE public.sys_user SET"
                " email=:e, nickname=:n, status=:st, dept_id=:d,"
                " password_hash=coalesce(:ph, password_hash), updated_at=now()"
                " WHERE id=:i AND deleted_at IS NULL"
            ),
            {
                "e": email,
                "n": nickname,
                "st": status,
                "d": dept_id,
                "ph": password_hash,
                "i": user_id,
            },
        )

    async def reset_password(self, user_id: str, password_hash: str) -> None:
        """重置密码:首登改密标志同步复位(rbac spec「编辑与重置密码」)。"""
        await self._s.execute(
            text(
                "UPDATE public.sys_user SET password_hash=:ph, password_changed_at=NULL,"
                " updated_at=now() WHERE id=:i AND deleted_at IS NULL"
            ),
            {"ph": password_hash, "i": user_id},
        )

    async def set_user_status(self, user_id: str, status: str) -> None:
        """专用启停:只翻状态列,不触碰其余字段(与全量编辑区分)。"""
        await self._s.execute(
            text(
                "UPDATE public.sys_user SET status=:st, updated_at=now()"
                " WHERE id=:i AND deleted_at IS NULL"
            ),
            {"st": status, "i": user_id},
        )

    async def soft_delete_user(self, user_id: str) -> bool:
        r = await self._s.execute(
            text(
                "UPDATE public.sys_user SET deleted_at=now(), updated_at=now()"
                " WHERE id=:i AND deleted_at IS NULL RETURNING id"
            ),
            {"i": user_id},
        )
        return r.scalar_one_or_none() is not None

    async def rebind_user_roles(self, user_id: str, role_ids: list[str]) -> None:
        await self._s.execute(
            text("DELETE FROM public.sys_user_role WHERE user_id=:u"), {"u": user_id}
        )
        for rid in role_ids:
            await self._s.execute(
                text(
                    "INSERT INTO public.sys_user_role (user_id, role_id)"
                    " VALUES (:u, :r) ON CONFLICT DO NOTHING"
                ),
                {"u": user_id, "r": rid},
            )

    async def rebind_user_tenant(self, user_id: str, tenant_id: str) -> None:
        await self._s.execute(
            text("DELETE FROM public.sys_user_tenant WHERE user_id=:u"), {"u": user_id}
        )
        await self._s.execute(
            text(
                "INSERT INTO public.sys_user_tenant (user_id, tenant_id)"
                " VALUES (:u, :t) ON CONFLICT DO NOTHING"
            ),
            {"u": user_id, "t": tenant_id},
        )


class _RoleRepoMixin(_SystemRepoBase):
    """角色读写与权限点/部门集合绑定。"""

    async def roles_exist(self, role_ids: list[str]) -> list[str]:
        if not role_ids:
            return []
        rows = await self._s.execute(
            text("SELECT id FROM public.sys_role WHERE id = ANY(:ids)"),
            {"ids": role_ids},
        )
        return [str(r[0]) for r in rows]

    async def list_roles(self) -> list[RoleListItem]:
        # 两个一对多聚合各走 LATERAL(单查询,避免双 JOIN 的笛卡儿积把集合乘重复);
        # 聚合内 ORDER BY id:回显顺序确定(整包替换语义下顺序本无语义,但契约面要可预期)
        rows = (
            await self._s.execute(
                text(
                    "SELECT r.id, r.role_key, r.role_name, r.data_scope, r.sql_scope, r.sort,"
                    " coalesce(p.perm_ids, '[]') AS perm_ids,"
                    " coalesce(d.dept_ids, '[]') AS dept_ids"
                    " FROM public.sys_role r"
                    " LEFT JOIN LATERAL ("
                    "   SELECT json_agg(rp.permission_id ORDER BY rp.permission_id)"
                    "     AS perm_ids FROM public.sys_role_permission rp"
                    "   WHERE rp.role_id = r.id"
                    " ) p ON TRUE"
                    " LEFT JOIN LATERAL ("
                    "   SELECT json_agg(rd.dept_id ORDER BY rd.dept_id) AS dept_ids"
                    "   FROM public.sys_role_dept rd WHERE rd.role_id = r.id"
                    " ) d ON TRUE"
                    " ORDER BY r.sort NULLS LAST, r.role_key"
                )
            )
        ).mappings()
        return [_to_role_item(dict(r)) for r in rows]

    async def get_role(self, role_id: str) -> dict[str, Any] | None:
        row = (
            (
                await self._s.execute(
                    text(
                        "SELECT id, role_key, role_name, data_scope, sql_scope, sort"
                        " FROM public.sys_role WHERE id=:i"
                    ),
                    {"i": role_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        return dict(row) if row else None

    async def role_key_taken(self, role_key: str) -> bool:
        n = await self._s.scalar(
            text("SELECT count(*) FROM public.sys_role WHERE role_key=:k"), {"k": role_key}
        )
        return int(n or 0) > 0

    async def role_id_by_key(self, role_key: str) -> str | None:
        v = await self._s.scalar(
            text("SELECT id FROM public.sys_role WHERE role_key=:k"), {"k": role_key}
        )
        return None if v is None else str(v)

    async def insert_role(
        self,
        *,
        role_id: str,
        role_key: str,
        role_name: str,
        data_scope: str,
        sql_scope: str,
        sort: int,
    ) -> None:
        await self._s.execute(
            text(
                "INSERT INTO public.sys_role"
                " (id, role_key, role_name, data_scope, sql_scope, sort)"
                " VALUES (:i, :k, :n, :ds, :ss, :so)"
            ),
            {
                "i": role_id,
                "k": role_key,
                "n": role_name,
                "ds": data_scope,
                "ss": sql_scope,
                "so": sort,
            },
        )

    async def update_role(
        self,
        role_id: str,
        *,
        role_name: str,
        data_scope: str,
        sql_scope: str,
        sort: int,
    ) -> None:
        await self._s.execute(
            text(
                "UPDATE public.sys_role SET role_name=:n, data_scope=:ds, sql_scope=:ss,"
                " sort=:so WHERE id=:i"
            ),
            {"n": role_name, "ds": data_scope, "ss": sql_scope, "so": sort, "i": role_id},
        )

    async def delete_role(self, role_id: str) -> None:
        # 级联清理绑定(无 FK ON DELETE,显式三删;前置校验已挡「有用户绑定」)
        await self._s.execute(
            text("DELETE FROM public.sys_role_permission WHERE role_id=:i"), {"i": role_id}
        )
        await self._s.execute(
            text("DELETE FROM public.sys_role_dept WHERE role_id=:i"), {"i": role_id}
        )
        await self._s.execute(
            text("DELETE FROM public.sys_user_role WHERE role_id=:i"), {"i": role_id}
        )
        await self._s.execute(text("DELETE FROM public.sys_role WHERE id=:i"), {"i": role_id})

    async def role_has_active_users(self, role_id: str) -> bool:
        n = await self._s.scalar(
            text(
                "SELECT count(*) FROM public.sys_user_role ur"
                " JOIN public.sys_user u ON u.id = ur.user_id AND u.deleted_at IS NULL"
                " WHERE ur.role_id=:i"
            ),
            {"i": role_id},
        )
        return int(n or 0) > 0

    async def rebind_role_perms(self, role_id: str, perm_ids: list[str]) -> None:
        await self._s.execute(
            text("DELETE FROM public.sys_role_permission WHERE role_id=:i"), {"i": role_id}
        )
        for pid in perm_ids:
            await self._s.execute(
                text(
                    "INSERT INTO public.sys_role_permission (role_id, permission_id)"
                    " VALUES (:r, :p) ON CONFLICT DO NOTHING"
                ),
                {"r": role_id, "p": pid},
            )

    async def rebind_role_depts(self, role_id: str, dept_ids: list[str]) -> None:
        """CUSTOM 部门集合整包替换:先删后插(与 rebind_role_perms 同构,空集合=清空)。"""
        await self._s.execute(
            text("DELETE FROM public.sys_role_dept WHERE role_id=:i"), {"i": role_id}
        )
        for did in dept_ids:
            await self._s.execute(
                text(
                    "INSERT INTO public.sys_role_dept (role_id, dept_id)"
                    " VALUES (:r, :d) ON CONFLICT DO NOTHING"
                ),
                {"r": role_id, "d": did},
            )


class _PermRepoMixin(_SystemRepoBase):
    """权限点只读清单与存在性校验。"""

    async def perms_exist(self, perm_ids: list[str]) -> list[str]:
        if not perm_ids:
            return []
        rows = await self._s.execute(
            text("SELECT id FROM public.sys_permission WHERE id = ANY(:ids)"),
            {"ids": perm_ids},
        )
        return [str(r[0]) for r in rows]

    async def list_perms(self) -> list[PermItem]:
        rows = (
            await self._s.execute(
                text(
                    "SELECT id, perm_code, perm_name, sort FROM public.sys_permission"
                    " ORDER BY sort, perm_code"
                )
            )
        ).mappings()
        return [_to_perm(dict(r)) for r in rows]


class _DeptRepoMixin(_SystemRepoBase):
    """部门读写与引用检查。"""

    async def dept_exists(self, dept_id: str) -> bool:
        n = await self._s.scalar(
            text("SELECT count(*) FROM public.sys_dept WHERE id=:i"), {"i": dept_id}
        )
        return int(n or 0) > 0

    async def depts_exist(self, dept_ids: list[str]) -> list[str]:
        if not dept_ids:
            return []
        rows = await self._s.execute(
            text("SELECT id FROM public.sys_dept WHERE id = ANY(:ids)"),
            {"ids": dept_ids},
        )
        return [str(r[0]) for r in rows]

    async def list_depts(self) -> list[Dept]:
        rows = (
            await self._s.execute(
                text(
                    "SELECT id, parent_id, dept_name, tenant_id, sort, status"
                    " FROM public.sys_dept ORDER BY sort, dept_name, id"
                )
            )
        ).mappings()
        return [_to_dept(dict(r)) for r in rows]

    async def get_dept(self, dept_id: str) -> Dept | None:
        row = (
            (
                await self._s.execute(
                    text(
                        "SELECT id, parent_id, dept_name, tenant_id, sort, status"
                        " FROM public.sys_dept WHERE id=:i"
                    ),
                    {"i": dept_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        return _to_dept(dict(row)) if row else None

    async def insert_dept(
        self,
        *,
        dept_id: str,
        parent_id: str | None,
        dept_name: str,
        tenant_id: str,
        sort: int,
    ) -> None:
        await self._s.execute(
            text(
                "INSERT INTO public.sys_dept (id, parent_id, dept_name, tenant_id, sort)"
                " VALUES (:i, :p, :n, :t, :s)"
            ),
            {"i": dept_id, "p": parent_id, "n": dept_name, "t": tenant_id, "s": sort},
        )

    async def update_dept(
        self,
        dept_id: str,
        *,
        parent_id: str | None,
        dept_name: str,
        tenant_id: str,
        sort: int,
        status: str,
    ) -> None:
        await self._s.execute(
            text(
                "UPDATE public.sys_dept SET parent_id=:p, dept_name=:n, tenant_id=:t,"
                " sort=:s, status=:st, updated_at=now() WHERE id=:i"
            ),
            {"p": parent_id, "n": dept_name, "t": tenant_id, "s": sort, "st": status, "i": dept_id},
        )

    async def delete_dept(self, dept_id: str) -> None:
        await self._s.execute(text("DELETE FROM public.sys_dept WHERE id=:i"), {"i": dept_id})

    async def dept_has_children(self, dept_id: str) -> bool:
        n = await self._s.scalar(
            text("SELECT count(*) FROM public.sys_dept WHERE parent_id=:i"), {"i": dept_id}
        )
        return int(n or 0) > 0

    async def dept_has_active_users(self, dept_id: str) -> bool:
        n = await self._s.scalar(
            text("SELECT count(*) FROM public.sys_user WHERE dept_id=:i AND deleted_at IS NULL"),
            {"i": dept_id},
        )
        return int(n or 0) > 0


class _TenantAuditMixin(_SystemRepoBase):
    """租户清单/存在性与操作审计留痕。"""

    async def tenant_exists(self, tenant_id: str) -> bool:
        n = await self._s.scalar(
            text("SELECT count(*) FROM public.sys_tenant WHERE id=:i"), {"i": tenant_id}
        )
        return int(n or 0) > 0

    async def log_oper(
        self, actor_id: str, action: str, target: str | None, detail: dict[str, object]
    ) -> None:
        await self._oper.add(actor_id, action, target, detail)

    async def list_tenants(self) -> list[TenantItem]:
        rows = (
            await self._s.execute(
                text(
                    "SELECT id, tenant_key, tenant_name FROM public.sys_tenant ORDER BY tenant_key"
                )
            )
        ).mappings()
        return [_to_tenant(dict(r)) for r in rows]


class SqlSystemRepo(
    _UserRepoMixin,
    _RoleRepoMixin,
    _PermRepoMixin,
    _DeptRepoMixin,
    _TenantAuditMixin,
):
    """system 仓储组合:各能力簇 mixin 拼接,自身只保留事务控制。"""

    async def commit(self) -> None:
        await self._s.commit()

    async def rollback(self) -> None:
        await self._s.rollback()
