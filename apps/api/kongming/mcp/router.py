"""mcp 路由(028,薄适配层):/system/mcp/* 管理端点 + /mcp 入站端点。

管理端点挂 require_perm("system:mcp:manage"),租户视角(X-Tenant-Id
白名单,与 chat 同构——复用 ChatRepository 租户解析,toolcall router
已有跨域装配先例);跨租户/不存在 404 同形;领域错误映射 400/404/409。
/mcp 入站不经 JWT——api-key 鉴权(025 体系),独立鉴权段。
"""

import logging
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.repository import SqlOperLogRepo
from kongming.auth.router import current_user, require_perm
from kongming.chat.repository import ChatRepository
from kongming.config import get_settings
from kongming.datasource.cipher import get_cipher
from kongming.db import get_session
from kongming.mcp.client import HttpMcpCaller
from kongming.mcp.models import McpServerExists, McpServerNotFound
from kongming.mcp.repository import SqlMcpRepo
from kongming.mcp.service import McpService, McpValidationError

log = logging.getLogger(__name__)

router = APIRouter(prefix="/system/mcp", tags=["mcp"])

MANAGE = "system:mcp:manage"


class _OperLogger:
    """McpOperLogger 端口绑定(actor 请求期横切;与业务同事务,007 惯例)。"""

    def __init__(self, session: AsyncSession, actor_id: str) -> None:
        self._repo = SqlOperLogRepo(session)
        self._actor = actor_id

    async def log(self, *, action: str, target: str, detail: dict[str, object]) -> None:
        await self._repo.add(self._actor, action, target, detail)


def _resolve_tenant(
    user: dict[str, object], session: AsyncSession, header: str | None
) -> tuple[Any, str | None]:
    """X-Tenant-Id 白名单解析(与 chat 同构);返回 (awaitable, 显式 key 或 None)。"""
    repo = ChatRepository(session)
    uid = str(user["sub"])
    if header is not None:
        return repo.find_bound_tenant(user_id=uid, tenant_key=header), header
    return repo.find_default_tenant(user_id=uid), None


async def _tenant_or_403(user: dict[str, object], session: AsyncSession, header: str | None) -> str:
    pending, explicit = _resolve_tenant(user, session, header)
    found = await pending
    if found is None:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN")
    if explicit is not None:
        return str(found)
    return str(found[0])


def get_mcp_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[dict[str, object], Depends(current_user)],
) -> McpService:
    settings = get_settings()
    return McpService(
        SqlMcpRepo(session, get_cipher(settings.datasource_encrypt_key)),
        HttpMcpCaller(timeout_sec=settings.mcp_outbound_timeout_sec),
        _OperLogger(session, str(user["sub"])),
    )


# ── 传输层模型 ──


class McpToolEntryOut(BaseModel):
    """发现快照条目(掩码面随 server 返回);
    input_schema 命名避 BaseModel.schema 阴影(pydantic 告警)。"""

    name: str
    qualified: str
    description: str = ""
    input_schema: dict[str, Any] = {}
    read_only: bool = False


class McpServerOut(BaseModel):
    id: str
    server_key: str
    name: str
    endpoint: str
    enabled: bool
    header_keys: list[str] = []
    tools: list[McpToolEntryOut] = []
    last_sync_at: str | None = None
    last_sync_error: str = ""


class McpTestOut(BaseModel):
    ok: bool
    kind: str | None = None
    detail: str | None = None
    tools: int | None = None


class McpSyncOut(BaseModel):
    ok: bool
    kind: str | None = None
    detail: str | None = None
    tools: int | None = None
    conflicts: list[str] | None = None


