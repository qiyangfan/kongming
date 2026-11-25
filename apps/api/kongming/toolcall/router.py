"""工具执行、写操作确认与工具审计查询路由。

内部执行端点由内部令牌保护(ADR-002);确认与管理端点各自执行权限与
归属校验。端口适配与工厂簇见 adapters/dependencies。
"""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.deps import CurrentUser, require_perm
from kongming.auth.models import AuthenticatedUser
from kongming.chat.repository import ChatRepository
from kongming.db import get_session
from kongming.toolcall import dependencies as _deps
from kongming.toolcall.dependencies import (
    build_mcp_service,
    get_tool_service,
    sync_runtime_disabled,
)
from kongming.toolcall.models import (
    ConfirmNotFound,
    TenantBindingForbidden,
    TenantNotFound,
)
from kongming.toolcall.registry import REGISTRY
from kongming.toolcall.repository import SqlToolCallRepo, user_has_all_scope
from kongming.toolcall.schemas import (
    CONFIRM_OUT_STATUS,
    ToolAuditPageOut,
    ToolConfirmIn,
    ToolConfirmOut,
    ToolExecuteIn,
    ToolExecuteOut,
    ToolItemOut,
    ToolListOut,
    ToolResolveConfirmIn,
    to_tool_audit_item,
    to_tool_execute_out,
)
from kongming.toolcall.service import ConfirmOutcome, ToolCallService
from kongming.toolgateway.gateway import ToolGateway

# 内部面(守门由 main 全局 middleware 承担)与管理面/用户面三个 router
internal_router = APIRouter(prefix="/internal/tool", tags=["internal"])
router = APIRouter(prefix="/system/tools", tags=["toolcall"])
chat_router = APIRouter(prefix="/chat", tags=["chat"])


async def _mcp_tenant_or_none(
    user: AuthenticatedUser, session: AsyncSession, header: str | None
) -> str | None:
    repo = ChatRepository(session)
    uid = user.user_id
    if header is not None:
        return await repo.find_bound_tenant(user_id=uid, tenant_key=header)
    default = await repo.find_default_tenant(user_id=uid)
    return None if default is None else str(default[0])


# ── 内部面:通用工具执行回调 ──


@internal_router.post("/execute", response_model=ToolExecuteOut)
async def tool_execute(
    req: ToolExecuteIn,
    svc: Annotated[ToolCallService, Depends(get_tool_service)],
) -> ToolExecuteOut:
    try:
        outcome = await svc.execute_tool(
            trace_id=req.trace_id,
            tenant_key=req.tenant_key,
            datasource_key=req.datasource_key,
            user_id=str(req.user_id),
            tool_name=req.tool_name,
            args=req.args,
            idempotency_key=req.idempotency_key,
            channel=req.channel,
        )
    except TenantNotFound:
        raise HTTPException(status_code=404, detail="TENANT_NOT_FOUND") from None
    except TenantBindingForbidden:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None
    return to_tool_execute_out(outcome)


@internal_router.post("/resolve-confirm", response_model=ToolExecuteOut)
async def tool_resolve_confirm(
    req: ToolResolveConfirmIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    svc: Annotated[ToolCallService, Depends(get_tool_service)],
) -> ToolExecuteOut:
    outcome = await svc.resolve_for_task(
        user_id=str(req.user_id),
        tenant_key=req.tenant_key,
        confirm_id=req.confirm_id,
        decision=req.decision,
    )
    await session.commit()
    return to_tool_execute_out(outcome)


# ── 用户面:写工具人工确认 ──


@chat_router.post("/tool-confirm", response_model=ToolConfirmOut)
async def tool_confirm(
    req: ToolConfirmIn,
    user: CurrentUser,
    svc: Annotated[ToolCallService, Depends(get_tool_service)],
    _: Annotated[None, Depends(require_perm("chat:execute"))],
) -> ToolConfirmOut:
    try:
        outcome: ConfirmOutcome = await svc.confirm(
            user_id=user.user_id,
            confirm_id=str(req.confirm_id),
            decision=req.decision,
            session_id=str(req.session_id),
        )
    except ConfirmNotFound:
        # 不存在/不属本人/会话不属本人同形 404(不暴露存在性)
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    except TenantBindingForbidden:
        # 确认时 L1 复校——挂起后用户被解绑租户,拒绝执行
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None
    return ToolConfirmOut(
        status=CONFIRM_OUT_STATUS[outcome.status],  # type: ignore[arg-type]
        reason=outcome.reason,
        result=outcome.result,
    )


