"""memory 路由(022,agent-memory spec「记忆自管理」):本人记忆四端点。

登录即可(无新权限点);归属收口=user 必为本人 + X-Tenant-Id 白名单复校
(与 chat 同构);越权 404 同形。
"""

import logging
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.repository import SqlOperLogRepo
from kongming.auth.router import current_user
from kongming.db import get_session
from kongming.memory.repository import SqlMemoryRepo
from kongming.memory.service import (
    MemoryNotFound,
    MemoryService,
    SensitiveContentRejected,
)
from kongming.rag.agent_callback import get_agent_rag_client
from kongming.redis_client import get_redis

log = logging.getLogger(__name__)

router = APIRouter(prefix="/memory", tags=["memory"])


def _get_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MemoryService:
    # embedding 通道复用 009 单例(013);operlog 复用 auth 域(015 ip 链路自动覆盖)
    from kongming.team.repository import SqlTeamRepo

    class _TeamRoles:
        def __init__(self, s: AsyncSession) -> None:
            self._r = SqlTeamRepo(s)

        async def role_map(self, user_id: str) -> dict[str, str]:
            return await self._r.role_map(user_id=user_id)

    return MemoryService(
        SqlMemoryRepo(session),
        get_agent_rag_client(get_redis()),
        SqlOperLogRepo(session),
        _TeamRoles(session),
    )


async def _tenant_id(session: AsyncSession, user_id: str, tenant_key: str | None) -> str:
    """X-Tenant-Id 白名单复校(与 chat._resolve_tenant 同构);缺省取默认。
    SQL 单一真理源=kongming.tenant.resolve_tenant(B1-B10 收口)。"""
    from kongming.tenant import resolve_tenant

    resolved = await resolve_tenant(session, user_id=user_id, tenant_key=tenant_key)
    if resolved is None:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None
    return resolved[0]


class MemoryItemOut(BaseModel):
    id: str
    content: str
    category: str
    confidence: float
    source: str
    dormant: bool
    last_hit_at: str | None
    created_at: str
    team_id: str | None = None  # 036⑥:归属回传(None=个人;契约纯增)
    kind: str = "memory"  # 059:列表分组(kind=category;契约纯增)
    legacy: bool = False  # 059:true=停写前 IMPLICIT 自由文本


class PrefsIn(BaseModel):
    # 059 闭合 schema:未知键 422(与 put_prefs.reject_unknown_keys 同语义)
    model_config = ConfigDict(extra="forbid")

    preferred_datasource: str | None = None
    default_time_range: str | None = None
    metric_aliases: list[str] | None = None
    chart_pref: str | None = None
    language: str | None = None
    timezone: str | None = None
    display_style: str | None = None


class PrefsOut(BaseModel):
    prefs: dict[str, object]


