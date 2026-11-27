"""服务域控制面端点。

- `/system/control`、`/system/commands*`:孔明管理台(用户 JWT + 本地权限)。
- `/service-read/{section}`:锦官只读投影(服务身份,字段白名单,无写入口)。
"""

import logging
from typing import Annotated, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.deps import CurrentUser, require_perm
from kongming.config import get_settings
from kongming.control.dependencies import get_control_runtime
from kongming.control.models import ControlAuthError
from kongming.control.projections import project
from kongming.control.runtime import ControlRuntime
from kongming.control.store import SqlCommandReceiptStore
from kongming.db import get_session, get_sessionmaker, uuid7

log = logging.getLogger(__name__)

router = APIRouter(prefix="/system", tags=["control"])
read_router = APIRouter(prefix="/service-read", tags=["service-read"])

VIEW = "system:control:view"
MANAGE = "system:control:manage"


class LocalCommandIn(BaseModel):
    action: str = Field(min_length=1, max_length=64)
    action_version: int = Field(default=1, ge=1)
    params: dict[str, object] = Field(default_factory=dict)
    reason: str = Field(min_length=1, max_length=500)


class LocalCommandOut(BaseModel):
    command_id: str
    idempotency_key: str
    status: str
    result: dict[str, object]
    error: str


class ReceiptItemOut(BaseModel):
    idempotency_key: str
    command_id: str
    action: str
    action_version: int
    source: str
    status: str
    result: dict[str, object]
    actor: str
    updated_at: str


class ReceiptOut(BaseModel):
    items: list[ReceiptItemOut]


class SharedTenantOut(BaseModel):
    tenant_key: str
    tenant_name: str
    status: str


class SharedEntitlementOut(BaseModel):
    tenant_key: str
    status: str
    features: list[str]
    entitlement_keys: list[str]
    quota_calls_daily: int
    quota_calls_monthly: int
    quota_bot_calls_daily: int


class SharedPackageOut(BaseModel):
    package_key: str
    version: int
    status: str
    rule_names: list[str]
    tenant_keys: list[str]


class SharedCatalogOut(BaseModel):
    bot_key: str
    listing: str
    visibility: str
    entitlement_key: str | None


class SharedSnapshotOut(BaseModel):
    present: bool
    version: int
    observed_at: str | None
    stale: bool
    tenants: list[SharedTenantOut]
    entitlements: list[SharedEntitlementOut]
    policy_packages: list[SharedPackageOut]
    catalog: list[SharedCatalogOut]


class ControlStatusOut(BaseModel):
    mode: str
    enabled: bool
    service_id: str
    base_url: str
    snapshot_present: bool
    snapshot_version: int
    observed_at: str | None
    stale: bool
    security_ok: bool
    pending_usage: int
    dropped_usage: int
    readonly_enabled: bool
    last_error: str


def _runtime() -> ControlRuntime:
    return get_control_runtime()


@router.get("/control", response_model=ControlStatusOut, dependencies=[Depends(require_perm(VIEW))])
async def control_status() -> ControlStatusOut:
    """控制面连接/快照/缓冲状态(只读;不含服务身份与验签密钥)。"""
    status = await _runtime().status()
    return ControlStatusOut(
        mode=status.mode,
        enabled=status.enabled,
        service_id=status.service_id,
        base_url=status.base_url,
        snapshot_present=status.snapshot_present,
        snapshot_version=status.snapshot_version,
        observed_at=status.observed_at.isoformat() if status.observed_at else None,
        stale=status.stale,
        security_ok=status.security_ok,
        pending_usage=status.pending_usage,
        dropped_usage=status.dropped_usage,
        readonly_enabled=status.readonly_enabled,
        last_error=status.last_error,
    )


