"""启动幂等种子(rbac spec「种子数据」):角色/权限点/默认租户/超管。

纪律:只增不改——按唯一键查缺补插,从不 UPDATE/DELETE 既有行(不覆盖线上改动);
重复启动幂等。权限点=随代码发布的契约数据,注册表即唯一定义处。
"""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth import password as pwd
from kongming.auth.models import Role, Tenant, User
from kongming.auth.repository import SqlUnitOfWork
from kongming.auth.service import DEFAULT_ROLE_KEY, DEFAULT_TENANT_KEY
from kongming.config import Settings
from kongming.db import uuid7

DEFAULT_ADMIN_PASSWORD = "admin123"  # noqa: S105  # 缺省不安全密码:播种后强制首登改密兜底

# 权限点注册表:(perm_code, perm_name, sort);业务权限点随各自域变更增补本清单
PERMISSIONS: list[tuple[str, str, int]] = [
    ("system:user:manage", "用户管理", 1),
    ("system:role:manage", "角色管理", 2),
    ("chat:execute", "问答执行", 3),
    ("system:datasource:manage", "数据源管理", 4),
]

ROLES: list[tuple[str, str, str, str, int]] = [
    # (role_key, role_name, data_scope, sql_scope, sort)
    ("SUPER_ADMIN", "超级管理员", "ALL", "DML_DDL", 1),
    (DEFAULT_ROLE_KEY, "开发者", "SELF", "SELECT_ONLY", 2),
]

# 角色推荐绑定(只增不摘):SUPER_ADMIN 由下方逻辑增量补齐全部注册点,
# 其余内置角色按本清单增量绑定——不覆盖运行时的人工调整
ROLE_RECOMMENDED_PERMS: dict[str, list[str]] = {
    DEFAULT_ROLE_KEY: ["chat:execute"],
}

DEFAULT_TENANT_NAME = "默认租户"


async def seed_all(session: AsyncSession, settings: Settings) -> None:
    """幂等播种(可重复调用):全部写操作 ON CONFLICT DO NOTHING / 查缺补插。"""
    uow = SqlUnitOfWork(session)

    await uow.perms.insert_missing(PERMISSIONS)
    for role_key, role_name, data_scope, sql_scope, sort in ROLES:
        await uow.roles.insert(
            Role(
                id=str(uuid7()),
                role_key=role_key,
                role_name=role_name,
                data_scope=data_scope,
                sql_scope=sql_scope,
                sort=sort,
            )
        )
    await uow.tenants.insert(
        Tenant(id=str(uuid7()), tenant_key=DEFAULT_TENANT_KEY, tenant_name=DEFAULT_TENANT_NAME)
    )

    # SUPER_ADMIN 增量补齐全部注册权限点(新版新增权限点自动达超管;不摘既有绑定)
    admin_role = await uow.roles.get_by_key("SUPER_ADMIN")
    if admin_role is not None:
        await uow.roles.bind_perm_codes(admin_role.id, [c for c, _, _ in PERMISSIONS])

    # 内置角色推荐绑定增量补齐(只增不摘,ON CONFLICT DO NOTHING)
    for role_key, codes in ROLE_RECOMMENDED_PERMS.items():
        role = await uow.roles.get_by_key(role_key)
        if role is not None:
            await uow.roles.bind_perm_codes(role.id, codes)

    # 超管:存在即跳过(含密码;在线改密后重播不覆盖)
    admin = await uow.users.get_by_username(settings.admin_username)
    if admin is None:
        env_password = settings.admin_initial_password
        admin = User(
            id=str(uuid7()),
            username=settings.admin_username,
            email=None,
            password_hash=pwd.hash_password(env_password or DEFAULT_ADMIN_PASSWORD),
            nickname="超管",
            status="ACTIVE",
            # env 密码视为已改密;缺省密码强制首登改密(spec 明文)
            password_changed_at=datetime.now(UTC) if env_password else None,
        )
        await uow.users.create(admin)
    if admin_role is not None:
        await uow.roles.bind_user(admin.id, admin_role.id)
    tenant = await uow.tenants.get_by_key(DEFAULT_TENANT_KEY)
    if tenant is not None:
        await uow.tenants.bind_user(admin.id, tenant.id)

    await uow.commit()
