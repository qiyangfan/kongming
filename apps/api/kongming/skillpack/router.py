"""skillpack 路由:管理 CRUD/启停(system:skill:manage;跨租户 404 同形)。"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.deps import current_user, require_perm
from kongming.auth.repository import SqlOperLogRepo
from kongming.db import get_session
from kongming.skillpack.repository import SqlSkillPackRepo
from kongming.skillpack.service import (
    MetricConflictError,
    SkillPackNotFound,
    SkillPackService,
)
from kongming.tenant import resolve_tenant

router = APIRouter(prefix="/system/skill", tags=["skill-pack"])
SKILL_MANAGE = Depends(require_perm("system:skill:manage"))


async def _tenant_id(session: AsyncSession, user_id: str, tenant_key: str | None) -> str:
    resolved = await resolve_tenant(session, user_id=user_id, tenant_key=tenant_key)
    if resolved is None:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None
    return resolved[0]


def _service(session: AsyncSession) -> SkillPackService:
    from kongming.semantic.repository import SqlSemanticRepo

    sem = SqlSemanticRepo(session)

    async def _names(*, tenant_id: str, datasource_key: str) -> list[dict[str, Any]]:
        # 完整行交给冲突判定(同名同定义=幂等重启用,不得自冲突)
        return await sem.list_metrics(tenant_id=tenant_id, datasource_key=datasource_key)

    async def _upsert(*, tenant_id: str, datasource_key: str, metric: dict[str, Any]) -> str:
        return await sem.upsert_metric(
            tenant_id=tenant_id, datasource_key=datasource_key, metric=metric
        )

    return SkillPackService(
        SqlSkillPackRepo(session),
        list_metric_names=_names,
        upsert_metric=_upsert,
        oper=SqlOperLogRepo(session),
    )


class PackCreateIn(BaseModel):
    pack_key: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=32)
    description: str = Field(default="", max_length=500)
    glossary: list[dict[str, Any]] = []
    fewshots: list[dict[str, Any]] = []
    persona: str = ""
    metrics: list[dict[str, Any]] = []


class BindIn(BaseModel):
    datasource_key: str = Field(min_length=1, max_length=64)
    enabled: bool


class PackOut(BaseModel):
    id: str
    pack_key: str
    name: str
    version: str
    description: str
    status: str


class PackListOut(BaseModel):
    items: list[PackOut]


class PackDetailOut(BaseModel):
    id: str
    pack_key: str
    name: str
    version: str
    description: str
    status: str
    content: dict[str, Any]
    bindings: list[dict[str, Any]]


@router.get("", response_model=PackListOut, dependencies=[SKILL_MANAGE])
async def list_packs(
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> PackListOut:
    tid = await _tenant_id(session, str(user["sub"]), x_tenant_id)
    rows = await _service(session).list(tenant_id=tid)
    items = [
        PackOut(
            id=str(r["id"]),
            pack_key=str(r["pack_key"]),
            name=str(r["name"]),
            version=str(r["version"]),
            # description 可空:str(None) 会回传字面量 "None"
            description=str(r["description"] or ""),
            status=str(r["status"]),
        )
        for r in rows
    ]
    return PackListOut(items=items)


@router.post("", status_code=201, dependencies=[SKILL_MANAGE])
async def create_pack(
    body: PackCreateIn,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    tid = await _tenant_id(session, str(user["sub"]), x_tenant_id)
    try:
        pid = await _service(session).create(
            user_id=str(user["sub"]),
            tenant_id=tid,
            pack_key=body.pack_key,
            name=body.name,
            version=body.version,
            description=body.description,
            content_raw={
                "glossary": body.glossary,
                "fewshots": body.fewshots,
                "persona": body.persona,
                "metrics": body.metrics,
            },
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return {"id": pid}


@router.get("/{pack_id}", response_model=PackDetailOut, dependencies=[SKILL_MANAGE])
async def get_pack(
    pack_id: str,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> PackDetailOut:
    tid = await _tenant_id(session, str(user["sub"]), x_tenant_id)
    try:
        row = await _service(session).get(pack_id=pack_id, tenant_id=tid)
    except SkillPackNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    return PackDetailOut(
        id=str(row["id"]),
        pack_key=str(row["pack_key"]),
        name=str(row["name"]),
        version=str(row["version"]),
        description=str(row["description"] or ""),
        status=str(row["status"]),
        content=dict(row.get("content") or {}),
        bindings=list(row.get("bindings") or []),
    )


@router.post("/{pack_id}/retire", status_code=204, dependencies=[SKILL_MANAGE])
async def retire_pack(
    pack_id: str,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> None:
    tid = await _tenant_id(session, str(user["sub"]), x_tenant_id)
    try:
        await _service(session).retire(user_id=str(user["sub"]), pack_id=pack_id, tenant_id=tid)
    except SkillPackNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None


@router.post("/{pack_id}/bind", status_code=204, dependencies=[SKILL_MANAGE])
async def bind_pack(
    pack_id: str,
    body: BindIn,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> None:
    tid = await _tenant_id(session, str(user["sub"]), x_tenant_id)
    try:
        await _service(session).set_enabled(
            user_id=str(user["sub"]),
            pack_id=pack_id,
            tenant_id=tid,
            datasource_key=body.datasource_key,
            enabled=body.enabled,
        )
    except SkillPackNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    except MetricConflictError as e:
        raise HTTPException(status_code=409, detail=f"METRIC_CONFLICT: {e.names}") from None
