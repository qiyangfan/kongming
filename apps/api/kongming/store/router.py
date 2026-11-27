"""处理 /store/* 下的发现、互动、执行和发布端点。

读取/互动挂 bot:run(商店消费面);上架治理复用 can_manage 判定
(创建者或归属团队 OWNER,fail-closed)。
"""

import logging
import uuid
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.deps import CurrentUser, require_perm
from kongming.auth.models import AuthenticatedUser
from kongming.bot.repository import SqlBotRepo
from kongming.chat.repository import ChatRepository
from kongming.config import get_settings
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
        return await self._r.get_mine(bot_id=bot_id, tenant_id=tenant_id, user_id="", is_admin=True)


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

    async def check_input(
        self, *, trace_id: str, user_id: str, tenant_id: str, texts: list[str]
    ) -> tuple[bool, list[str]]:
        """输入面判定(回滚重审系统提示词;与 bot 域同口径)。"""
        from kongming.db import get_sessionmaker
        from kongming.guardrail.repository import SqlGuardrailAuditWriter, SqlGuardrailRepo
        from kongming.guardrail.service import GuardrailService

        async with get_sessionmaker()() as s:
            svc = GuardrailService(SqlGuardrailRepo(s), SqlGuardrailAuditWriter())
            verdict = await svc.evaluate_input(
                trace_id=trace_id, user_id=user_id, tenant_id=tenant_id, texts=texts
            )
        return verdict.blocked, verdict.processed


class _StoreRunner:
    async def run(
        self,
        *,
        bot_key: str,
        question: str,
        tenant_id: str,
        channel: str,
        definition: dict[str, Any] | None = None,
    ) -> tuple[bool, str, str]:
        from kongming.bot.service_factory import build_bot_service_for_agent as _bot_factory
        from kongming.chat.dependencies import get_agent_client
        from kongming.db import get_sessionmaker

        async with get_sessionmaker()() as s:
            svc = _bot_factory(get_agent_client(), s)
            from kongming.bot.service import BotNotFound

            try:
                out = await svc.run(
                    bot_key=bot_key,
                    question=question,
                    channel=channel,
                    tenant_id_hint=tenant_id,
                    # 商店直跑消费当前上架版本快照(系统提示词/工具集)
                    definition_override=definition,
                )
            except BotNotFound:
                # 创建者被禁用(状态闸)——收敛友好文案不裸 500
                return False, "", "助手暂不可用"
            summary = str(out.get("summary") or "")
            return bool(out.get("success")), summary, str(out.get("error") or "")


class _StoreToolLister:
    """通过 Bot 域服务解析当前上架 Bot 可用工具，不直接依赖 Bot 仓储。"""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def resolve_runnable(
        self, *, creator_id: str, tenant_id: str, row: dict[str, Any]
    ) -> list[dict[str, Any]]:
        from kongming.bot.service_factory import build_bot_service_for_agent as _bot_factory
        from kongming.chat.dependencies import get_agent_client

        svc = _bot_factory(get_agent_client(), self._s)
        return await svc.resolve_tools(creator_id=creator_id, tenant_id=tenant_id, row=row)


def _svc(session: AsyncSession) -> StoreService:
    return StoreService(
        repo=SqlStoreRepo(session),
        bot_reader=_BotReader(session),
        tool_lister=_StoreToolLister(session),
        guardrail=_StoreGuardrail(),
        runner=_StoreRunner(),
        version_cap=get_settings().bot_store_version_cap,  # 封顶上限部署级
    )


async def _tenant_or_403(user: AuthenticatedUser, session: AsyncSession, header: str | None) -> str:
    repo = ChatRepository(session)
    uid = user.user_id
    if header is not None:
        tid = await repo.find_bound_tenant(user_id=uid, tenant_key=header)
        if tid is None:
            raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN")
        return str(tid)
    default = await repo.find_default_tenant(user_id=uid)
    if default is None:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN")
    return str(default[0])


async def _tenant_key(session: AsyncSession, tenant_id: str) -> str:
    row = (
        await session.execute(
            text("SELECT tenant_key FROM public.sys_tenant WHERE id = CAST(:t AS uuid)"),
            {"t": tenant_id},
        )
    ).scalar_one_or_none()
    return "" if row is None else str(row)


