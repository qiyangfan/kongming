"""billing 请求/响应模型与支付行投影(模型类名冻结:OpenAPI components 单源)。"""

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field


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
    payment_status: str | None = None  # 最新支付单状态(无单 null)


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


def to_payment_out(row: dict[str, Any]) -> PaymentOut:
    """支付行 → 响应投影。"""
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


class PolicyIn(BaseModel):
    tenant_id: uuid.UUID  # 形态校验:非 UUID 由 pydantic 422,不炸 500
    dimension: str = Field(pattern="^(TOKENS|CALLS|COST)$")
    limit_value: int = Field(ge=0)
    warn_pct: int = Field(default=80, ge=1, le=100)
    # 多级配额(scope_ref=BOT/USER/TEAM 时必填对应资源 id)
    scope: str = Field(default="TENANT", pattern="^(TENANT|BOT|USER|TEAM)$")
    scope_ref: uuid.UUID | None = None


class PlanIn(BaseModel):
    plan_key: str = Field(min_length=2, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    monthly_tokens: int = Field(default=0, ge=0)
    monthly_calls: int = Field(default=0, ge=0)
    price_per_mtok: int = Field(default=0, ge=0)
    price_per_call: int = Field(default=0, ge=0)


class PlanPatchIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    monthly_tokens: int = Field(default=0, ge=0)
    monthly_calls: int = Field(default=0, ge=0)
    price_per_mtok: int = Field(default=0, ge=0)
    price_per_call: int = Field(default=0, ge=0)


class TenantBriefOut(BaseModel):
    id: str
    tenant_key: str
    tenant_name: str


class BotBriefOut(BaseModel):
    id: str
    bot_key: str
    name: str


class UserBriefOut(BaseModel):
    id: str
    username: str
    nickname: str


class TeamBriefOut(BaseModel):
    id: str
    team_key: str
    name: str


class BindPlanIn(BaseModel):
    tenant_id: str = Field(min_length=1)
    plan_id: str | None = None
