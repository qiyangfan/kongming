"""bot 路由(custom-agent spec):管理端点(归属收口)+ 公开 web 嵌入 +
OpenAI 兼容 API(api-key 鉴权)。组合根见 dependencies/adapters。
"""

import time
import uuid
from typing import Annotated, cast

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.audit.context import client_ip_var
from kongming.auth.repository import SqlOperLogRepo, SqlPermRepo
from kongming.auth.router import current_user, require_perm
from kongming.bot.dependencies import build_bot_rate_gate, get_bot_service
from kongming.bot.models import (
    InvalidBotKey,
    ToolSubsetViolation,
    tools_for_perms,
    validate_tool_subset,
)
from kongming.bot.schemas import (
    ApiKeyOut,
    BotChatIn,
    BotChatOut,
    BotIn,
    BotOut,
    BotUpdateIn,
    OpenAiChatIn,
    to_bot_out,
)
from kongming.bot.service import (
    ApiKeyInvalid,
    BotNotFound,
    BotService,
    GuardrailRejected,
)
from kongming.chat.costgate import ChatRateLimited
from kongming.db import get_session
from kongming.toolcall.repository import user_has_all_scope

router = APIRouter(prefix="/system/bots", tags=["bot"])
public_router = APIRouter(tags=["bot-public"])


async def _team_ids(session: AsyncSession, user_id: str) -> list[str]:
    """用户所属团队 id 集(列表与归属查询的可见性过滤用)。"""
    from kongming.team.repository import SqlTeamRepo

    return list((await SqlTeamRepo(session).role_map(user_id=user_id)).keys())


async def _team_role(session: AsyncSession, user_id: str, team_id: str | None) -> str | None:
    """单资源的团队角色(编辑/删除判定);无团队 None。"""
    if team_id is None:
        return None
    from kongming.team.repository import SqlTeamRepo

    return await SqlTeamRepo(session).get_member_role(team_id=team_id, user_id=user_id)


async def _tenant_of(session: AsyncSession, user_id: str, tenant_key: str | None) -> str:
    """与 memory/semantic 同构(ACTIVE+default 优先+tiebreak)。
    SQL 单一真理源=kongming.tenant.resolve_tenant。"""
    from kongming.tenant import resolve_tenant

    resolved = await resolve_tenant(session, user_id=user_id, tenant_key=tenant_key)
    if resolved is None:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None
    return resolved[0]


def _row_team_id(row: dict[str, object]) -> str | None:
    """行内团队 id 原样转字符串;无团队保留 None(角色判定据此短路)。"""
    return None if row["team_id"] is None else str(row["team_id"])


async def _is_creator_or_owner(session: AsyncSession, user_id: str, row: dict[str, object]) -> bool:
    """创建者或团队 OWNER 判定(删除/密钥管理口径,等同 can_manage)。"""
    from kongming.team.models import can_manage

    return can_manage(
        team_id=_row_team_id(row),
        creator_id=str(row["created_by"]),
        user_id=user_id,
        member_role=await _team_role(session, user_id, _row_team_id(row)),
    )


async def _assert_creator_or_owner(
    session: AsyncSession, user_id: str, row: dict[str, object]
) -> None:
    """非创建者且非团队 OWNER 一律 403(与各端点同形 detail)。"""
    if not await _is_creator_or_owner(session, user_id, row):
        raise HTTPException(status_code=403, detail="TEAM_ROLE_FORBIDDEN") from None


