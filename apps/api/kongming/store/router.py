"""store 路由(033,薄适配层):/store/* 发现/互动/直跑 + publish 治理。

读取/互动挂 bot:run(商店消费面);上架治理复用 032 can_manage 判定
(创建者或归属团队 OWNER,fail-closed)。
"""

import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.router import current_user, require_perm
from kongming.bot.repository import SqlBotRepo
from kongming.chat.repository import ChatRepository
from kongming.db import get_session
from kongming.store.repository import SqlStoreRepo
from kongming.store.service import (
    GuardrailRejected,
    StoreForbidden,
    StoreNotFound,
    StoreService,
    StoreValidationError,
)
from kongming.team.models import can_manage as _can_manage
from kongming.team.repository import SqlTeamRepo

log = logging.getLogger(__name__)

router = APIRouter(tags=["store"])

RUN = "bot:run"


class _BotReader:
    def __init__(self, session: AsyncSession) -> None:
        self._r = SqlBotRepo(session)

    async def get_bot(self, *, bot_id: str, tenant_id: str) -> dict[str, Any] | None:
        return await self._r.get_mine(
            bot_id=bot_id, tenant_id=tenant_id, user_id="", is_admin=True
        )


class _StoreGuardrail:
    async def filter_output(
        self, *, trace_id: str, user_id: str, tenant_id: str, texts: list[str]
    ) -> list[str]:
        from kongming.db import get_sessionmaker
        from kongming.guardrail.repository import SqlGuardrailAuditWriter, SqlGuardrailRepo
        from kongming.guardrail.service import GuardrailService

        async with get_sessionmaker()() as s:
            svc = GuardrailService(SqlGuardrailRepo(s), SqlGuardrailAuditWriter())
            return await svc.evaluate_outputs(
                trace_id=trace_id,
                user_id=user_id,
                tenant_id=tenant_id,
                texts=texts,
                safe_replacement="内容未通过安全检查,已省略。",
            )


class _StoreRunner:
    async def run(
        self, *, bot_key: str, question: str, tenant_id: str, channel: str
    ) -> tuple[bool, str, str]:
        from kongming.bot.router import _get_service as _bot_factory
        from kongming.chat.router import get_agent_client
        from kongming.db import get_sessionmaker

        async with get_sessionmaker()() as s:
            svc = _bot_factory(s, get_agent_client())
            from kongming.bot.service import BotNotFound

            try:
                out = await svc.run(
                    bot_key=bot_key,
                    question=question,
                    channel=channel,
                    tenant_id_hint=tenant_id,
                )
            except BotNotFound:
                # R1-J1:创建者被禁用(025 状态闸)——收敛友好文案不裸 500
                return False, "", "助手暂不可用"
            summary = str(out.get("summary") or "")
            return bool(out.get("success")), summary, str(out.get("error") or "")


def _svc(session: AsyncSession) -> StoreService:
    return StoreService(
        repo=SqlStoreRepo(session),
        bot_reader=_BotReader(session),
        guardrail=_StoreGuardrail(),
        runner=_StoreRunner(),
    )


async def _tenant_or_403(
    user: dict[str, object], session: AsyncSession, header: str | None
) -> str:
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


async def _can_manage_bot(
    user: dict[str, object], session: AsyncSession, row: dict[str, Any] | None
) -> bool:
    if row is None:
        return False
    team_id = None if row["team_id"] is None else str(row["team_id"])
    role = (
        await SqlTeamRepo(session).get_member_role(team_id=team_id, user_id=str(user["sub"]))
        if team_id
        else None
    )
    return _can_manage(
        team_id=team_id,
        creator_id=str(row["created_by"]),
        user_id=str(user["sub"]),
        member_role=role,
    )


class StoreBotOut(BaseModel):
    """商店列表项(J4 契约化;response_model 精确化,生成类型可消费)。"""

    id: str
    bot_key: str
    name: str
    greeting: str = ""
    use_count: int = 0
    avg_score: float = 0.0
    rating_count: int = 0
    fav_count: int = 0
    favorited: bool = False  # J5:本人收藏态(心形填充依据)
    published_at: str | None = None


class StoreBotDetailOut(StoreBotOut):
    suggestions: list[str] = []
    tools: list[str] = []
    my_rating: int | None = None


class StoreRunOut(BaseModel):
    success: bool
    summary: str = ""
    error: str = ""


def _store_out(r: dict[str, Any]) -> StoreBotOut:
    return StoreBotOut(
        id=str(r["id"]),
        bot_key=str(r["bot_key"]),
        name=str(r["name"]),
        greeting=str(r.get("greeting") or ""),
        use_count=int(r["use_count"]),
        avg_score=round(float(r.get("avg_score") or 0), 1),
        rating_count=int(r.get("rating_count") or 0),
        fav_count=int(r.get("fav_count") or 0),
        favorited=bool(r.get("favorited")),
        published_at=(
            r["published_at"].isoformat() if r.get("published_at") else None
        ),
    )


# ── 治理(挂在 bot 域既有路由前缀下,保持端点聚合) ──


class PublishIn(BaseModel):
    greeting: str = Field(default="", max_length=500)
    suggestions: list[str] = Field(default_factory=list, max_length=5)


@router.post("/system/bots/{bot_id}/publish", status_code=200,
             dependencies=[Depends(require_perm("chat:execute"))])