async def _require_catalog(
    session: AsyncSession, *, user_id: str, tenant_id: str, bot_key: str
) -> None:
    """managed 目录闸:不在租户可见目录即 404 同形,并落拒绝审计。"""
    from kongming.auth.repository import SqlOperLogRepo
    from kongming.control.dependencies import get_control_runtime

    catalog = get_control_runtime().catalog
    if not catalog.managed:
        return
    entries = await catalog.entries_for_tenant(await _tenant_key(session, tenant_id))
    if bot_key in entries:
        return
    await SqlOperLogRepo(session).add(
        user_id, "store.catalog_denied", bot_key, {"tenant_id": tenant_id}
    )
    await session.commit()
    raise HTTPException(status_code=404, detail="NOT_FOUND")


async def _filter_catalog(
    session: AsyncSession, rows: list[dict[str, Any]], tenant_id: str
) -> list[dict[str, Any]]:
    from kongming.control.dependencies import get_control_runtime

    catalog = get_control_runtime().catalog
    if not catalog.managed:
        return rows
    entries = await catalog.entries_for_tenant(await _tenant_key(session, tenant_id))
    return [r for r in rows if str(r.get("bot_key")) in entries]


async def _can_manage_bot(
    user: AuthenticatedUser, session: AsyncSession, row: dict[str, Any] | None
) -> bool:
    if row is None:
        return False
    team_id = None if row["team_id"] is None else str(row["team_id"])
    role = (
        await SqlTeamRepo(session).get_member_role(team_id=team_id, user_id=user.user_id)
        if team_id
        else None
    )
    return _can_manage(
        team_id=team_id,
        creator_id=str(row["created_by"]),
        user_id=user.user_id,
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
    favorited: bool = False  # 本人收藏态(心形填充依据)
    published_at: str | None = None


class StoreBotDetailOut(StoreBotOut):
    suggestions: list[str] = []
    tools: list[str] = []
    my_rating: int | None = None
    version_no: int | None = None  # 当前上架版本号
    version_published_at: str | None = None  # 当前版本上架时间


class BotSnapshotOut(BaseModel):
    """版本快照定型(读侧单源=StoreSnapshotFields 白名单)。"""

    name: str
    greeting: str = ""
    suggestions: list[str] = []
    system_prompt: str = ""
    tools: list[str] = []


class BotVersionOut(BaseModel):
    """版本历史项:版本号/上架时间/当前标识/快照内容。"""

    version_no: int
    published_at: str
    current: bool
    snapshot: BotSnapshotOut


class StoreRunOut(BaseModel):
    success: bool
    summary: str = ""
    error: str = ""


def _store_out(r: dict[str, Any]) -> StoreBotOut:
    published_at = r.get("published_at")
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
        published_at=published_at.isoformat() if published_at else None,
    )


# ── 治理(挂在 bot 域既有路由前缀下,保持端点聚合) ──


class PublishIn(BaseModel):
    greeting: str = Field(default="", max_length=500)
    suggestions: list[str] = Field(default_factory=list, max_length=5)


