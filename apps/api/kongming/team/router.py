"""team 路由(032,薄适配层):/system/teams/* 端点族。

管理 CRUD 挂 system:team:manage;「我所在的团队」与成员列表登录即可
(成员关系过滤);成员管理操作要求操作者是该团队 OWNER(或持
system:team:manage 超管语义——v1 简化为操作者须目标团队 OWNER)。
"""

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.repository import SqlOperLogRepo
from kongming.auth.router import current_user, require_perm
from kongming.chat.repository import ChatRepository
from kongming.db import get_session
from kongming.team.models import TeamNotFound, TeamValidationError
from kongming.team.repository import SqlTeamRepo
from kongming.team.service import TeamForbiddenError, TeamService

log = logging.getLogger(__name__)

router = APIRouter(prefix="/system/teams", tags=["team"])

MANAGE = "system:team:manage"


class _Oper:
    def __init__(self, session: AsyncSession, actor_id: str) -> None:
        self._r = SqlOperLogRepo(session)
        self._a = actor_id

    async def log(self, *, action: str, target: str, detail: dict[str, object]) -> None:
        await self._r.add(self._a, action, target, detail)


def _svc(session: AsyncSession, actor_id: str | None = None) -> TeamService:
    return TeamService(SqlTeamRepo(session), _Oper(session, actor_id) if actor_id else None)


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


async def _require_owner_or_manage(
    user: dict[str, object], session: AsyncSession, team_id: str
) -> None:
    """成员管理操作闸:目标团队 OWNER 或持 system:team:manage 的超管语义
    (权限实时查库,与 require_perm 同口径——claims 不载权限点)。"""
    from kongming.auth.repository import SqlPermRepo

    perms = await SqlPermRepo(session).list_codes(str(user["sub"]))
    if MANAGE in set(perms):
        return
    role = await SqlTeamRepo(session).get_member_role(team_id=team_id, user_id=str(user["sub"]))
    if role != "OWNER":
        raise HTTPException(status_code=403, detail="TEAM_OWNER_REQUIRED")


class TeamIn(BaseModel):
    team_key: str = Field(min_length=2, max_length=64)
    name: str = Field(min_length=1, max_length=128)


class TeamRenameIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)


class MemberIn(BaseModel):
    user_id: str = Field(min_length=1)
    role: str = Field(pattern="^(OWNER|MEMBER|VIEWER)$")


class AttachIn(BaseModel):
    resource_type: str = Field(pattern="^(bot|workflow)$")
    resource_id: str = Field(min_length=1)


class TeamOut(BaseModel):
    """032 团队条目(审查循环 D-F2:契约化——曾裸 dict 使前端只能手写类型)。"""

    id: str
    team_key: str
    name: str
    created_at: str | None = None


class TeamMemberOut(BaseModel):
    user_id: str
    username: str
    role: str


class TeamDetailOut(BaseModel):
    team: TeamOut
    members: list[TeamMemberOut]


def _team_out(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "team_key": row["team_key"],
        "name": row["name"],
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
    }