@router.get("/prefs", response_model=PrefsOut)
async def get_memory_prefs(
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[MemoryService, Depends(_get_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> PrefsOut:
    """059:读取本人闭合偏好槽位。"""
    from kongming.memory.prefs import SLOT_KEYS

    tid = await _tenant_id(session, str(user["sub"]), x_tenant_id)
    prefs = await svc.get_prefs(user_id=str(user["sub"]), tenant_id=tid)
    return PrefsOut(prefs={k: prefs[k] for k in SLOT_KEYS if k in prefs})


@router.put("/prefs", response_model=PrefsOut)
async def put_memory_prefs(
    body: PrefsIn,
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[MemoryService, Depends(_get_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> PrefsOut:
    """059:覆盖更新闭合槽位;未知键拒绝(闭合 schema)。"""
    raw = {k: v for k, v in body.model_dump().items() if v is not None}
    tid = await _tenant_id(session, str(user["sub"]), x_tenant_id)
    try:
        prefs = await svc.put_prefs(user_id=str(user["sub"]), tenant_id=tid, raw=raw)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return PrefsOut(prefs=prefs)


class MemoryListOut(BaseModel):
    items: list[MemoryItemOut]


def _item(r: dict[str, object]) -> MemoryItemOut:
    last_hit = r["last_hit_at"]
    created = r["created_at"]
    category = str(r["category"])
    return MemoryItemOut(
        id=str(r["id"]),
        content=str(r["content"]),
        category=category,
        confidence=float(str(r["confidence"])),
        source=str(r["source"]),
        dormant=bool(r["dormant"]),
        last_hit_at=last_hit.isoformat() if isinstance(last_hit, datetime) else None,
        created_at=created.isoformat() if isinstance(created, datetime) else str(created),
        team_id=str(r["team_id"]) if r.get("team_id") else None,  # 036⑥
        kind=category,
        legacy=str(r.get("source") or "").upper() == "IMPLICIT",
    )


class MemoryCreateIn(BaseModel):
    content: str = Field(min_length=1, max_length=500)
    team_id: str | None = None  # 032:团队记忆(限 OWNER/MEMBER 成员)


@router.post("", status_code=201)
async def create_memory(
    body: MemoryCreateIn,
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[MemoryService, Depends(_get_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    """显式写入(032):team_id 非空=团队记忆(OWNER/MEMBER 成员才允许);
    空=个人记忆(与「请记住」同路径)。"""
    tid = await _tenant_id(session, str(user["sub"]), x_tenant_id)
    if body.team_id:
        from kongming.team.repository import SqlTeamRepo

        # R1-J5:团队必须属于当前租户(跨租户写入 404 同形)
        team = await SqlTeamRepo(session).get_team(tenant_id=tid, team_id=body.team_id)
        if team is None:
            raise HTTPException(status_code=404, detail="NOT_FOUND") from None
        role = await SqlTeamRepo(session).get_member_role(
            team_id=body.team_id, user_id=str(user["sub"])
        )
        if role not in ("OWNER", "MEMBER"):
            raise HTTPException(status_code=403, detail="TEAM_ROLE_FORBIDDEN") from None
    try:
        await svc.form_explicit(
            user_id=str(user["sub"]),
            tenant_id=tid,
            content=body.content,
            team_id=body.team_id,
            # 管理端显式建条目:不走槽位分流(否则条目从列表消失)
            route_slot=False,
        )
        await session.commit()
    except SensitiveContentRejected:
        raise HTTPException(status_code=400, detail="MEMORY_SENSITIVE") from None
    return {"ok": "true"}


@router.get("", response_model=MemoryListOut)
async def list_memory(
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[MemoryService, Depends(_get_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> MemoryListOut:
    tid = await _tenant_id(session, str(user["sub"]), x_tenant_id)
    rows = await svc.list_mine(user_id=str(user["sub"]), tenant_id=tid)
    return MemoryListOut(items=[_item(r) for r in rows])


class MemoryUpdateIn(BaseModel):
    content: str = Field(min_length=1, max_length=500)
    category: str = Field(default="general", max_length=32)


@router.patch("/{memory_id}", status_code=204)
async def update_memory(
    memory_id: uuid.UUID,
    req: MemoryUpdateIn,
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[MemoryService, Depends(_get_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> None:
    tid = await _tenant_id(session, str(user["sub"]), x_tenant_id)
    try:
        await svc.update_mine(
            memory_id=str(memory_id),
            user_id=str(user["sub"]),
            tenant_id=tid,
            content=req.content,
            category=req.category,
        )
    except MemoryNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    except SensitiveContentRejected:
        raise HTTPException(status_code=400, detail="SENSITIVE_CONTENT") from None


@router.delete("/{memory_id}", status_code=204)
async def delete_memory(
    memory_id: uuid.UUID,
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[MemoryService, Depends(_get_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> None:
    tid = await _tenant_id(session, str(user["sub"]), x_tenant_id)
    try:
        await svc.delete_mine(memory_id=str(memory_id), user_id=str(user["sub"]), tenant_id=tid)
    except MemoryNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None


@router.get("/export")
async def export_memory(
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[MemoryService, Depends(_get_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> list[dict[str, object]]:
    tid = await _tenant_id(session, str(user["sub"]), x_tenant_id)
    rows = await svc.list_mine(user_id=str(user["sub"]), tenant_id=tid)
    return [
        {
            "content": r["content"],
            "category": r["category"],
            "source": r["source"],
            "dormant": r["dormant"],
            "created_at": (
                r["created_at"].isoformat()
                if isinstance(r["created_at"], datetime)
                else str(r["created_at"])
            ),
        }
        for r in rows
    ]
