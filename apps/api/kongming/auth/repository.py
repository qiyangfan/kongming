"""auth 域 SQLAlchemy 仓储实现(唯一 import sqlalchemy 的 auth 文件,宪法 Article II)。

显式 SQL(text)而非 ORM 映射:identity 域表少且稳定,SQL 即文档;
行 → 领域 dataclass 的映射集中在各 _to_* 函数。
"""

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.audit.context import client_ip_var
from kongming.auth import ports
from kongming.auth.models import Role, Tenant, User
from kongming.db import uuid7

_USER_COLS = "id, username, email, password_hash, nickname, status, password_changed_at, deleted_at"


def _to_user(row: dict[str, Any]) -> User:
    return User(
        id=str(row["id"]),
        username=str(row["username"]),
        email=row["email"] if row["email"] is None else str(row["email"]),
        password_hash=None if row["password_hash"] is None else str(row["password_hash"]),
        nickname=str(row["nickname"]),
        status=row["status"],  # Any → Literal;CHECK 约束保证字面量合法
        password_changed_at=row["password_changed_at"],
        deleted=row["deleted_at"] is not None,
    )


def _to_role(row: dict[str, Any]) -> Role:
    return Role(
        id=str(row["id"]),
        role_key=str(row["role_key"]),
        role_name=str(row["role_name"]),
        data_scope=str(row["data_scope"]),
        sql_scope=str(row["sql_scope"]),
    )


def _to_tenant(row: dict[str, Any]) -> Tenant:
    return Tenant(
        id=str(row["id"]), tenant_key=str(row["tenant_key"]), tenant_name=str(row["tenant_name"])
    )


