"""处理 /system/* 管理端点。

守卫复用 auth.router(current_user/require_perm);领域错误在此统一映射
状态码(404 不存在 / 409 状态冲突 / 400 参数与领域规则不符)。
X-Tenant-Id 对管理端点无语义(身份治理是全局视角)。
"""

import logging
import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.deps import CurrentUser, require_perm
from kongming.auth.stores import RedisRefreshFamilyStore
from kongming.config import get_settings
from kongming.db import get_session
from kongming.redis_client import get_redis
from kongming.system.models import (
    AdminAccountProtected,
    BuiltinRoleProtected,
    CustomDeptScopeInvalid,
    DeptInUse,
    DeptNode,
    DeptNotFound,
    InvalidDeptParent,
    PermItem,
    RoleInUse,
    RoleKeyImmutable,
    RoleKeyTaken,
    RoleListItem,
    RoleNotFound,
    SelfDeletion,
    SelfDisable,
    SystemError,
    TenantItem,
    UnknownDeptId,
    UnknownPermId,
    UnknownRoleId,
    UnknownTenantId,
    UserListItem,
    UserNotFound,
)
from kongming.system.repository import SqlSystemRepo
from kongming.system.service import SystemService

router = APIRouter(prefix="/system", tags=["system"])

logger = logging.getLogger(__name__)

UserStatus = Literal["ACTIVE", "DISABLED"]
DataScope = Literal["ALL", "CUSTOM", "DEPT_AND_BELOW", "DEPT", "SELF"]
SqlScope = Literal["SELECT_ONLY", "READ_DDL", "DML_DDL"]


def get_system_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> SystemService:
    settings = get_settings()
    return SystemService(
        SqlSystemRepo(session),
        settings.admin_subject or "",
        families=RedisRefreshFamilyStore(redis),
        refresh_ttl_sec=settings.refresh_token_ttl_sec,
    )


Service = Annotated[SystemService, Depends(get_system_service)]

# ---- 传输模型 ----


class UserUpdateIn(BaseModel):
    """全量更新用户资料与授权;username 由Keycloak登录建档决定,创建后不可修改。"""

    nickname: str = Field(max_length=64)
    email: str | None = Field(default=None, max_length=128)
    status: UserStatus
    dept_id: uuid.UUID | None = None
    tenant_id: uuid.UUID
    role_ids: list[uuid.UUID] = Field(default_factory=list[uuid.UUID])


class UserItemOut(BaseModel):
    id: str
    username: str
    nickname: str
    email: str | None
    status: str
    dept_id: str | None
    dept_name: str | None
    tenant_id: str | None
    role_keys: list[str]
    created_at: datetime


class UserPageOut(BaseModel):
    total: int
    items: list[UserItemOut]


class RoleCreateIn(BaseModel):
    # 使用与内置角色一致的大写蛇形格式，拒绝空白和小写变体。
    role_key: str = Field(min_length=2, max_length=100, pattern=r"^[A-Z][A-Z0-9_]+$")
    role_name: str = Field(min_length=1, max_length=100)
    data_scope: DataScope = "SELF"
    sql_scope: SqlScope = "SELECT_ONLY"
    sort: int = 0
    # None=键缺失(既有客户端兼容);CUSTOM 时缺失由 service 显式拒绝
    data_scope_dept_ids: list[uuid.UUID] | None = None


class RoleUpdateIn(BaseModel):
    """role_key 创建后不可修改。"""

    role_key: str = Field(min_length=2, max_length=100, pattern=r"^[A-Z][A-Z0-9_]+$")
    role_name: str = Field(min_length=1, max_length=100)
    data_scope: DataScope
    sql_scope: SqlScope
    sort: int
    data_scope_dept_ids: list[uuid.UUID] | None = None


class RolePermsIn(BaseModel):
    # None=键缺失(400 防呆);显式空数组=清空全部绑定(rbac spec)
    perm_ids: list[uuid.UUID] | None = None