@router.get(
    "/control/snapshot",
    response_model=SharedSnapshotOut,
    dependencies=[Depends(require_perm(VIEW))],
)
async def control_snapshot() -> SharedSnapshotOut:
    """共享控制只读视图(服务管理台展示;不返回成员/凭据)。"""
    view = await _runtime().snapshot.view()
    if not view.present:
        return SharedSnapshotOut(
            present=False,
            version=0,
            observed_at=None,
            stale=True,
            tenants=[],
            entitlements=[],
            policy_packages=[],
            catalog=[],
        )
    sections = view.sections
    return SharedSnapshotOut(
        present=True,
        version=view.version,
        observed_at=view.observed_at.isoformat() if view.observed_at else None,
        stale=view.stale,
        tenants=[
            SharedTenantOut(tenant_key=t.tenant_key, tenant_name=t.tenant_name, status=t.status)
            for t in sections.tenants
        ],
        entitlements=[
            SharedEntitlementOut(
                tenant_key=e.tenant_key,
                status=e.status,
                features=list(e.features),
                entitlement_keys=list(e.entitlement_keys),
                quota_calls_daily=e.quota_calls_daily,
                quota_calls_monthly=e.quota_calls_monthly,
                quota_bot_calls_daily=e.quota_bot_calls_daily,
            )
            for e in sections.entitlements
        ],
        policy_packages=[
            SharedPackageOut(
                package_key=p.package_key,
                version=p.version,
                status=p.status,
                rule_names=list(p.rule_names),
                tenant_keys=list(p.tenant_keys),
            )
            for p in sections.policy_packages
        ],
        catalog=[
            SharedCatalogOut(
                bot_key=c.bot_key,
                listing=c.listing,
                visibility=c.visibility,
                entitlement_key=c.entitlement_key,
            )
            for c in sections.catalog
        ],
    )


@router.post(
    "/commands",
    response_model=LocalCommandOut,
    dependencies=[Depends(require_perm(MANAGE))],
)
async def run_local_command(req: LocalCommandIn, user: CurrentUser) -> LocalCommandOut:
    """本地运行命令(reindex/清缓存/取消任务):控制面缺席仍可执行,结果审计。"""
    runtime = _runtime()
    command_id = uuid7().hex
    key = f"local:{command_id}"
    outcome = await runtime.executor.execute(
        command_id=command_id,
        idempotency_key=key,
        action=req.action,
        action_version=req.action_version,
        params=req.params,
        actor=user.user_id,
        source="local",
    )
    return LocalCommandOut(
        command_id=command_id,
        idempotency_key=key,
        status=outcome.status,
        result=outcome.result,
        error=outcome.error,
    )


@router.get(
    "/commands/receipts",
    response_model=ReceiptOut,
    dependencies=[Depends(require_perm(VIEW))],
)
async def command_receipts(
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ReceiptOut:
    """最近命令回执(本地执行 + 跨服务命令的终态)。"""
    store = SqlCommandReceiptStore(get_sessionmaker())
    rows = await store.recent(limit=limit)
    return ReceiptOut(
        items=[
            ReceiptItemOut(
                idempotency_key=str(row["idempotency_key"]),
                command_id=str(row["command_id"]),
                action=str(row["action"]),
                action_version=int(cast("int", row["action_version"])),
                source=str(row["source"]),
                status=str(row["status"]),
                result=cast("dict[str, object]", row["result"] or {}),
                actor=str(row["actor"] or ""),
                updated_at=str(row["updated_at"]),
            )
            for row in rows
        ]
    )


@read_router.get("/{section}")
async def service_read(
    section: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    """服务只读投影(锦官控制台消费)。未知节 404;写方法由框架 405。"""
    settings = get_settings()
    if not settings.control_readonly_enabled:
        raise HTTPException(status_code=404, detail="NOT_FOUND")
    runtime = _runtime()
    verifier = runtime.reader_verifier
    if not verifier.configured:
        raise HTTPException(status_code=503, detail="SERVICE_READ_NOT_CONFIGURED")
    try:
        await verifier.verify(authorization or "")
    except ControlAuthError as e:
        raise HTTPException(status_code=401, detail="UNAUTHORIZED") from e
    except Exception as e:  # JWKS 拉取失败等:服务只读不可用
        log.warning("服务只读认证不可用: %s", e)
        raise HTTPException(status_code=503, detail="SERVICE_READ_UNAVAILABLE") from e
    body = await project(section, session, runtime)
    if body is None:
        raise HTTPException(status_code=404, detail="NOT_FOUND")
    return body