# ── 管理面:工具清单与审计查询 ──


@router.get("", response_model=ToolListOut)
async def list_tools(
    _: Annotated[None, Depends(require_perm("system:tool:manage"))],
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> ToolListOut:
    items = [
        ToolItemOut(
            name=d.name,
            description=d.description,
            perm_code=d.perm_code,
            write=d.write,
            require_confirm=d.require_confirm,
        )
        for d in REGISTRY.values()
    ]
    # 合并租户 MCP 动态工具(溯源 server_key);租户未绑定 → 仅静态集
    tenant_id = await _mcp_tenant_or_none(user, session, x_tenant_id)
    if tenant_id is not None:
        mcp_svc = build_mcp_service(session)
        for e in await mcp_svc.list_dynamic_tools(tenant_id=tenant_id):
            read_only = bool(e.get("read_only"))
            items.append(
                ToolItemOut(
                    name=str(e.get("qualified") or ""),
                    description=str(e.get("description") or ""),
                    perm_code="tool:mcp:use",
                    write=not read_only,
                    require_confirm=not read_only,
                    server_key=str(e.get("server_key") or ""),
                )
            )
    return ToolListOut(tools=items)


# ── 域治理面:清单摘要(不含凭证与白名单明细)+ 运行时启停(重启随部署清单复位)──


def _summary_ids(gw: ToolGateway | None) -> set[str]:
    return {str(d["id"]) for d in (gw.summary() if gw else [])}


@router.get(
    "/domains",
    dependencies=[Depends(require_perm("system:tool:manage"))],
)
async def list_domains() -> list[dict[str, object]]:
    """治理面清单摘要(不含凭证与白名单明细;disabled 反映本进程运行时状态)。"""
    gw = _deps.get_gateway()
    return gw.summary() if gw else []


@router.post("/domains/{domain_id}/disable", status_code=204)
async def disable_domain(
    domain_id: str,
    user: CurrentUser,
    _: Annotated[None, Depends(require_perm("system:tool:manage"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """停用工具域并持久化;本进程立即生效。"""
    gw = _deps.get_gateway()
    if gw is None or domain_id not in _summary_ids(gw):
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    gw.disable(domain_id)
    sync_runtime_disabled()
    from kongming.toolcall.repository import upsert_domain_state

    await upsert_domain_state(session, domain_id=domain_id, disabled=True, updated_by=user.user_id)
    await session.commit()


@router.post("/domains/{domain_id}/enable", status_code=204)
async def enable_domain(
    domain_id: str,
    user: CurrentUser,
    _: Annotated[None, Depends(require_perm("system:tool:manage"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """启用工具域并持久化;本进程立即生效。"""
    gw = _deps.get_gateway()
    if gw is None or domain_id not in _summary_ids(gw):
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    gw.enable(domain_id)
    sync_runtime_disabled()
    from kongming.toolcall.repository import upsert_domain_state

    await upsert_domain_state(session, domain_id=domain_id, disabled=False, updated_by=user.user_id)
    await session.commit()


@router.get("/audit/page", response_model=ToolAuditPageOut)
async def tool_audit_page(
    user: CurrentUser,
    _: Annotated[None, Depends(require_perm("system:tool:manage"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
    tool_name: Annotated[str | None, Query(max_length=64)] = None,
    status: Annotated[str | None, Query(max_length=24)] = None,
    user_id: Annotated[uuid.UUID | None, Query()] = None,
    tenant_id: Annotated[uuid.UUID | None, Query()] = None,
    start: Annotated[datetime | None, Query()] = None,
    end: Annotated[datetime | None, Query()] = None,
) -> ToolAuditPageOut:
    viewer_id = user.user_id
    # 行级可见性(spec, 同口径):非 ALL 角色强制本人(覆盖传值)
    all_scope = await user_has_all_scope(session, viewer_id)
    effective_user = (None if user_id is None else str(user_id)) if all_scope else viewer_id
    items, total = await SqlToolCallRepo(session).page_audit(
        page=page,
        size=size,
        tool_name=tool_name,
        status=status,
        user_id=effective_user,
        tenant_id=None if tenant_id is None else str(tenant_id),
        start=start,
        end=end,
    )
    return ToolAuditPageOut(
        items=[to_tool_audit_item(it) for it in items], total=total, page=page, size=size
    )
