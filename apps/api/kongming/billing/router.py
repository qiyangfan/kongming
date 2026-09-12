"""billing 路由(034,薄适配层):/billing/* 超管配置 + /portal/* 自助门户。

配置挂 system:billing:manage(全局视角,租户显式指定);门户登录即可
(developer:portal,限自己租户)。
035:支付闭环——门户支付端点(创建/查询/取消/mock-simulate)+统一回调
(无登录态验签即鉴权,031 webhook 同构)+管理面作废/分页。
"""

import logging
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.repository import SqlOperLogRepo
from kongming.auth.router import current_user, require_perm
from kongming.billing.channels import MockChannel
from kongming.billing.ports import PaymentChannel
from kongming.billing.repository import SqlBillingRepo
from kongming.billing.service import (
    BillingNotFound,
    BillingService,
    BillingValidationError,
    PaymentBadSignature,
    PaymentChannelClosed,
    PaymentConflict,
)
from kongming.chat.repository import ChatRepository
from kongming.config import get_settings
from kongming.db import get_session

log = logging.getLogger(__name__)

router = APIRouter(tags=["billing"])

MANAGE = "system:billing:manage"
PORTAL = "developer:portal"


class _Oper:
    def __init__(self, session: AsyncSession, actor: str | None) -> None:
        self._r = SqlOperLogRepo(session)
        self._a = actor

    async def log(self, *, action: str, target: str, detail: dict[str, object]) -> None:
        await self._r.add(self._a, action, target, detail)


def _channels() -> dict[str, PaymentChannel]:
    """通道注册表(035 design D1):mock 经环境开关整体关闭(design 风险面)。"""
    st = get_settings()
    if not st.billing_mock_enabled:
        return {}
    return {MockChannel.key: MockChannel(st.billing_mock_secret)}


def _svc(session: AsyncSession, actor: str | None = None) -> BillingService:
    # 审计器恒创建:callback 等无登录态场景 actor=None 落库即匿名
    # (与 034 网关切面 _QuotaOper 同款)
    st = get_settings()
    return BillingService(
        SqlBillingRepo(session),
        _Oper(session, actor),
        channels=_channels(),
        payment_ttl_minutes=st.billing_payment_ttl_minutes,
    )


def _pay_http(e: Exception) -> HTTPException:
    """支付异常族 → HTTP 态映射(单点,门户/回调/管理面三路同用)。"""
    if isinstance(e, BillingNotFound | PaymentChannelClosed):
        return HTTPException(status_code=404, detail=str(e) or "NOT_FOUND")
    if isinstance(e, PaymentBadSignature):
        return HTTPException(status_code=401, detail=str(e) or "BAD_SIGNATURE")
    if isinstance(e, PaymentConflict):
        return HTTPException(status_code=409, detail=str(e))
    return HTTPException(status_code=400, detail=str(e))


async def _tenant_or_403(user: dict[str, object], session: AsyncSession, header: str | None) -> str:
    repo = ChatRepository(session)
    uid = str(user["sub"])
    if header is not None:
        tid = await repo.find_bound_tenant(user_id=uid, tenant_key=header)
        if tid is None:
            raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN")
        return str(tid)
    default = await repo.find_default_tenant(user_id=uid)
    if default is None:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN")
    return str(default[0])


# ── 传输层响应模型(R1-S3:ADR-005 契约精确化,前端消费生成类型) ──


class PolicyOut(BaseModel):
    id: str
    tenant_id: str
    dimension: str
    period: str
    limit_value: int
    warn_pct: int
    scope: str = "TENANT"
    scope_ref: str | None = None


class PlanOut(BaseModel):
    id: str
    plan_key: str
    name: str
    monthly_tokens: int
    monthly_calls: int
    price_per_mtok: int
    price_per_call: int


class InvoiceOut(BaseModel):
    id: str
    period: str
    amount: int
    status: str
    created_at: str | None = None


class InvoiceAdminOut(InvoiceOut):
    tenant_key: str
    payment_status: str | None = None  # 035:最新支付单状态(无单 null)