class RoleOut(BaseModel):
    id: str
    role_key: str
    role_name: str
    data_scope: str
    sql_scope: str
    sort: int
    builtin: bool
    perm_ids: list[str]
    # data_scope=CUSTOM 的自定义部门集合(新增字段,既有客户端可忽略;非 CUSTOM 恒空)
    data_scope_dept_ids: list[str]


class PermOut(BaseModel):
    id: str
    perm_code: str
    perm_name: str
    sort: int


class DeptCreateIn(BaseModel):
    parent_id: uuid.UUID | None = None
    dept_name: str = Field(min_length=1, max_length=100)
    tenant_id: uuid.UUID
    sort: int = 0


class DeptUpdateIn(BaseModel):
    parent_id: uuid.UUID | None = None
    dept_name: str = Field(min_length=1, max_length=100)
    tenant_id: uuid.UUID
    sort: int
    status: UserStatus


class DeptNodeOut(BaseModel):
    id: str
    parent_id: str | None
    dept_name: str
    tenant_id: str
    sort: int
    status: str
    children: list["DeptNodeOut"] = Field(default_factory=list["DeptNodeOut"])


class TenantOut(BaseModel):
    id: str
    tenant_key: str
    tenant_name: str


def _map_user(u: UserListItem) -> UserItemOut:
    return UserItemOut(
        id=u.id,
        username=u.username,
        nickname=u.nickname,
        email=u.email,
        status=u.status,
        dept_id=u.dept_id,
        dept_name=u.dept_name,
        tenant_id=u.tenant_id,
        role_keys=u.role_keys,
        created_at=u.created_at,
    )


def _map_role(r: RoleListItem) -> RoleOut:
    return RoleOut(
        id=r.id,
        role_key=r.role_key,
        role_name=r.role_name,
        data_scope=r.data_scope,
        sql_scope=r.sql_scope,
        sort=r.sort,
        builtin=r.builtin,
        perm_ids=r.perm_ids,
        data_scope_dept_ids=r.dept_ids,
    )


def _map_perm(p: PermItem) -> PermOut:
    return PermOut(id=p.id, perm_code=p.perm_code, perm_name=p.perm_name, sort=p.sort)


def _map_dept(n: DeptNode) -> DeptNodeOut:
    return DeptNodeOut(
        id=n.dept.id,
        parent_id=n.dept.parent_id,
        dept_name=n.dept.dept_name,
        tenant_id=n.dept.tenant_id,
        sort=n.dept.sort,
        status=n.dept.status,
        children=[_map_dept(c) for c in n.children],
    )


def _map_tenant(t: TenantItem) -> TenantOut:
    return TenantOut(id=t.id, tenant_key=t.tenant_key, tenant_name=t.tenant_name)


def _dept_ids_in(req: RoleCreateIn | RoleUpdateIn) -> list[str] | None:
    """None(键缺失)与 [](显式空)分开下传;搭配规则由 service 判。"""
    return None if req.data_scope_dept_ids is None else [str(d) for d in req.data_scope_dept_ids]


# ---- 用户管理(权限点 system:user:manage) ----


@router.get(
    "/users",
    response_model=UserPageOut,
    dependencies=[Depends(require_perm("system:user:manage"))],
)
async def list_users(
    svc: Service,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 10,
    keyword: Annotated[str, Query(max_length=64)] = "",
) -> UserPageOut:
    total, items = await svc.list_users(page=page, size=size, keyword=keyword)
    return UserPageOut(total=total, items=[_map_user(u) for u in items])


@router.put(
    "/users/{user_id}",
    status_code=204,
    dependencies=[Depends(require_perm("system:user:manage"))],
)
async def update_user(
    user_id: uuid.UUID, req: UserUpdateIn, user: CurrentUser, svc: Service
) -> None:
    await svc.update_user(
        actor_id=user.user_id,
        user_id=str(user_id),
        nickname=req.nickname,
        email=req.email,
        status=req.status,
        dept_id=None if req.dept_id is None else str(req.dept_id),
        tenant_id=str(req.tenant_id),
        role_ids=[str(r) for r in req.role_ids],
    )