@router.post(
    "/system/bots/{bot_id}/publish",
    status_code=200,
    dependencies=[Depends(require_perm("chat:execute"))],
)
async def publish_bot(
    bot_id: uuid.UUID,
    body: PublishIn,
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> dict[str, bool]:
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    row = await _BotReader(session).get_bot(bot_id=str(bot_id), tenant_id=tenant_id)
    can = await _can_manage_bot(user, session, row)
    svc = _svc(session)
    try:
        out = await svc.publish(
            bot_id=str(bot_id),
            tenant_id=tenant_id,
            user_id=user.user_id,
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
    # 上架审计(bot.* 先例);幂等复用记「内容无变化」,
    # 新版本记版本号(spec「内容无变化幂等复用…审计记录内容无变化」)
    from kongming.auth.repository import SqlOperLogRepo

    await SqlOperLogRepo(session).add(
        user.user_id,
        "bot.publish",
        str(bot_id),
        {
            "content_changed": not bool(out["idempotent"]),
            "version_no": int(cast("int", out["version_no"])),
        },
    )
    await session.commit()
    return {"ok": True}


@router.post(
    "/system/bots/{bot_id}/unpublish",
    status_code=200,
    dependencies=[Depends(require_perm("chat:execute"))],
)
async def unpublish_bot(
    bot_id: uuid.UUID,
    user: CurrentUser,
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

    await SqlOperLogRepo(session).add(user.user_id, "bot.unpublish", str(bot_id), {})
    await session.commit()
    return {"ok": True}


def _version_out(r: dict[str, Any]) -> BotVersionOut:
    """版本行(dict)→ 契约出参;快照读侧定型(BotSnapshotOut 白名单)。"""
    snap = cast("dict[str, object]", r.get("snapshot") or {})
    raw_sug = snap.get("suggestions")
    raw_tools = snap.get("tools")
    return BotVersionOut(
        version_no=int(r["version_no"]),
        published_at=r["published_at"].isoformat(),
        current=bool(r.get("is_current")),
        snapshot=BotSnapshotOut(
            name=str(snap.get("name") or ""),
            greeting=str(snap.get("greeting") or ""),
            suggestions=[str(x) for x in cast("list[object]", raw_sug)]
            if isinstance(raw_sug, list)
            else [],
            system_prompt=str(snap.get("system_prompt") or ""),
            tools=[str(x) for x in cast("list[object]", raw_tools)]
            if isinstance(raw_tools, list)
            else [],
        ),
    )


# ── 版本历史与回滚(归属口径与上架治理同) ──


@router.get(
    "/system/bots/{bot_id}/versions",
    response_model=list[BotVersionOut],
    dependencies=[Depends(require_perm("chat:execute"))],
)
async def list_bot_versions(
    bot_id: uuid.UUID,
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> list[BotVersionOut]:
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    row = await _BotReader(session).get_bot(bot_id=str(bot_id), tenant_id=tenant_id)
    can = await _can_manage_bot(user, session, row)
    svc = _svc(session)
    try:
        rows = await svc.list_versions(bot_id=str(bot_id), tenant_id=tenant_id, can_manage=can)
    except StoreForbidden as e:
        raise HTTPException(status_code=403, detail=str(e)) from None
    except StoreNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    return [_version_out(r) for r in rows]


@router.post(
    "/system/bots/{bot_id}/versions/{version_no}/rollback",
    dependencies=[Depends(require_perm("chat:execute"))],
)
async def rollback_bot_version(
    bot_id: uuid.UUID,
    version_no: int,
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    """回滚=历史版本重新指为当前上架版本(视同上架,按现行护栏重审;
    BLOCK 拒绝指针不动;回滚到当前版本为幂等 no-op)。"""
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    row = await _BotReader(session).get_bot(bot_id=str(bot_id), tenant_id=tenant_id)
    can = await _can_manage_bot(user, session, row)
    svc = _svc(session)
    try:
        out = await svc.rollback(
            bot_id=str(bot_id),
            tenant_id=tenant_id,
            user_id=user.user_id,
            version_no=version_no,
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
    from kongming.auth.repository import SqlOperLogRepo

    await SqlOperLogRepo(session).add(
        user.user_id,
        "bot.version_rollback",
        str(bot_id),
        {
            "version_no": int(cast("int", out["version_no"])),
            "switched": bool(out["switched"]),
        },
    )
    await session.commit()
    return {"ok": True, "version_no": int(cast("int", out["version_no"]))}


# ── 商店面 ──


@router.get(
    "/store/bots", response_model=list[StoreBotOut], dependencies=[Depends(require_perm(RUN))]
)
async def store_list(
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
    q: Annotated[str, Query(max_length=64)] = "",
    sort: Annotated[str, Query(pattern="^(hot|new|rating)$")] = "new",
) -> list[StoreBotOut]:
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    rows = await _svc(session).list_store(
        tenant_id=tenant_id, keyword=q, sort=sort, user_id=user.user_id
    )
    rows = await _filter_catalog(session, rows, tenant_id)
    return [_store_out(r) for r in rows]


@router.get(
    "/store/bots/{bot_key}",
    response_model=StoreBotDetailOut,
    dependencies=[Depends(require_perm(RUN))],
)
async def store_detail(
    bot_key: str,
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> StoreBotDetailOut:
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    await _require_catalog(session, user_id=user.user_id, tenant_id=tenant_id, bot_key=bot_key)
    try:
        row = await _svc(session).get_detail(
            tenant_id=tenant_id, bot_key=bot_key, user_id=user.user_id
        )
    except StoreNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    base = _store_out(row)
    version_published_at = row.get("version_published_at")
    return StoreBotDetailOut(
        **base.model_dump(),
        suggestions=[str(x) for x in cast("list[object]", row.get("suggestions") or [])],
        tools=[str(x) for x in cast("list[object]", row.get("tools") or [])],
        my_rating=row.get("my_rating"),
        version_no=None if row.get("version_no") is None else int(row["version_no"]),
        version_published_at=(version_published_at.isoformat() if version_published_at else None),
    )


@router.post("/store/bots/{bot_key}/favorite", dependencies=[Depends(require_perm(RUN))])
async def add_favorite(
    bot_key: str,
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> dict[str, bool]:
    """收藏(幂等:set 语义,重复 POST 不反选—— 对齐 spec
    「重复操作幂等」;取消走 DELETE 同路径)。"""
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    await _require_catalog(session, user_id=user.user_id, tenant_id=tenant_id, bot_key=bot_key)
    try:
        favorited = await _svc(session).set_favorite(
            tenant_id=tenant_id, bot_key=bot_key, user_id=user.user_id, favorited=True
        )
    except StoreNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    return {"favorited": favorited}


@router.delete("/store/bots/{bot_key}/favorite", dependencies=[Depends(require_perm(RUN))])
async def remove_favorite(
    bot_key: str,
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> dict[str, bool]:
    """取消收藏(幂等:未收藏时重复 DELETE 同样返回 favorited=false)。"""
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    await _require_catalog(session, user_id=user.user_id, tenant_id=tenant_id, bot_key=bot_key)
    try:
        favorited = await _svc(session).set_favorite(
            tenant_id=tenant_id, bot_key=bot_key, user_id=user.user_id, favorited=False
        )
    except StoreNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    return {"favorited": favorited}


@router.get(
    "/store/favorites", response_model=list[StoreBotOut], dependencies=[Depends(require_perm(RUN))]
)
async def my_favorites(
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> list[StoreBotOut]:
    """与 /store/bots 同构的 StoreBotOut(含 favorited/fav_count,前端心形
    填充与收藏计数同源);列表本身即本人收藏,favorited 恒 TRUE。"""
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    rows = await _svc(session).my_favorites(tenant_id=tenant_id, user_id=user.user_id)
    rows = await _filter_catalog(session, rows, tenant_id)
    return [_store_out(r) for r in rows]


class RatingIn(BaseModel):
    score: int = Field(ge=1, le=5)


@router.put("/store/bots/{bot_key}/rating", dependencies=[Depends(require_perm(RUN))])
async def rate_bot(
    bot_key: str,
    body: RatingIn,
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> dict[str, bool]:
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    await _require_catalog(session, user_id=user.user_id, tenant_id=tenant_id, bot_key=bot_key)
    svc = _svc(session)
    try:
        await svc.rate(
            tenant_id=tenant_id,
            bot_key=bot_key,
            user_id=user.user_id,
            score=body.score,
        )
    except StoreValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    except StoreNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    return {"ok": True}


class StoreRunIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


@router.post(
    "/store/bots/{bot_key}/run",
    response_model=StoreRunOut,
    dependencies=[Depends(require_perm(RUN))],
)
async def store_run(
    bot_key: str,
    body: StoreRunIn,
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> StoreRunOut:
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    await _require_catalog(session, user_id=user.user_id, tenant_id=tenant_id, bot_key=bot_key)
    try:
        out = await _svc(session).run(tenant_id=tenant_id, bot_key=bot_key, question=body.question)
    except StoreNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    return StoreRunOut(
        success=bool(out["success"]),
        summary=str(out.get("summary") or ""),
        error=str(out.get("error") or ""),
    )