class SqlUserRepo:
    def __init__(self, s: AsyncSession) -> None:
        self._s = s

    async def get_by_username(self, username: str) -> User | None:
        row = (
            (
                await self._s.execute(
                    text(
                        f"SELECT {_USER_COLS} FROM public.sys_user"  # noqa: S608  # 常量列清单
                        " WHERE username=:u AND deleted_at IS NULL"
                    ),
                    {"u": username},
                )
            )
            .mappings()
            .one_or_none()
        )
        return _to_user(dict(row)) if row else None

    async def get_by_id(self, user_id: str) -> User | None:
        row = (
            (
                await self._s.execute(
                    text(f"SELECT {_USER_COLS} FROM public.sys_user WHERE id=:i"),  # noqa: S608
                    {"i": user_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        return _to_user(dict(row)) if row else None

    async def get_by_email(self, email: str) -> User | None:
        row = (
            (
                await self._s.execute(
                    text(
                        f"SELECT {_USER_COLS} FROM public.sys_user"  # noqa: S608
                        " WHERE email=:e AND deleted_at IS NULL ORDER BY created_at LIMIT 1"
                    ),
                    {"e": email},
                )
            )
            .mappings()
            .one_or_none()
        )
        return _to_user(dict(row)) if row else None

    async def create(self, user: User) -> None:
        await self._s.execute(
            text(
                "INSERT INTO public.sys_user"
                " (id, username, email, password_hash, nickname, status, password_changed_at)"
                " VALUES (:id, :u, :e, :ph, :n, :st, :pca)"
            ),
            {
                "id": user.id,
                "u": user.username,
                "e": user.email,
                "ph": user.password_hash,
                "n": user.nickname,
                "st": user.status,
                "pca": user.password_changed_at,
            },
        )

    async def update_password(self, user_id: str, password_hash: str) -> None:
        await self._s.execute(
            text(
                "UPDATE public.sys_user SET password_hash=:ph, password_changed_at=now(),"
                " updated_at=now() WHERE id=:i"
            ),
            {"ph": password_hash, "i": user_id},
        )

    async def touch_last_login(self, user_id: str) -> None:
        await self._s.execute(
            text("UPDATE public.sys_user SET last_login_at=now() WHERE id=:i"), {"i": user_id}
        )


class SqlRoleRepo:
    def __init__(self, s: AsyncSession) -> None:
        self._s = s

    async def get_by_key(self, role_key: str) -> Role | None:
        row = (
            (
                await self._s.execute(
                    text(
                        "SELECT id, role_key, role_name, data_scope, sql_scope"
                        " FROM public.sys_role WHERE role_key=:k"
                    ),
                    {"k": role_key},
                )
            )
            .mappings()
            .one_or_none()
        )
        return _to_role(dict(row)) if row else None

    async def insert(self, role: Role) -> None:
        await self._s.execute(
            text(
                "INSERT INTO public.sys_role (id, role_key, role_name, data_scope, sql_scope, sort)"
                " VALUES (:i, :k, :n, :ds, :ss, :sort) ON CONFLICT (role_key) DO NOTHING"
            ),
            {
                "i": role.id,
                "k": role.role_key,
                "n": role.role_name,
                "ds": role.data_scope,
                "ss": role.sql_scope,
                "sort": role.sort,
            },
        )

    async def list_role_keys(self, user_id: str) -> list[str]:
        rows = (
            (
                await self._s.execute(
                    text(
                        "SELECT r.role_key FROM public.sys_role r"
                        " JOIN public.sys_user_role ur ON ur.role_id=r.id WHERE ur.user_id=:u"
                        " ORDER BY r.sort, r.role_key"
                    ),
                    {"u": user_id},
                )
            )
            .scalars()
            .all()
        )
        return [str(k) for k in rows]

    async def bind_user(self, user_id: str, role_id: str) -> None:
        await self._s.execute(
            text(
                "INSERT INTO public.sys_user_role (user_id, role_id) VALUES (:u, :r)"
                " ON CONFLICT DO NOTHING"
            ),
            {"u": user_id, "r": role_id},
        )

    async def bind_perm_codes(self, role_id: str, perm_codes: list[str]) -> None:
        if not perm_codes:
            return
        await self._s.execute(
            text(
                "INSERT INTO public.sys_role_permission (role_id, permission_id)"
                " SELECT :r, p.id FROM public.sys_permission p WHERE p.perm_code = ANY(:codes)"
                " ON CONFLICT DO NOTHING"
            ),
            {"r": role_id, "codes": perm_codes},
        )


class SqlTenantRepo:
    def __init__(self, s: AsyncSession) -> None:
        self._s = s

    async def get_by_key(self, tenant_key: str) -> Tenant | None:
        stmt = text("SELECT id, tenant_key, tenant_name FROM public.sys_tenant WHERE tenant_key=:k")
        row = (await self._s.execute(stmt, {"k": tenant_key})).mappings().one_or_none()
        return _to_tenant(dict(row)) if row else None

    async def insert(self, tenant: Tenant) -> None:
        await self._s.execute(
            text(
                "INSERT INTO public.sys_tenant (id, tenant_key, tenant_name)"
                " VALUES (:i, :k, :n) ON CONFLICT (tenant_key) DO NOTHING"
            ),
            {"i": tenant.id, "k": tenant.tenant_key, "n": tenant.tenant_name},
        )

    async def list_for_user(self, user_id: str) -> list[Tenant]:
        rows = (
            (
                await self._s.execute(
                    text(
                        "SELECT t.id, t.tenant_key, t.tenant_name FROM public.sys_tenant t"
                        " JOIN public.sys_user_tenant ut ON ut.tenant_id=t.id WHERE ut.user_id=:u"
                        " ORDER BY t.tenant_key"
                    ),
                    {"u": user_id},
                )
            )
            .mappings()
            .all()
        )
        return [_to_tenant(dict(r)) for r in rows]

    async def bind_user(self, user_id: str, tenant_id: str) -> None:
        await self._s.execute(
            text(
                "INSERT INTO public.sys_user_tenant (user_id, tenant_id) VALUES (:u, :t)"
                " ON CONFLICT DO NOTHING"
            ),
            {"u": user_id, "t": tenant_id},
        )


class SqlPermRepo:
    def __init__(self, s: AsyncSession) -> None:
        self._s = s

    async def list_codes(self, user_id: str) -> list[str]:
        rows = (
            (
                await self._s.execute(
                    text(
                        # 未启用角色不授予任何权限(与 datascope 的「启用角色」闸同一口径):
                        # 此前这里不看 r.status,而行级过滤按 status 收敛——把角色置为 DISABLED
                        # 会让功能权限原样保留、数据范围解析为空却「降级为不过滤」,
                        # 即治理动作反向放宽可见面(086 收口)
                        "SELECT DISTINCT p.perm_code FROM public.sys_permission p"
                        " JOIN public.sys_role_permission rp ON rp.permission_id=p.id"
                        " JOIN public.sys_user_role ur ON ur.role_id=rp.role_id"
                        " JOIN public.sys_role r ON r.id=ur.role_id AND r.status='ACTIVE'"
                        " WHERE ur.user_id=:u ORDER BY p.perm_code"
                    ),
                    {"u": user_id},
                )
            )
            .scalars()
            .all()
        )
        return [str(c) for c in rows]

    async def insert_missing(self, perms: list[tuple[str, str, int]]) -> None:
        for code, name, sort in perms:
            await self._s.execute(
                text(
                    "INSERT INTO public.sys_permission (id, perm_code, perm_name, sort)"
                    " VALUES (:i, :c, :n, :s) ON CONFLICT (perm_code) DO NOTHING"
                ),
                {"i": str(uuid7()), "c": code, "n": name, "s": sort},
            )


class SqlIdentityRepo:
    def __init__(self, s: AsyncSession) -> None:
        self._s = s

    async def find_user_id(self, provider: str, external_id: str) -> str | None:
        uid = (
            await self._s.execute(
                text(
                    "SELECT user_id FROM public.user_identities"
                    " WHERE provider=:p AND external_id=:e"
                ),
                {"p": provider, "e": external_id},
            )
        ).scalar_one_or_none()
        return None if uid is None else str(uid)

    async def create(self, user_id: str, provider: str, external_id: str) -> None:
        await self._s.execute(
            text(
                "INSERT INTO public.user_identities (id, user_id, provider, external_id)"
                " VALUES (:i, :u, :p, :e) ON CONFLICT (provider, external_id) DO NOTHING"
            ),
            {"i": str(uuid7()), "u": user_id, "p": provider, "e": external_id},
        )


class SqlOperLogRepo:
    def __init__(self, s: AsyncSession) -> None:
        self._s = s

    async def add(
        self, user_id: str | None, action: str, target: str | None, detail: dict[str, object]
    ) -> None:
        await self._s.execute(
            text(
                "INSERT INTO public.sys_oper_log (id, user_id, action, target, detail, ip)"
                " VALUES (:i, :u, :a, :t, CAST(:d AS JSONB), :ip)"
            ),
            {
                "i": str(uuid7()),
                "u": user_id,
                "a": action,
                "t": target,
                "d": json.dumps(detail, ensure_ascii=False),
                # 015 ip 链路:请求上下文横切(contextvar),调用点零改动;
                # 无请求上下文(后台任务/定时任务/测试直调)自然为 None
                "ip": client_ip_var.get(),
            },
        )


class SqlUnitOfWork:
    """请求/用例级 UoW:聚合全部 repo,commit/rollback 委托 Session。

    属性显式标注为 Protocol 类型(可变属性对 Protocol 匹配是不变量,标注后对
    service 暴露的正是端口接口)。
    """

    def __init__(self, s: AsyncSession) -> None:
        self._s = s
        self.users: ports.UserRepo = SqlUserRepo(s)
        self.roles: ports.RoleRepo = SqlRoleRepo(s)
        self.tenants: ports.TenantRepo = SqlTenantRepo(s)
        self.perms: ports.PermRepo = SqlPermRepo(s)
        self.identities: ports.IdentityRepo = SqlIdentityRepo(s)
        self.operlog: ports.OperLogRepo = SqlOperLogRepo(s)

    async def commit(self) -> None:
        await self._s.commit()

    async def rollback(self) -> None:
        await self._s.rollback()