@router.delete(
    "/users/{user_id}",
    status_code=204,
    dependencies=[Depends(require_perm("system:user:manage"))],
)
async def delete_user(user_id: uuid.UUID, user: CurrentUser, svc: Service) -> None:
    await svc.delete_user(actor_id=user.user_id, user_id=str(user_id))


@router.post(
    "/users/{user_id}/enable",
    status_code=204,
    dependencies=[Depends(require_perm("system:user:manage"))],
)
async def enable_user(user_id: uuid.UUID, user: CurrentUser, svc: Service) -> None:
    await svc.enable_user(actor_id=user.user_id, user_id=str(user_id))


@router.post(
    "/users/{user_id}/disable",
    status_code=204,
    dependencies=[Depends(require_perm("system:user:manage"))],
)
async def disable_user(user_id: uuid.UUID, user: CurrentUser, svc: Service) -> None:
    await svc.disable_user(actor_id=user.user_id, user_id=str(user_id))


# ---- 角色管理(权限点 system:role:manage) ----


@router.get(
    "/roles",
    response_model=list[RoleOut],
    dependencies=[Depends(require_perm("system:role:manage"))],
)
async def list_roles(svc: Service) -> list[RoleOut]:
    return [_map_role(r) for r in await svc.list_roles()]


@router.post(
    "/roles",
    status_code=201,
    dependencies=[Depends(require_perm("system:role:manage"))],
)
async def create_role(req: RoleCreateIn, user: CurrentUser, svc: Service) -> dict[str, str]:
    role_id = await svc.create_role(
        actor_id=user.user_id,
        role_key=req.role_key,
        role_name=req.role_name,
        data_scope=req.data_scope,
        sql_scope=req.sql_scope,
        sort=req.sort,
        dept_ids=_dept_ids_in(req),
    )
    return {"id": role_id}


@router.put(
    "/roles/{role_id}",
    status_code=204,
    dependencies=[Depends(require_perm("system:role:manage"))],
)
async def update_role(
    role_id: uuid.UUID, req: RoleUpdateIn, user: CurrentUser, svc: Service
) -> None:
    await svc.update_role(
        actor_id=user.user_id,
        role_id=str(role_id),
        role_key=req.role_key,
        role_name=req.role_name,
        data_scope=req.data_scope,
        sql_scope=req.sql_scope,
        sort=req.sort,
        dept_ids=_dept_ids_in(req),
    )


@router.delete(
    "/roles/{role_id}",
    status_code=204,
    dependencies=[Depends(require_perm("system:role:manage"))],
)
async def delete_role(role_id: uuid.UUID, user: CurrentUser, svc: Service) -> None:
    await svc.delete_role(actor_id=user.user_id, role_id=str(role_id))


@router.post(
    "/roles/{role_id}/permissions",
    status_code=204,
    dependencies=[Depends(require_perm("system:role:manage"))],
)
async def bind_role_perms(
    role_id: uuid.UUID, req: RolePermsIn, user: CurrentUser, svc: Service
) -> None:
    if req.perm_ids is None:
        raise HTTPException(status_code=400, detail="PERM_IDS_REQUIRED")
    await svc.bind_role_perms(
        actor_id=user.user_id,
        role_id=str(role_id),
        perm_ids=[str(p) for p in req.perm_ids],
    )


@router.get(
    "/permissions",
    response_model=list[PermOut],
    dependencies=[Depends(require_perm("system:role:manage"))],
)
async def list_perms(svc: Service) -> list[PermOut]:
    return [_map_perm(p) for p in await svc.list_perms()]


# ---- 部门管理(权限点 system:user:manage,用户管理辅助数据) ----