class InvoiceAdminPageOut(BaseModel):
    items: list[InvoiceAdminOut]
    total: int
    page: int
    size: int


class InvoiceDetailOut(InvoiceOut):
    usage_json: dict[str, Any]


class PortalUsageOut(BaseModel):
    month: str
    tokens: int
    calls: int
    cost: int
    policies: list[PolicyOut]
    plan: PlanOut | None = None


class PortalKeyOut(BaseModel):
    id: str
    bot_key: str
    name: str
    created_at: str | None = None
    last_used_at: str | None = None


# ── 支付(035) ──


class PaymentOut(BaseModel):
    id: str
    invoice_id: str
    channel: str
    amount: int
    status: str
    trade_no: str | None = None
    expires_at: str | None = None
    paid_at: str | None = None
    created_at: str | None = None


class ReconcileOut(BaseModel):
    idempotent: bool
    status: str


class MockSimulateIn(BaseModel):
    result: Literal["SUCCESS", "FAILURE"]


class CallbackIn(BaseModel):
    payment_id: uuid.UUID
    trade_no: str = Field(min_length=1, max_length=64)
    result: Literal["SUCCESS", "FAILURE"]
    amount: int = Field(ge=0)
    sign: str = Field(min_length=1, max_length=128)


def _payment_out(row: dict[str, Any]) -> PaymentOut:
    return PaymentOut(
        id=str(row["id"]),
        invoice_id=str(row["invoice_id"]),
        channel=str(row["channel"]),
        amount=int(row["amount"]),
        status=str(row["status"]),
        trade_no=str(row["trade_no"]) if row.get("trade_no") else None,
        expires_at=row["expires_at"].isoformat() if row.get("expires_at") else None,
        paid_at=row["paid_at"].isoformat() if row.get("paid_at") else None,
        created_at=row["created_at"].isoformat() if row.get("created_at") else None,
    )


# ── 超管配置 ──


class PolicyIn(BaseModel):
    tenant_id: uuid.UUID  # T2:形态校验(非 UUID 422 不炸 500)
    dimension: str = Field(pattern="^(TOKENS|CALLS|COST)$")
    limit_value: int = Field(ge=0)
    warn_pct: int = Field(default=80, ge=1, le=100)
    # 035:BOT 级配额(scope_ref=BOT 时必填 bot_id)
    scope: str = Field(default="TENANT", pattern="^(TENANT|BOT)$")
    scope_ref: uuid.UUID | None = None


@router.post("/billing/policies", status_code=201, dependencies=[Depends(require_perm(MANAGE))])
async def upsert_policy(
    body: PolicyIn,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, bool]:
    # T2:租户存在复校(孤儿策略静默挂不可取)
    from sqlalchemy import text as _text2

    exists = (
        await session.execute(
            _text2("SELECT 1 FROM public.sys_tenant WHERE id=CAST(:t AS uuid)"),
            {"t": str(body.tenant_id)},
        )
    ).scalar_one_or_none()
    if exists is None:
        raise HTTPException(status_code=404, detail="TENANT_NOT_FOUND") from None
    svc = _svc(session, str(user["sub"]))
    try:
        await svc.upsert_policy(
            tenant_id=str(body.tenant_id),
            dimension=body.dimension,
            limit_value=body.limit_value,
            warn_pct=body.warn_pct,
            scope=body.scope,
            scope_ref=str(body.scope_ref) if body.scope_ref else None,
        )
        await session.commit()
    except BillingNotFound as e:
        raise _pay_http(e) from None
    except BillingValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return {"ok": True}


@router.get(
    "/billing/policies",
    response_model=list[PolicyOut],
    dependencies=[Depends(require_perm(MANAGE))],
)
async def list_policies(
    _: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[str, Query(min_length=1)],
) -> list[PolicyOut]:
    rows = await SqlBillingRepo(session).list_policies(tenant_id=tenant_id)
    return [
        PolicyOut(
            id=str(r["id"]),
            tenant_id=str(r["tenant_id"]),
            dimension=str(r["dimension"]),
            period=str(r["period"]),
            limit_value=int(r["limit_value"]),
            warn_pct=int(r["warn_pct"]),
            scope=str(r.get("scope") or "TENANT"),
            scope_ref=str(r["scope_ref"]) if r.get("scope_ref") else None,
        )
        for r in rows
    ]


