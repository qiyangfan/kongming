"""guardrail 路由(prompt-guardrails spec):规则清单/覆盖更新/审计分页。

全部端点挂 system:guardrail:manage(仅 SUPER_ADMIN 种子覆盖);
行级可见性:非 ALL scope 强制只看本人记录。
"""

import logging
import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.deps import CurrentUser, require_perm
from kongming.db import get_session
from kongming.guardrail.models import MASKERS
from kongming.guardrail.repository import SqlGuardrailRepo
from kongming.guardrail.rules import REGISTRY
from kongming.toolcall.repository import user_has_all_scope

log = logging.getLogger(__name__)

router = APIRouter(prefix="/system/guardrails", tags=["guardrail"])


# ── 规则清单(注册表 + 覆盖合并视图) ──


class GuardrailRuleOut(BaseModel):
    name: str
    guardrail_type: str
    action: str
    description: str
    enabled: bool
    threshold: int | None
    overridden: bool  # 是否有库覆盖行
    maskable: bool  # 有脱敏器(仅此类规则允许 action=masked)


class RuleListOut(BaseModel):
    rules: list[GuardrailRuleOut]


@router.get("/rules", response_model=RuleListOut)
async def list_rules(
    _: Annotated[None, Depends(require_perm("system:guardrail:manage"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RuleListOut:
    overrides = await SqlGuardrailRepo(session).list_overrides()
    items: list[GuardrailRuleOut] = []
    for rule in REGISTRY:
        ov = overrides.get(rule.name)
        items.append(
            GuardrailRuleOut(
                name=rule.name,
                guardrail_type=rule.guardrail_type,
                action=str((ov or {}).get("action") or rule.action),
                description=rule.description,
                enabled=bool((ov or {}).get("enabled", rule.enabled)),
                threshold=(ov or {}).get("threshold", rule.threshold),
                overridden=ov is not None,
                maskable=rule.name in MASKERS,
            )
        )
    return RuleListOut(rules=items)


class RuleUpdateIn(BaseModel):
    enabled: bool
    action: Literal["blocked", "masked"] | None = None
    threshold: int | None = Field(default=None, ge=1, le=100)


@router.put("/rules/{rule_name}", status_code=204)
async def update_rule(
    rule_name: str,
    req: RuleUpdateIn,
    user: CurrentUser,
    _: Annotated[None, Depends(require_perm("system:guardrail:manage"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    rule = next((r for r in REGISTRY if r.name == rule_name), None)
    if rule is None:
        raise HTTPException(status_code=404, detail="RULE_NOT_FOUND") from None
    # 覆盖合法性——threshold 仅 tool_intent 类;action=masked 仅有脱敏器规则
    if req.threshold is not None and rule.guardrail_type != "tool_intent":
        raise HTTPException(status_code=400, detail="THRESHOLD_NOT_APPLICABLE") from None
    if req.action == "masked" and rule.name not in MASKERS:
        raise HTTPException(status_code=400, detail="MASK_NOT_APPLICABLE") from None
    await SqlGuardrailRepo(session).upsert_override(
        rule_name=rule_name,
        enabled=req.enabled,
        action=req.action,
        threshold=req.threshold,
        updated_by=user.user_id,
    )


# ── 审计分页 ──


class GuardrailAuditItem(BaseModel):
    id: str
    trace_id: str
    user_id: str | None
    tenant_id: str
    guardrail_type: str
    rule_name: str
    action: str
    content_digest: str | None
    created_at: datetime


class GuardrailAuditPageOut(BaseModel):
    items: list[GuardrailAuditItem]
    total: int
    page: int
    size: int


def _item(r: dict[str, object]) -> GuardrailAuditItem:
    return GuardrailAuditItem(
        id=str(r["id"]),
        trace_id=str(r["trace_id"]),
        user_id=None if r["user_id"] is None else str(r["user_id"]),
        tenant_id=str(r["tenant_id"]),
        guardrail_type=str(r["guardrail_type"]),
        rule_name=str(r["rule_name"]),
        action=str(r["action"]),
        content_digest=None if r["content_digest"] is None else str(r["content_digest"]),
        created_at=r["created_at"],  # type: ignore[arg-type]  # datetime 直传
    )


@router.get("/audit/page", response_model=GuardrailAuditPageOut)
async def guardrail_audit_page(
    user: CurrentUser,
    _: Annotated[None, Depends(require_perm("system:guardrail:manage"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
    guardrail_type: Annotated[Literal["input", "output", "tool_intent"] | None, Query()] = None,
    rule_name: Annotated[str | None, Query(max_length=64)] = None,
    action: Annotated[Literal["blocked", "masked"] | None, Query()] = None,
    user_id: Annotated[uuid.UUID | None, Query()] = None,
    start: Annotated[datetime | None, Query()] = None,
    end: Annotated[datetime | None, Query()] = None,
) -> GuardrailAuditPageOut:
    viewer_id = user.user_id
    all_scope = await user_has_all_scope(session, viewer_id)
    effective_user = (None if user_id is None else str(user_id)) if all_scope else viewer_id
    items, total = await SqlGuardrailRepo(session).page_audit(
        page=page,
        size=size,
        guardrail_type=guardrail_type,
        rule_name=rule_name,
        action=action,
        user_id=effective_user,
        start=start,
        end=end,
    )
    return GuardrailAuditPageOut(
        items=[_item(it) for it in items], total=total, page=page, size=size
    )