@router.get(
    "/depts",
    response_model=list[DeptNodeOut],
    dependencies=[Depends(require_perm("system:user:manage"))],
)
async def dept_tree(svc: Service) -> list[DeptNodeOut]:
    return [_map_dept(n) for n in await svc.dept_tree()]


@router.post(
    "/depts",
    status_code=201,
    dependencies=[Depends(require_perm("system:user:manage"))],
)
async def create_dept(req: DeptCreateIn, user: CurrentUser, svc: Service) -> dict[str, str]:
    dept_id = await svc.create_dept(
        actor_id=user.user_id,
        parent_id=None if req.parent_id is None else str(req.parent_id),
        dept_name=req.dept_name,
        tenant_id=str(req.tenant_id),
        sort=req.sort,
    )
    return {"id": dept_id}


@router.put(
    "/depts/{dept_id}",
    status_code=204,
    dependencies=[Depends(require_perm("system:user:manage"))],
)
async def update_dept(
    dept_id: uuid.UUID, req: DeptUpdateIn, user: CurrentUser, svc: Service
) -> None:
    await svc.update_dept(
        actor_id=user.user_id,
        dept_id=str(dept_id),
        parent_id=None if req.parent_id is None else str(req.parent_id),
        dept_name=req.dept_name,
        tenant_id=str(req.tenant_id),
        sort=req.sort,
        status=req.status,
    )


@router.delete(
    "/depts/{dept_id}",
    status_code=204,
    dependencies=[Depends(require_perm("system:user:manage"))],
)
async def delete_dept(dept_id: uuid.UUID, user: CurrentUser, svc: Service) -> None:
    await svc.delete_dept(actor_id=user.user_id, dept_id=str(dept_id))


# ---- 租户只读清单(权限点 system:user:manage) ----


@router.get(
    "/tenants",
    response_model=list[TenantOut],
    dependencies=[Depends(require_perm("system:user:manage"))],
)
async def list_tenants(svc: Service) -> list[TenantOut]:
    return [_map_tenant(t) for t in await svc.list_tenants()]


# ---- 领域错误 → HTTP 映射(router 唯一的规则) ----

_STATUS_BY_TYPE: tuple[tuple[type[SystemError], int], ...] = (
    (UserNotFound, 404),
    (RoleNotFound, 404),
    (DeptNotFound, 404),
    (RoleKeyTaken, 409),
    (BuiltinRoleProtected, 409),
    (AdminAccountProtected, 409),
    (SelfDeletion, 409),
    (SelfDisable, 409),
    (RoleInUse, 409),
    (DeptInUse, 409),
    (RoleKeyImmutable, 400),
    (InvalidDeptParent, 400),
    (UnknownRoleId, 400),
    (UnknownPermId, 400),
    (UnknownTenantId, 400),
    (UnknownDeptId, 400),
    (CustomDeptScopeInvalid, 400),
)


def register_system_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(IntegrityError)
    async def _(request: Request, e: IntegrityError) -> JSONResponse:
        # 仅收敛管理面路径的并发唯一约束竞态(username/role_key 先查后插窗口);
        # 其他域维持 500 语义(与未注册本 handler 时的默认行为等价),证据留日志
        if request.url.path.startswith("/api/system") or request.url.path.startswith("/system"):
            logger.warning("管理面唯一约束冲突 path=%s", request.url.path, exc_info=e)
            return JSONResponse(status_code=409, content={"detail": "数据冲突,请刷新后重试"})
        logger.error("IntegrityError(非管理面,维持 500) path=%s", request.url.path, exc_info=e)
        return JSONResponse(status_code=500, content={"detail": "Internal Server Error"})

    @app.exception_handler(SystemError)
    async def _(_: Request, e: SystemError) -> JSONResponse:
        code = next((c for t, c in _STATUS_BY_TYPE if isinstance(e, t)), 400)
        return JSONResponse(status_code=code, content={"detail": str(e)})