@router.post("", status_code=201, dependencies=[Depends(require_perm(MANAGE))])
async def create_team(
    body: TeamIn,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    try:
        tid = await _svc(session, str(user["sub"])).create(
            tenant_id=tenant_id,
            team_key=body.team_key,
            name=body.name,
            created_by=str(user["sub"]),
        )
        await session.commit()
    except TeamValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return {"id": tid}


@router.get("", response_model=list[TeamOut])
async def list_teams(
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
    mine: bool = True,
) -> list[dict[str, Any]]:
    """mine=true(缺省)=我所在的团队(登录即可);manage 面 mine=false 全量。"""
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    from kongming.auth.repository import SqlPermRepo

    perms = set(await SqlPermRepo(session).list_codes(str(user["sub"])))
    svc = _svc(session)
    if mine or MANAGE not in perms:
        rows = await svc.list_mine(tenant_id=tenant_id, user_id=str(user["sub"]))
    else:
        rows = await svc.list_all(tenant_id=tenant_id)
    return [_team_out(r) for r in rows]


@router.get("/{team_id}", response_model=TeamDetailOut)
async def get_team(
    team_id: str,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    svc = _svc(session)
    # R1-J7:成员关系过滤(与 docstring 一致)——非成员且无 manage 404 同形
    from kongming.auth.repository import SqlPermRepo

    role = await SqlTeamRepo(session).get_member_role(team_id=team_id, user_id=str(user["sub"]))
    if role is None and MANAGE not in set(await SqlPermRepo(session).list_codes(str(user["sub"]))):
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    try:
        out = await svc.get_with_members(tenant_id=tenant_id, team_id=team_id)
    except TeamNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    return {
        "team": _team_out(out["team"]),
        "members": [
            {
                "user_id": str(m["user_id"]),
                "username": m["username"],
                "role": m["role"],
            }
            for m in out["members"]
        ],
    }


@router.put("/{team_id}", dependencies=[Depends(require_perm(MANAGE))])
async def rename_team(
    team_id: str,
    body: TeamRenameIn,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> dict[str, bool]:
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    try:
        await _svc(session, str(user["sub"])).rename(
            tenant_id=tenant_id, team_id=team_id, name=body.name
        )
        await session.commit()
    except TeamValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    except TeamNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    return {"ok": True}


@router.delete("/{team_id}", dependencies=[Depends(require_perm(MANAGE))])
async def delete_team(
    team_id: str,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> dict[str, bool]:
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    try:
        await _svc(session, str(user["sub"])).delete(tenant_id=tenant_id, team_id=team_id)
        await session.commit()
    except TeamNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    return {"ok": True}


@router.post("/{team_id}/members")
async def add_member(
    team_id: str,
    body: MemberIn,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> dict[str, bool]:
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    await _require_owner_or_manage(user, session, team_id)
    try:
        await _svc(session, str(user["sub"])).add_member(
            tenant_id=tenant_id, team_id=team_id, user_id=body.user_id, role=body.role
        )
        await session.commit()
    except TeamValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    except TeamNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    return {"ok": True}


@router.put("/{team_id}/members/{member_id}")
async def change_member_role(
    team_id: str,
    member_id: str,
    body: MemberIn,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> dict[str, bool]:
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    await _require_owner_or_manage(user, session, team_id)
    try:
        await _svc(session, str(user["sub"])).change_member_role(
            tenant_id=tenant_id, team_id=team_id, user_id=member_id, role=body.role
        )
        await session.commit()
    except TeamValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    except TeamNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    except TeamForbiddenError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    return {"ok": True}


@router.delete("/{team_id}/members/{member_id}")
async def remove_member(
    team_id: str,
    member_id: str,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> dict[str, bool]:
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    await _require_owner_or_manage(user, session, team_id)
    try:
        await _svc(session, str(user["sub"])).remove_member(
            tenant_id=tenant_id, team_id=team_id, user_id=member_id
        )
        await session.commit()
    except TeamNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    except TeamForbiddenError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    return {"ok": True}


@router.post("/{team_id}/attach", dependencies=[Depends(require_perm(MANAGE))])
async def attach_resource(
    team_id: str,
    body: AttachIn,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> dict[str, bool]:
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    role = await SqlTeamRepo(session).get_member_role(team_id=team_id, user_id=str(user["sub"]))
    # 资源创建者反查(bot/workflow 两表)
    from sqlalchemy import text as _text

    table = {
        "bot": "public.sys_bot_definition",
        "workflow": "public.sys_workflow_def",
    }[body.resource_type]
    # R1-S1:资源行租户过滤(反查与 UPDATE 同租户收口;跨租户 404 同形)
    creator = (
        await session.execute(
            _text(
                f"SELECT created_by FROM {table}"  # noqa: S608  # 常量表名映射
                " WHERE id=CAST(:i AS uuid) AND tenant_id=CAST(:t AS uuid)"
            ),
            {"i": body.resource_id, "t": tenant_id},
        )
    ).scalar_one_or_none()
    if creator is None:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    try:
        await _svc(session, str(user["sub"])).attach(
            tenant_id=tenant_id,
            team_id=team_id,
            resource_type=body.resource_type,
            resource_id=body.resource_id,
            actor_id=str(user["sub"]),
            actor_role=role,
            resource_creator_id=str(creator),
        )
        await session.commit()
    except TeamValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    except TeamNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    except TeamForbiddenError as e:
        raise HTTPException(status_code=403, detail=str(e)) from None
    return {"ok": True}
