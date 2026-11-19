"""toolcall 请求/响应模型与结果投影(模型类名冻结:OpenAPI components 单源)。"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from kongming.toolcall.models import ToolOutcome


class ToolExecuteIn(BaseModel):
    """Agent 面 → 控制面的工具执行回调(ADR-002:Agent 不持凭证,只传标识与文本)。"""

    trace_id: str = Field(min_length=1, max_length=64)
    tenant_key: str = Field(min_length=1, max_length=64)
    datasource_key: str = Field(default="default", pattern=r"^[a-z][a-z0-9_]{1,63}$")
    user_id: uuid.UUID  # 必填:工具权限点校验需要真实主体(无系统调用豁免,spec)
    tool_name: str = Field(min_length=1, max_length=64)
    args: dict[str, object] = {}
    idempotency_key: str | None = Field(default=None, max_length=128)
    # 渠道标记(审计可溯;agent 透传)
    channel: str = Field(default="chat", max_length=24)


class ToolExecuteOut(BaseModel):
    """三态+确认挂起:executed(含结果)/ rejected(防御拒绝,含原因)/
    error(执行异常)/ rate_limited(限流)/ confirm_required(写工具待确认)。"""

    status: Literal["executed", "rejected", "error", "rate_limited", "confirm_required"]
    reason: str | None = None
    result: dict[str, object] | None = None
    confirm_id: str | None = None
    duration_ms: int = 0


_OUT_STATUS: dict[str, str] = {
    "EXECUTED": "executed",
    "REJECTED": "rejected",
    "ERROR": "error",
    "RATE_LIMITED": "rate_limited",
    "PENDING_CONFIRM": "confirm_required",
}


def to_tool_execute_out(outcome: ToolOutcome) -> ToolExecuteOut:
    """执行终态 → 内部回调响应投影。"""
    return ToolExecuteOut(
        status=_OUT_STATUS[outcome.status],  # type: ignore[arg-type]  # 五态全覆盖
        reason=outcome.reason,
        result=outcome.result,
        confirm_id=outcome.confirm_id,
        duration_ms=outcome.duration_ms,
    )


class ToolResolveConfirmIn(BaseModel):
    """任务通道确认兑现载荷(graph-checkpoint;决策权威=任务域确认行,
    本端点只做兑换与全量复校)。"""

    tenant_key: str
    user_id: uuid.UUID
    confirm_id: str = Field(min_length=1, max_length=64)
    decision: Literal["approve", "reject"]


class ToolConfirmIn(BaseModel):
    confirm_id: uuid.UUID
    decision: Literal["confirm", "reject"]
    session_id: uuid.UUID


class ToolConfirmOut(BaseModel):
    status: Literal["executed", "confirm_rejected", "confirm_expired", "rejected", "error"]
    reason: str | None = None
    result: dict[str, object] | None = None


CONFIRM_OUT_STATUS = {
    "executed": "executed",
    "confirm_rejected": "confirm_rejected",
    "confirm_expired": "confirm_expired",
    "rejected": "rejected",
    "error": "error",
}


class ToolItemOut(BaseModel):
    name: str
    description: str
    perm_code: str
    write: bool
    require_confirm: bool
    enabled: bool = True  # 注册表即启用集(配置化启停随运营诉求评估)
    server_key: str | None = None  # MCP 工具溯源(内置为 None)


class ToolListOut(BaseModel):
    tools: list[ToolItemOut]


class ToolAuditItem(BaseModel):
    id: str
    trace_id: str
    user_id: str
    tenant_id: str
    tool_name: str
    args_digest: str | None
    status: str
    error_reason: str | None
    duration_ms: int | None
    datasource_key: str | None
    # 域标签读面投影(存量行/内置工具为 NULL→None);可选字段,老客户端兼容
    domain: str | None = None
    idempotency_key: str | None
    expires_at: datetime | None
    created_at: datetime


class ToolAuditPageOut(BaseModel):
    items: list[ToolAuditItem]
    total: int
    page: int
    size: int


def to_tool_audit_item(r: dict[str, object]) -> ToolAuditItem:
    """审计行 → 查询响应投影。"""
    return ToolAuditItem(
        id=str(r["id"]),
        trace_id=str(r["trace_id"]),
        user_id=str(r["user_id"]),
        tenant_id=str(r["tenant_id"]),
        tool_name=str(r["tool_name"]),
        args_digest=None if r["args_digest"] is None else str(r["args_digest"]),
        status=str(r["status"]),
        error_reason=None if r["error_reason"] is None else str(r["error_reason"]),
        duration_ms=None if r["duration_ms"] is None else int(str(r["duration_ms"])),
        datasource_key=None if r["datasource_key"] is None else str(r["datasource_key"]),
        domain=None if r.get("domain") is None else str(r["domain"]),
        idempotency_key=None if r["idempotency_key"] is None else str(r["idempotency_key"]),
        expires_at=r["expires_at"],  # type: ignore[arg-type]  # datetime|None 直传
        created_at=r["created_at"],  # type: ignore[arg-type]  # datetime 直传
    )