async def publish_bot(
    bot_id: uuid.UUID,
    body: PublishIn,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> dict[str, bool]:
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    row = await _BotReader(session).get_bot(bot_id=str(bot_id), tenant_id=tenant_id)
    can = await _can_manage_bot(user, session, row)
    svc = _svc(session)
    try:
        await svc.publish(
            bot_id=str(bot_id),
            tenant_id=tenant_id,
            user_id=str(user["sub"]),
            greeting=body.greeting,
            suggestions=body.suggestions[:5],
            can_manage=can,
        )
    except StoreForbidden as e:
        raise HTTPException(status_code=403, detail=str(e)) from None
    except StoreNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    except StoreValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    except GuardrailRejected as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    # J7:上架审计(025 bot.* 先例)
    from kongming.auth.repository import SqlOperLogRepo

    await SqlOperLogRepo(session).add(
        str(user["sub"]), "bot.publish", str(bot_id), {}
    )
    await session.commit()
    return {"ok": True}


@router.post("/system/bots/{bot_id}/unpublish", status_code=200,
             dependencies=[Depends(require_perm("chat:execute"))])
async def unpublish_bot(
    bot_id: uuid.UUID,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> dict[str, bool]:
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    row = await _BotReader(session).get_bot(bot_id=str(bot_id), tenant_id=tenant_id)
    can = await _can_manage_bot(user, session, row)
    svc = _svc(session)
    try:
        await svc.unpublish(bot_id=str(bot_id), tenant_id=tenant_id, can_manage=can)
    except StoreForbidden as e:
        raise HTTPException(status_code=403, detail=str(e)) from None
    except StoreNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    from kongming.auth.repository import SqlOperLogRepo

    await SqlOperLogRepo(session).add(
        str(user["sub"]), "bot.unpublish", str(bot_id), {}
    )
    await session.commit()
    return {"ok": True}


# ── 商店面 ──


@router.get("/store/bots", response_model=list[StoreBotOut],
            dependencies=[Depends(require_perm(RUN))])
async def store_list(
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
    q: Annotated[str, Query(max_length=64)] = "",
    sort: Annotated[str, Query(pattern="^(hot|new|rating)$")] = "new",
) -> list[StoreBotOut]:
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    rows = await _svc(session).list_store(
        tenant_id=tenant_id, keyword=q, sort=sort, user_id=str(user["sub"])
    )
    return [_store_out(r) for r in rows]


@router.get("/store/bots/{bot_key}", response_model=StoreBotDetailOut,
            dependencies=[Depends(require_perm(RUN))])
async def store_detail(
    bot_key: str,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> StoreBotDetailOut:
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    try:
        row = await _svc(session).get_detail(
            tenant_id=tenant_id, bot_key=bot_key, user_id=str(user["sub"])
        )
    except StoreNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    base = _store_out(row)
    return StoreBotDetailOut(
        **base.model_dump(),
        suggestions=[str(x) for x in row.get("suggestions") or []],
        tools=[str(x) for x in row.get("tools") or []],
        my_rating=row.get("my_rating"),
    )


@router.post("/store/bots/{bot_key}/favorite", dependencies=[Depends(require_perm(RUN))])
async def add_favorite(
    bot_key: str,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> dict[str, bool]:
    """收藏(幂等:set 语义,重复 POST 不反选——审查循环 F6 对齐 spec
    「重复操作幂等」;取消走 DELETE 同路径)。"""
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    try:
        favorited = await _svc(session).set_favorite(
            tenant_id=tenant_id, bot_key=bot_key, user_id=str(user["sub"]), favorited=True
        )
    except StoreNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    return {"favorited": favorited}


@router.delete("/store/bots/{bot_key}/favorite", dependencies=[Depends(require_perm(RUN))])
async def remove_favorite(
    bot_key: str,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> dict[str, bool]:
    """取消收藏(幂等:未收藏时重复 DELETE 同样返回 favorited=false)。"""
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    try:
        favorited = await _svc(session).set_favorite(
            tenant_id=tenant_id, bot_key=bot_key, user_id=str(user["sub"]), favorited=False
        )
    except StoreNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    return {"favorited": favorited}


@router.get("/store/favorites", dependencies=[Depends(require_perm(RUN))])
async def my_favorites(
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> list[dict[str, Any]]:
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    rows = await _svc(session).my_favorites(
        tenant_id=tenant_id, user_id=str(user["sub"])
    )
    return [
        {
            "id": str(r["id"]),
            "bot_key": r["bot_key"],
            "name": r["name"],
            "greeting": r.get("greeting") or "",
            "use_count": r["use_count"],
            "avg_score": round(float(r.get("avg_score") or 0), 1),
            "rating_count": r.get("rating_count") or 0,
        }
        for r in rows
    ]


class RatingIn(BaseModel):
    score: int = Field(ge=1, le=5)


@router.put("/store/bots/{bot_key}/rating", dependencies=[Depends(require_perm(RUN))])
async def rate_bot(
    bot_key: str,
    body: RatingIn,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> dict[str, bool]:
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    svc = _svc(session)
    try:
        await svc.rate(
            tenant_id=tenant_id,
            bot_key=bot_key,
            user_id=str(user["sub"]),
            score=body.score,
        )
    except StoreValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    except StoreNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    return {"ok": True}


class StoreRunIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


@router.post("/store/bots/{bot_key}/run", response_model=StoreRunOut,
            dependencies=[Depends(require_perm(RUN))])
async def store_run(
    bot_key: str,
    body: StoreRunIn,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> StoreRunOut:
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    try:
        out = await _svc(session).run(
            tenant_id=tenant_id, bot_key=bot_key, question=body.question
        )
    except StoreNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    return StoreRunOut(
        success=bool(out["success"]),
        summary=str(out.get("summary") or ""),
        error=str(out.get("error") or ""),
    )