async def _fetch_owned_bot(
    session: AsyncSession,
    *,
    bot_id: str,
    tenant_id: str,
    user_id: str,
    all_scope: bool,
) -> dict[str, object]:
    """按归属收口取 Bot 行;不存在/不可见统一 404。"""
    from kongming.bot.repository import SqlBotRepo

    row = await SqlBotRepo(session).get_mine(
        bot_id=bot_id,
        tenant_id=tenant_id,
        user_id=user_id,
        is_admin=all_scope,
        team_ids=await _team_ids(session, user_id),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    return row


async def _check_datasource_field(
    svc: BotService, tenant_id: str, fields: dict[str, object]
) -> None:
    """datasource_key 非 default 时复校存在/启用/归属(400 带服务层文案)。"""
    if "datasource_key" not in fields or str(fields["datasource_key"]) == "default":
        return
    try:
        await svc._assert_datasource(tenant_id, str(fields["datasource_key"]))  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    except InvalidBotKey as e:
        raise HTTPException(status_code=400, detail=str(e)) from None


async def _check_model_field(svc: BotService, fields: dict[str, object]) -> None:
    """model 变更时复校 agent 注册表。"""
    if "model" not in fields:
        return
    available = await svc._runner.list_models()  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    if str(fields["model"]) not in available:
        raise HTTPException(status_code=400, detail="模型未注册") from None


async def _check_tools_field(
    session: AsyncSession,
    svc: BotService,
    *,
    tenant_id: str,
    user_id: str,
    fields: dict[str, object],
) -> None:
    """tools 变更时复校权限子集(须含 MCP 动态工具集,见下方注释)。"""
    if "tools" not in fields:
        return
    # 与 create 同口径——须含 MCP 动态工具集,否则含
    # mcp__ 工具的 Bot 任何一次带 tools 的更新都被静态注册表误拒(工具集被冻结)
    mcp_names = await svc._mcp_names(tenant_id)  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    allowed = tools_for_perms(await SqlPermRepo(session).list_codes(user_id), mcp_names)
    try:
        validate_tool_subset(
            [str(t) for t in cast("list[object]", fields["tools"] or [])],
            allowed,
            registered_names=svc._registered_names(mcp_names),  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        )
    except ToolSubsetViolation as e:
        raise HTTPException(status_code=400, detail=str(e)) from None


async def _check_renamed_published(
    session: AsyncSession,
    *,
    tenant_id: str,
    user_id: str,
    row: dict[str, object],
    fields: dict[str, object],
) -> None:
    """已上架 Bot 改名须过护栏(上架内容的「名」与 greeting 同风险面)。"""
    if "name" not in fields or row["published_at"] is None:
        return
    from kongming.guardrail.repository import (
        SqlGuardrailAuditWriter,
        SqlGuardrailRepo,
    )
    from kongming.guardrail.service import GuardrailService

    filtered = await GuardrailService(
        SqlGuardrailRepo(session), SqlGuardrailAuditWriter()
    ).evaluate_outputs(
        trace_id="",
        user_id=user_id,
        tenant_id=tenant_id,
        texts=[str(fields["name"])],
        safe_replacement="内容未通过安全检查,已省略。",
    )
    if filtered[0] != str(fields["name"]):
        raise HTTPException(status_code=400, detail="GUARDRAIL_REJECTED") from None


async def _check_prompt_field(
    svc: BotService,
    *,
    tenant_id: str,
    user_id: str,
    row: dict[str, object],
    fields: dict[str, object],
) -> None:
    """system_prompt 变更或开启公开均重审(未改提示词时审库存值)。"""
    if "system_prompt" not in fields and not fields.get("is_public"):
        return
    prompt_to_check = str(fields.get("system_prompt") or row["system_prompt"])
    try:
        await svc.validate_prompt(user_id=user_id, tenant_id=tenant_id, prompt=prompt_to_check)
    except GuardrailRejected:
        raise HTTPException(status_code=400, detail="GUARDRAIL_REJECTED") from None


# ── 管理端点(登录+chat:execute;归属收口创建者或超管) ──


@router.get("", response_model=list[BotOut])
async def list_bots(
    user: Annotated[dict[str, object], Depends(current_user)],
    _: Annotated[None, Depends(require_perm("chat:execute"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> list[BotOut]:
    from kongming.bot.repository import SqlBotRepo

    tid = await _tenant_of(session, str(user["sub"]), x_tenant_id)
    all_scope = await user_has_all_scope(session, str(user["sub"]))
    rows = await SqlBotRepo(session).list_bots(
        tenant_id=tid,
        user_id=str(user["sub"]),
        is_admin=all_scope,
        team_ids=await _team_ids(session, str(user["sub"])),
    )
    return [to_bot_out(r) for r in rows]


@router.post("", status_code=201)
async def create_bot(
    req: BotIn,
    user: Annotated[dict[str, object], Depends(current_user)],
    _: Annotated[None, Depends(require_perm("chat:execute"))],
    svc: Annotated[BotService, Depends(get_bot_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    tid = await _tenant_of(session, str(user["sub"]), x_tenant_id)
    try:
        bot_id = await svc.create(user_id=str(user["sub"]), tenant_id=tid, payload=req.model_dump())
    except InvalidBotKey:
        raise HTTPException(status_code=400, detail="INVALID_BOT_KEY") from None
    except ToolSubsetViolation as e:
        raise HTTPException(status_code=400, detail=f"TOOL_SUBSET_VIOLATION: {e}") from None
    except GuardrailRejected:
        raise HTTPException(status_code=400, detail="GUARDRAIL_REJECTED") from None
    return {"id": bot_id}


@router.put("/{bot_id}", status_code=204)
async def update_bot(
    bot_id: uuid.UUID,
    req: BotUpdateIn,
    user: Annotated[dict[str, object], Depends(current_user)],
    _: Annotated[None, Depends(require_perm("chat:execute"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    svc: Annotated[BotService, Depends(get_bot_service)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> None:
    from kongming.bot.repository import SqlBotRepo

    user_id = str(user["sub"])
    tid = await _tenant_of(session, user_id, x_tenant_id)
    all_scope = await user_has_all_scope(session, user_id)
    repo = SqlBotRepo(session)
    row = await _fetch_owned_bot(
        session, bot_id=str(bot_id), tenant_id=tid, user_id=user_id, all_scope=all_scope
    )
    # 编辑=创建者或 MEMBER+(VIEWER 可见不可编辑)
    from kongming.team.models import can_edit

    if not can_edit(
        team_id=_row_team_id(row),
        creator_id=str(row["created_by"]),
        user_id=user_id,
        member_role=await _team_role(session, user_id, _row_team_id(row)),
    ):
        raise HTTPException(status_code=403, detail="TEAM_ROLE_FORBIDDEN") from None
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    # 公开发布(is_public false→true)=创建者或 OWNER 全权——
    # MEMBER 可编辑但不可把团队 Bot 暴露到免登录公网渠道
    # (is_public 为 bool|None,真值判断即语义,无需 is True 恒等比较)
    if fields.get("is_public") and not row["is_public"]:
        await _assert_creator_or_owner(session, user_id, row)
    # 编辑路径 datasource/model 复校
    await _check_datasource_field(svc, tid, fields)
    await _check_model_field(svc, fields)
    # 编辑越权与审核:tools/system_prompt/name 变更时复校
    await _check_tools_field(session, svc, tenant_id=tid, user_id=user_id, fields=fields)
    await _check_renamed_published(session, tenant_id=tid, user_id=user_id, row=row, fields=fields)
    await _check_prompt_field(svc, tenant_id=tid, user_id=user_id, row=row, fields=fields)
    ok = await repo.update_bot(bot_id=str(bot_id), fields=fields)
    if not ok:
        raise HTTPException(status_code=400, detail="EMPTY_UPDATE") from None
    await SqlOperLogRepo(session).add(
        str(user["sub"]), "bot.update", str(row["bot_key"]), {"fields": sorted(fields)}
    )
    await session.commit()


@router.delete("/{bot_id}", status_code=204)
async def delete_bot(
    bot_id: uuid.UUID,
    user: Annotated[dict[str, object], Depends(current_user)],
    _: Annotated[None, Depends(require_perm("chat:execute"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> None:
    """Bot 删除(api-key 经 FK CASCADE;归属收口)。"""
    user_id = str(user["sub"])
    tid = await _tenant_of(session, user_id, x_tenant_id)
    all_scope = await user_has_all_scope(session, user_id)
    row = await _fetch_owned_bot(
        session, bot_id=str(bot_id), tenant_id=tid, user_id=user_id, all_scope=all_scope
    )
    # 删除=创建者或 OWNER(全权)
    await _assert_creator_or_owner(session, user_id, row)
    hit = (
        await session.execute(
            text("DELETE FROM public.sys_bot_definition WHERE id=:i RETURNING id"),
            {"i": str(bot_id)},
        )
    ).scalar_one_or_none()
    if hit is None:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    await SqlOperLogRepo(session).add(str(user["sub"]), "bot.delete", str(row["bot_key"]), {})
    await session.commit()


@router.post("/{bot_id}/keys", status_code=201)
async def create_key(
    bot_id: uuid.UUID,
    user: Annotated[dict[str, object], Depends(current_user)],
    _: Annotated[None, Depends(require_perm("chat:execute"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    svc: Annotated[BotService, Depends(get_bot_service)],
    name: str = "default",
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    user_id = str(user["sub"])
    tid = await _tenant_of(session, user_id, x_tenant_id)
    all_scope = await user_has_all_scope(session, user_id)
    row = await _fetch_owned_bot(
        session, bot_id=str(bot_id), tenant_id=tid, user_id=user_id, all_scope=all_scope
    )
    # key 管理=创建者或 OWNER(全权)
    await _assert_creator_or_owner(session, user_id, row)
    plain, key_id = await svc.create_key(
        bot_id=str(bot_id), tenant_id=tid, user_id=str(user["sub"]), name=name
    )
    await SqlOperLogRepo(session).add(
        str(user["sub"]), "bot.key_create", str(row["bot_key"]), {"name": name}
    )
    await session.commit()
    return {"id": key_id, "api_key": plain}  # 明文仅此一次


@router.get("/{bot_id}/keys", response_model=list[ApiKeyOut])
async def list_keys(
    bot_id: uuid.UUID,
    user: Annotated[dict[str, object], Depends(current_user)],
    _: Annotated[None, Depends(require_perm("chat:execute"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> list[ApiKeyOut]:
    from kongming.bot.repository import SqlBotRepo

    user_id = str(user["sub"])
    tid = await _tenant_of(session, user_id, x_tenant_id)
    all_scope = await user_has_all_scope(session, user_id)
    repo = SqlBotRepo(session)
    row = await _fetch_owned_bot(
        session, bot_id=str(bot_id), tenant_id=tid, user_id=user_id, all_scope=all_scope
    )
    await _assert_creator_or_owner(session, user_id, row)
    rows = await repo.list_keys(bot_id=str(bot_id))
    return [
        ApiKeyOut(
            id=str(r["id"]),
            name=str(r["name"]),
            revoked_at=None if r["revoked_at"] is None else str(r["revoked_at"]),
            last_used_at=None if r["last_used_at"] is None else str(r["last_used_at"]),
            created_at=str(r["created_at"]),
        )
        for r in rows
    ]


@router.delete("/keys/{key_id}", status_code=204)
async def revoke_key(
    key_id: uuid.UUID,
    user: Annotated[dict[str, object], Depends(current_user)],
    _: Annotated[None, Depends(require_perm("chat:execute"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> None:
    from kongming.bot.repository import SqlBotRepo

    user_id = str(user["sub"])
    tid = await _tenant_of(session, user_id, x_tenant_id)
    all_scope = await user_has_all_scope(session, user_id)
    repo = SqlBotRepo(session)
    # 归属:key 的 bot 属本人或超管
    row = (
        await session.execute(
            text("SELECT bot_id FROM public.sys_api_key WHERE id=:i AND tenant_id=:t"),
            {"i": str(key_id), "t": tid},
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    bot = await _fetch_owned_bot(
        session, bot_id=str(row), tenant_id=tid, user_id=user_id, all_scope=all_scope
    )
    # 密钥管理只允许创建者或 OWNER，与创建和列表接口使用相同权限范围。
    await _assert_creator_or_owner(session, user_id, bot)
    await repo.revoke_key(key_id=str(key_id), bot_id=str(row))
    await SqlOperLogRepo(session).add(str(user["sub"]), "bot.key_revoke", str(bot["bot_key"]), {})
    await session.commit()


# ── 公开渠道 ──


@public_router.post(
    "/bot/{bot_key}/chat",
    response_model=BotChatOut,
    responses={
        429: {
            "description": (
                "请求超过速率限制(detail=CHAT_RATE_LIMITED);X-Quota-Warning 可能包含预警。"
            )
        }
    },
)
async def bot_chat_public(
    bot_key: str,
    req: BotChatIn,
    svc: Annotated[BotService, Depends(get_bot_service)],
) -> BotChatOut:
    """web 嵌入公开端点(免登录):is_public+enabled 闸;护栏与工具防御全量。
    (助手标识, 客户端 IP) 维度速率闸——该面无用户/租户维度可用,IP 取自
    ClientIpMiddleware contextvar(经 --forwarded-allow-ips 约束,非客户端可
    伪造),fail-open(Redis 不可用放行,与 工具限流同口径)。"""
    try:
        await build_bot_rate_gate().check_bot(bot_key=bot_key, client_ip=client_ip_var.get())
    except ChatRateLimited:
        raise HTTPException(status_code=429, detail="CHAT_RATE_LIMITED") from None
    try:
        out = await svc.run(bot_key=bot_key, question=req.question, channel="web_embed")
    except BotNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    return BotChatOut(
        success=bool(out["success"]),
        summary=str(out["summary"]),
        error=None if out["error"] is None else str(out["error"]),
    )


@public_router.post(
    "/v1/chat/completions",
    responses={429: {"description": "请求超过配额限制;X-Quota-Warning 可能包含预警。"}},
)
async def openai_chat(
    req: OpenAiChatIn,
    svc: Annotated[BotService, Depends(get_bot_service)],
    x_api_key: Annotated[str | None, Header()] = None,
) -> object:
    """OpenAI 兼容端点:api-key 鉴权(scopes 限 bot);取最后一条 user 消息。
    配额切面(超额 429;达预警线 X-Quota-Warning 响应头标记)。"""
    if not x_api_key:
        raise HTTPException(status_code=401, detail="API_KEY_REQUIRED") from None
    try:
        key_info = await svc.resolve_api_key(x_api_key)
    except ApiKeyInvalid:
        raise HTTPException(status_code=401, detail="API_KEY_INVALID") from None
    question = next((m.content for m in reversed(req.messages) if m.role == "user"), "")
    if not question.strip():
        raise HTTPException(status_code=400, detail="EMPTY_QUESTION") from None
    # 配额切面(执行前 fail-closed;无策略无套餐=零判定)
    allowed, dimension, quota_warn = await _check_gateway_quota(key_info)
    if not allowed:
        raise HTTPException(status_code=429, detail=f"QUOTA_EXCEEDED: {dimension}") from None
    try:
        out = await svc.run(
            bot_key=str(key_info["bot_key"]),
            question=question,
            channel="api",
            tenant_id_hint=str(key_info["tenant_id"]),
        )
    except BotNotFound:
        raise HTTPException(status_code=404, detail="BOT_NOT_FOUND") from None
    body = _completion_body(str(key_info["bot_key"]), out)
    if quota_warn:
        return _json_response(body, headers={"X-Quota-Warning": quota_warn})
    return body


async def _check_gateway_quota(key_info: dict[str, object]) -> tuple[bool, str, str]:
    """网关配额判定(独立会话;拒前先落审计并显式提交);返回 (放行, 超限维度, 预警头)。"""
    from kongming.auth.repository import SqlOperLogRepo
    from kongming.billing.repository import SqlBillingRepo
    from kongming.billing.service import BillingService
    from kongming.db import get_sessionmaker as _gsm

    class _QuotaOper:
        def __init__(self, s: object) -> None:
            self._r = SqlOperLogRepo(s)  # type: ignore[arg-type]

        async def log(self, *, action: str, target: str, detail: dict[str, object]) -> None:
            await self._r.add(None, action, target, detail)

    async with _gsm()() as qs:
        # 审计注入(spec「429 并落审计」)+提交(独立会话须显式
        # commit,否则审计随会话关闭回滚)
        allowed, dimension, quota_warn = await BillingService(
            SqlBillingRepo(qs), _QuotaOper(qs)
        ).check_gateway_quota(
            tenant_id=str(key_info["tenant_id"]),
            bot_id=str(key_info["bot_id"]),  # BOT 级配额双路判定
        )
        if not allowed:
            await qs.commit()  # 拒前落审计
    return allowed, dimension, quota_warn


def _completion_body(bot_key: str, out: dict[str, object]) -> dict[str, object]:
    """OpenAI 兼容应答体;失败形态 summary 为空时 error 兜底(触顶/护栏拒绝/
    不可用)——集成方不再拿到静默空 content;error 已过输出护栏。"""
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),  # OpenAI SDK 必填
        "model": bot_key,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": str(out["summary"] or out["error"] or ""),
                },
                "finish_reason": "stop",
            }
        ],
        "usage": None,  # 用量经 sys_llm_usage,v1 响应不回填
    }


def _json_response(body: dict[str, object], *, headers: dict[str, str]) -> JSONResponse:
    """带附加响应头(如 X-Quota-Warning)的 JSON 应答。"""
    return JSONResponse(body, headers=headers)