@router.delete("/billing/policies/{policy_id}", dependencies=[Depends(require_perm(MANAGE))])
async def delete_policy(
    policy_id: str,
    _: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[str, Query(min_length=1)],
) -> dict[str, bool]:
    if not await SqlBillingRepo(session).delete_policy(policy_id=policy_id, tenant_id=tenant_id):
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    # R1-A4:删配额=解除防线,与 upsert 审计对称
    user2 = _
    await SqlOperLogRepo(session).add(
        str(user2["sub"]), "billing.policy.delete", policy_id, {"tenant_id": tenant_id}
    )
    await session.commit()
    return {"ok": True}


class PlanIn(BaseModel):
    plan_key: str = Field(min_length=2, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    monthly_tokens: int = Field(default=0, ge=0)
    monthly_calls: int = Field(default=0, ge=0)
    price_per_mtok: int = Field(default=0, ge=0)
    price_per_call: int = Field(default=0, ge=0)


@router.post("/billing/plans", status_code=201, dependencies=[Depends(require_perm(MANAGE))])
async def create_plan(
    body: PlanIn,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    svc = _svc(session, str(user["sub"]))
    try:
        rid = await svc.create_plan(**body.model_dump())
        await session.commit()
    except BillingValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return {"id": rid}


@router.get(
    "/billing/plans", response_model=list[PlanOut], dependencies=[Depends(require_perm(MANAGE))]
)
async def list_plans(
    _: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[PlanOut]:
    rows = await SqlBillingRepo(session).list_plans()
    return [
        PlanOut(
            id=str(r["id"]),
            plan_key=str(r["plan_key"]),
            name=str(r["name"]),
            monthly_tokens=int(r["monthly_tokens"]),
            monthly_calls=int(r["monthly_calls"]),
            price_per_mtok=int(r["price_per_mtok"]),
            price_per_call=int(r["price_per_call"]),
        )
        for r in rows
    ]


class PlanPatchIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    monthly_tokens: int = Field(default=0, ge=0)
    monthly_calls: int = Field(default=0, ge=0)
    price_per_mtok: int = Field(default=0, ge=0)
    price_per_call: int = Field(default=0, ge=0)


@router.patch("/billing/plans/{plan_id}", dependencies=[Depends(require_perm(MANAGE))])
async def update_plan(
    plan_id: str,
    body: PlanPatchIn,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, bool]:
    """035 挂账④:套餐更新(改价不回溯既有账单——明细为出账时快照)。"""
    svc = _svc(session, str(user["sub"]))
    try:
        await svc.update_plan(plan_id=plan_id, **body.model_dump())
        await session.commit()
    except BillingNotFound as e:
        raise _pay_http(e) from None
    except BillingValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return {"ok": True}


@router.delete("/billing/plans/{plan_id}", dependencies=[Depends(require_perm(MANAGE))])
async def delete_plan(
    plan_id: str,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, bool]:
    """035 挂账④:绑定中 409 PLAN_IN_USE;无绑定删除+审计。"""
    svc = _svc(session, str(user["sub"]))
    try:
        await svc.delete_plan(plan_id=plan_id)
        await session.commit()
    except (BillingNotFound, PaymentConflict) as e:
        raise _pay_http(e) from None
    return {"ok": True}


class TenantBriefOut(BaseModel):
    id: str
    tenant_key: str
    tenant_name: str


@router.get(
    "/billing/tenants",
    response_model=list[TenantBriefOut],
    dependencies=[Depends(require_perm(MANAGE))],
)
async def list_tenants_brief(
    _: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[TenantBriefOut]:
    """035 挂账⑥:计费配置面自供租户清单(解耦 system:user:manage)。"""
    rows = await SqlBillingRepo(session).list_tenants_brief()
    return [
        TenantBriefOut(
            id=str(r["id"]),
            tenant_key=str(r["tenant_key"]),
            tenant_name=str(r["tenant_name"]),
        )
        for r in rows
    ]


class BotBriefOut(BaseModel):
    id: str
    bot_key: str
    name: str


@router.get(
    "/billing/bots", response_model=list[BotBriefOut], dependencies=[Depends(require_perm(MANAGE))]
)
async def list_bots_brief(
    _: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[str, Query(min_length=1)],
) -> list[BotBriefOut]:
    """035:BOT 级策略配置面 Bot 下拉(挂 MANAGE 自供,解耦 bot:manage——
    与租户清单同解耦原则)。"""
    rows = await SqlBillingRepo(session).list_bots_brief(tenant_id=tenant_id)
    return [
        BotBriefOut(id=str(r["id"]), bot_key=str(r["bot_key"]), name=str(r["name"])) for r in rows
    ]


@router.get(
    "/billing/tenants/{tenant_id}/plan",
    response_model=PlanOut | None,
    dependencies=[Depends(require_perm(MANAGE))],
)
async def get_tenant_plan(
    tenant_id: str,
    _: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PlanOut | None:
    """R1-A9:管理面查租户当前绑定套餐(受控回显用;无绑定 null)。"""
    row = await SqlBillingRepo(session).get_tenant_plan(tenant_id=tenant_id)
    if row is None:
        return None
    return PlanOut(
        id=str(row["id"]),
        plan_key=str(row["plan_key"]),
        name=str(row["name"]),
        monthly_tokens=int(row["monthly_tokens"]),
        monthly_calls=int(row["monthly_calls"]),
        price_per_mtok=int(row["price_per_mtok"]),
        price_per_call=int(row["price_per_call"]),
    )


class BindPlanIn(BaseModel):
    tenant_id: str = Field(min_length=1)
    plan_id: str | None = None


@router.post("/billing/tenants/bind-plan", dependencies=[Depends(require_perm(MANAGE))])
async def bind_plan(
    body: BindPlanIn,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, bool]:
    # R1-A3:plan 存在性校验(悬空绑定静默回「无套餐」不限量是配置事故
    # 无信号)+审计(计费语义变更);非 UUID 形态 400
    import uuid as _uuid

    try:
        if body.plan_id is not None:
            _uuid.UUID(body.plan_id)
        _uuid.UUID(body.tenant_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="INVALID_UUID") from None
    if body.plan_id is not None:
        plans = await SqlBillingRepo(session).list_plans()
        if not any(str(p["id"]) == body.plan_id for p in plans):
            raise HTTPException(status_code=404, detail="PLAN_NOT_FOUND") from None
    repo = SqlBillingRepo(session)
    await repo.bind_plan(tenant_id=body.tenant_id, plan_id=body.plan_id)
    await SqlOperLogRepo(session).add(
        str(user["sub"]),
        "billing.plan.bind",
        body.tenant_id,
        {"plan_id": body.plan_id},
    )
    await session.commit()
    return {"ok": True}


@router.get(
    "/billing/invoices",
    response_model=InvoiceAdminPageOut,
    dependencies=[Depends(require_perm(MANAGE))],
)
async def page_all_invoices(
    _: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> InvoiceAdminPageOut:
    """035 挂账⑧:管理面账单分页(弃 LIMIT 200);行附最新支付单状态。"""
    rows, total = await SqlBillingRepo(session).page_all_invoices(page=page, size=size)
    return InvoiceAdminPageOut(
        items=[
            InvoiceAdminOut(
                id=str(r["id"]),
                tenant_key=str(r["tenant_key"]),
                period=str(r["period"]),
                amount=int(r["amount"]),
                status=str(r["status"]),
                created_at=r["created_at"].isoformat() if r.get("created_at") else None,
                payment_status=(str(r["payment_status"]) if r.get("payment_status") else None),
            )
            for r in rows
        ],
        total=total,
        page=page,
        size=size,
    )


# ── 门户(自助,限自己租户) ──


@router.get(
    "/portal/usage", response_model=PortalUsageOut, dependencies=[Depends(require_perm(PORTAL))]
)
async def portal_usage(
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> PortalUsageOut:
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    out = await _svc(session).portal_usage(tenant_id=tenant_id)
    return PortalUsageOut(
        month=str(out["month"]),
        tokens=int(out["tokens"]),
        calls=int(out["calls"]),
        cost=int(out["cost"]),
        policies=[
            PolicyOut(
                id=str(p["id"]),
                tenant_id=str(p["tenant_id"]),
                dimension=str(p["dimension"]),
                period=str(p["period"]),
                limit_value=int(p["limit_value"]),
                warn_pct=int(p["warn_pct"]),
                scope=str(p.get("scope") or "TENANT"),
                scope_ref=str(p["scope_ref"]) if p.get("scope_ref") else None,
            )
            for p in out["policies"]
        ],
        plan=(
            PlanOut(
                id=str(out["plan"]["id"]),
                plan_key=str(out["plan"]["plan_key"]),
                name=str(out["plan"]["name"]),
                monthly_tokens=int(out["plan"]["monthly_tokens"]),
                monthly_calls=int(out["plan"]["monthly_calls"]),
                price_per_mtok=int(out["plan"]["price_per_mtok"]),
                price_per_call=int(out["plan"]["price_per_call"]),
            )
            if out["plan"]
            else None
        ),
    )


@router.get(
    "/portal/keys", response_model=list[PortalKeyOut], dependencies=[Depends(require_perm(PORTAL))]
)
async def portal_keys(
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> list[PortalKeyOut]:
    """R1-A6:门户 api-key 清单(spec「key 列表」兑现;025 仓储聚合,
    本人 Bot 的 key,吊销态不回显)。"""
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    from sqlalchemy import text as _text

    rows = (
        (
            await session.execute(
                _text(
                    "SELECT k.id, b.bot_key, k.name, k.created_at, k.last_used_at"
                    " FROM public.sys_api_key k"
                    " JOIN public.sys_bot_definition b ON b.id=k.bot_id"
                    " WHERE k.tenant_id=CAST(:t AS uuid) AND k.revoked_at IS NULL"
                    " ORDER BY k.created_at DESC"
                ),
                {"t": tenant_id},
            )
        )
        .mappings()
        .all()
    )
    return [
        PortalKeyOut(
            id=str(r["id"]),
            bot_key=str(r["bot_key"]),
            name=str(r["name"]),
            created_at=r["created_at"].isoformat() if r.get("created_at") else None,
            last_used_at=(r["last_used_at"].isoformat() if r.get("last_used_at") else None),
        )
        for r in rows
    ]


@router.get(
    "/portal/invoices",
    response_model=list[InvoiceOut],
    dependencies=[Depends(require_perm(PORTAL))],
)
async def portal_invoices(
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> list[InvoiceOut]:
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    rows = await SqlBillingRepo(session).list_invoices(tenant_id=tenant_id)
    return [
        InvoiceOut(
            id=str(r["id"]),
            period=str(r["period"]),
            amount=int(r["amount"]),
            status=str(r["status"]),
            created_at=r["created_at"].isoformat() if r.get("created_at") else None,
        )
        for r in rows
    ]


@router.get(
    "/portal/invoices/{invoice_id}",
    response_model=InvoiceDetailOut,
    dependencies=[Depends(require_perm(PORTAL))],
)
async def portal_invoice_detail(
    invoice_id: str,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> InvoiceDetailOut:
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    row = await SqlBillingRepo(session).get_invoice(invoice_id=invoice_id, tenant_id=tenant_id)
    if row is None:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    return InvoiceDetailOut(
        id=str(row["id"]),
        period=str(row["period"]),
        amount=int(row["amount"]),
        status=str(row["status"]),
        usage_json=dict(row["usage_json"]),
        created_at=row["created_at"].isoformat() if row.get("created_at") else None,
    )


# ── 门户支付(035) ──


@router.post(
    "/portal/invoices/{invoice_id}/payments",
    status_code=201,
    response_model=PaymentOut,
    dependencies=[Depends(require_perm(PORTAL))],
)
async def create_payment(
    invoice_id: str,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> PaymentOut:
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    svc = _svc(session, str(user["sub"]))
    try:
        row = await svc.create_payment(invoice_id=invoice_id, tenant_id=tenant_id)
        await session.commit()
    except (BillingNotFound, PaymentConflict, PaymentChannelClosed, BillingValidationError) as e:
        raise _pay_http(e) from None
    return _payment_out(row)


@router.get(
    "/portal/payments/{payment_id}",
    response_model=PaymentOut,
    dependencies=[Depends(require_perm(PORTAL))],
)
async def get_portal_payment(
    payment_id: str,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> PaymentOut:
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    svc = _svc(session, str(user["sub"]))
    try:
        row = await svc.get_portal_payment(payment_id=payment_id, tenant_id=tenant_id)
        await session.commit()  # 惰性过期落库
    except BillingNotFound as e:
        raise _pay_http(e) from None
    return _payment_out(row)


@router.post("/portal/payments/{payment_id}/cancel", dependencies=[Depends(require_perm(PORTAL))])
async def cancel_payment(
    payment_id: str,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> dict[str, bool]:
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    svc = _svc(session, str(user["sub"]))
    try:
        await svc.cancel_payment(payment_id=payment_id, tenant_id=tenant_id)
        await session.commit()
    except (BillingNotFound, PaymentConflict) as e:
        raise _pay_http(e) from None
    return {"ok": True}


@router.post(
    "/portal/payments/{payment_id}/mock-simulate",
    response_model=ReconcileOut,
    dependencies=[Depends(require_perm(PORTAL))],
)
async def mock_simulate(
    payment_id: str,
    body: MockSimulateIn,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> ReconcileOut:
    """mock 通道「支付动作」入口(035 design D1):服务端真实生成签名后
    走统一核销——收银台按钮唯一合法路径。"""
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    svc = _svc(session, str(user["sub"]))
    try:
        out = await svc.mock_simulate(
            payment_id=payment_id, tenant_id=tenant_id, result=body.result
        )
        await session.commit()
    except (BillingNotFound, PaymentChannelClosed) as e:
        raise _pay_http(e) from None
    return ReconcileOut(idempotent=bool(out["idempotent"]), status=str(out["status"]))


# ── 统一回调(035;无登录态,验签即鉴权,031 webhook 同构) ──


@router.post("/billing/payments/callback/{channel}", response_model=ReconcileOut)
async def payment_callback(
    channel: str,
    body: CallbackIn,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ReconcileOut:
    svc = _svc(session)
    try:
        out = await svc.reconcile_payment(
            channel=channel.upper(),
            payment_id=str(body.payment_id),
            trade_no=body.trade_no,
            result=body.result,
            amount=body.amount,
            sign=body.sign,
        )
        await session.commit()
    except (
        BillingNotFound,
        PaymentBadSignature,
        PaymentChannelClosed,
        BillingValidationError,
    ) as e:
        # 拒前落审计(034 R1-S1 同款:伪签/金额不符审计须持久,不随 4xx 回滚)
        await session.commit()
        raise _pay_http(e) from None
    return ReconcileOut(idempotent=bool(out["idempotent"]), status=str(out["status"]))


# ── 管理面:账单作废(035) ──


@router.post("/billing/invoices/{invoice_id}/void", dependencies=[Depends(require_perm(MANAGE))])
async def void_invoice(
    invoice_id: str,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, bool]:
    svc = _svc(session, str(user["sub"]))
    try:
        await svc.void_invoice(invoice_id=invoice_id)
        await session.commit()
    except (BillingNotFound, PaymentConflict) as e:
        raise _pay_http(e) from None
    return {"ok": True}