class McpServerIn(BaseModel):
    server_key: str = Field(min_length=2, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    endpoint: str = Field(min_length=8, max_length=512)
    headers: dict[str, str] = {}


class McpServerUpdateIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    endpoint: str = Field(min_length=8, max_length=512)
    # 值 None=保持现值;空串=删除该键(D2 编辑语义)
    headers: dict[str, str | None] = {}


def _dict_or_empty(v: Any) -> dict[str, Any]:
    # isinstance 收窄产物键型未知,收窄为目标键型(仅类型断言)
    return cast("dict[str, Any]", v) if isinstance(v, dict) else {}


def _row_out(row: dict[str, Any]) -> McpServerOut:
    tools_raw = cast("list[object]", row.get("tools") or [])
    # isinstance 守卫保留:先过滤出 dict 形态条目,再收窄键型供字段读取(仅类型断言)
    tools_entries: list[dict[str, Any]] = [e for e in tools_raw if isinstance(e, dict)]
    return McpServerOut(
        id=str(row["id"]),
        server_key=str(row["server_key"]),
        name=str(row["name"]),
        endpoint=str(row["endpoint"]),
        enabled=bool(row["enabled"]),
        header_keys=[str(k) for k in cast("list[object]", row.get("header_keys") or [])],
        tools=[
            McpToolEntryOut(
                name=str(e.get("name") or ""),
                qualified=str(e.get("qualified") or ""),
                description=str(e.get("description") or ""),
                input_schema=_dict_or_empty(e.get("schema")),
                read_only=bool(e.get("read_only")),
            )
            for e in tools_entries
        ],
        last_sync_at=(row["last_sync_at"].isoformat() if row.get("last_sync_at") else None),
        last_sync_error=str(row.get("last_sync_error") or ""),
    )


# ── 管理端点 ──


@router.get("", response_model=list[McpServerOut], dependencies=[Depends(require_perm(MANAGE))])
async def list_servers(
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[McpService, Depends(get_mcp_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> list[McpServerOut]:
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    return [_row_out(r) for r in await svc.repo_list_masked(tenant_id=tenant_id)]


@router.post(
    "",
    status_code=201,
    response_model=McpServerOut,
    dependencies=[Depends(require_perm(MANAGE))],
)
async def create_server(
    body: McpServerIn,
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[McpService, Depends(get_mcp_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> McpServerOut:
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    try:
        sid = await svc.create(
            tenant_id=tenant_id,
            server_key=body.server_key,
            name=body.name,
            endpoint=body.endpoint,
            headers=body.headers,
        )
        # 创建即同步(028 spec「保存即同步」);失败不炸创建(降级记错误)
        await svc.sync(server_id=sid, tenant_id=tenant_id)
        await svc.commit()
    except McpValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    except McpServerExists:
        raise HTTPException(status_code=409, detail="MCP_SERVER_EXISTS") from None
    row = await svc.get(server_id=sid, tenant_id=tenant_id)
    if row is None:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    return _row_out(row)


@router.put(
    "/{server_id}",
    response_model=McpServerOut,
    dependencies=[Depends(require_perm(MANAGE))],
)
async def update_server(
    server_id: str,
    body: McpServerUpdateIn,
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[McpService, Depends(get_mcp_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> McpServerOut:
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    try:
        await svc.update(
            server_id=server_id,
            tenant_id=tenant_id,
            name=body.name,
            endpoint=body.endpoint,
            headers=body.headers,
        )
        await svc.sync(server_id=server_id, tenant_id=tenant_id)
        await svc.commit()
    except McpValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    except McpServerNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    row = await svc.get(server_id=server_id, tenant_id=tenant_id)
    if row is None:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    return _row_out(row)


@router.post("/{server_id}/enable", dependencies=[Depends(require_perm(MANAGE))])
async def enable_server(
    server_id: str,
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[McpService, Depends(get_mcp_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> dict[str, bool]:
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    try:
        await svc.set_enabled(server_id=server_id, tenant_id=tenant_id, enabled=True)
        await svc.commit()
    except McpServerNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    return {"ok": True}


@router.post("/{server_id}/disable", dependencies=[Depends(require_perm(MANAGE))])
async def disable_server(
    server_id: str,
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[McpService, Depends(get_mcp_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> dict[str, bool]:
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    try:
        await svc.set_enabled(server_id=server_id, tenant_id=tenant_id, enabled=False)
        await svc.commit()
    except McpServerNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    return {"ok": True}


@router.delete("/{server_id}", dependencies=[Depends(require_perm(MANAGE))])
async def delete_server(
    server_id: str,
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[McpService, Depends(get_mcp_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> dict[str, bool]:
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    try:
        await svc.delete(server_id=server_id, tenant_id=tenant_id)
        await svc.commit()
    except McpServerNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    return {"ok": True}


@router.post(
    "/{server_id}/test",
    response_model=McpTestOut,
    dependencies=[Depends(require_perm(MANAGE))],
)
async def test_server(
    server_id: str,
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[McpService, Depends(get_mcp_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> McpTestOut:
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    try:
        return McpTestOut(**await svc.test_connection(server_id=server_id, tenant_id=tenant_id))
    except McpServerNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None


@router.post(
    "/{server_id}/sync",
    response_model=McpSyncOut,
    dependencies=[Depends(require_perm(MANAGE))],
)
async def sync_server(
    server_id: str,
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[McpService, Depends(get_mcp_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> McpSyncOut:
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    try:
        out = McpSyncOut(**await svc.sync(server_id=server_id, tenant_id=tenant_id))
        await svc.commit()
    except McpServerNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    return out
