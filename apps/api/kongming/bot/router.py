"""bot 路由(025,custom-agent spec):管理端点(归属收口)+ 公开 web 嵌入 +
OpenAI 兼容 API(api-key 鉴权)。

组合根:ReactRunner 适配 chat AgentClient;BotGuardrail 适配 020 服务。
"""

import logging
import uuid
from datetime import datetime
from typing import Annotated, cast

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.audit.context import client_ip_var
from kongming.auth.repository import SqlOperLogRepo, SqlPermRepo
from kongming.auth.router import current_user, require_perm
from kongming.bot.models import (
    InvalidBotKey,
    ToolSubsetViolation,
    tools_for_perms,
    validate_tool_subset,
)
from kongming.bot.service import (
    ApiKeyInvalid,
    BotNotFound,
    BotService,
    GuardrailRejected,
)
from kongming.chat.agent_client import AgentClient
from kongming.chat.costgate import ChatCostGate, ChatRateLimited
from kongming.chat.router import get_agent_client
from kongming.config import get_settings
from kongming.db import get_session, get_sessionmaker
from kongming.redis_client import get_redis
from kongming.toolcall.ratelimit import RedisRateLimiter
from kongming.toolcall.repository import user_has_all_scope

log = logging.getLogger(__name__)

router = APIRouter(prefix="/system/bots", tags=["bot"])
public_router = APIRouter(tags=["bot-public"])


# ── 组合根适配 ──


class _AgentReactRunner:
    def __init__(self, agent: AgentClient) -> None:
        self._agent = agent

    async def list_models(self) -> list[str]:
        return await self._agent.list_models()

    async def run_react(
        self,
        *,
        trace_id: str,
        tenant_key: str,
        question: str,
        user_id: str,
        datasource_key: str,
        tools: list[dict[str, object]],
        bot_system_prompt: str,
        model: str | None = None,
        channel: str = "chat",
    ) -> dict[str, object]:
        reply = await self._agent.query_react(
            trace_id=trace_id,
            tenant_key=tenant_key,
            question=question,
            user_id=user_id,
            history=[],
            datasource_key=datasource_key,
            tools=tools,
            bot_system_prompt=bot_system_prompt,
            model=model,
            channel=channel,
            collect_usage=True,  # 035:bot 链路请求内同步采集(R1-S2 按调用方挂载)
        )
        return {
            "success": reply.success,
            "summary": reply.summary,
            "sql": reply.sql,
            "columns": reply.columns,
            "rows": reply.rows,
            "stats": reply.stats,
            "retry_count": reply.retry_count,
            "error": reply.error,
            "usage_records": reply.usage_records,  # 035:同步采集透传
        }


class _BotGuardrailAdapter:
    async def check_input(
        self, *, trace_id: str, user_id: str, tenant_id: str, texts: list[str]
    ) -> tuple[bool, list[str]]:
        from kongming.guardrail.repository import (
            SqlGuardrailAuditWriter,
            SqlGuardrailRepo,
        )
        from kongming.guardrail.service import GuardrailService

        async with get_sessionmaker()() as s:
            svc = GuardrailService(SqlGuardrailRepo(s), SqlGuardrailAuditWriter())
            verdict = await svc.evaluate_input(
                trace_id=trace_id, user_id=user_id, tenant_id=tenant_id, texts=texts
            )
        return verdict.blocked, verdict.processed

    async def filter_output(
        self, *, trace_id: str, user_id: str, tenant_id: str, texts: list[str]
    ) -> list[str]:
        from kongming.guardrail.repository import (
            SqlGuardrailAuditWriter,
            SqlGuardrailRepo,
        )
        from kongming.guardrail.service import GuardrailService

        async with get_sessionmaker()() as s:
            svc = GuardrailService(SqlGuardrailRepo(s), SqlGuardrailAuditWriter())
            return await svc.evaluate_outputs(
                trace_id=trace_id,
                user_id=user_id,
                tenant_id=tenant_id,
                texts=texts,
                safe_replacement="答案未通过安全检查,已省略。",
            )


class _McpDynamicToolLister:
    """028 DynamicToolLister 绑定(mcp 域服务装配,与 toolcall 组合根同构)。"""

    def __init__(self, session: AsyncSession) -> None:
        from kongming.config import get_settings
        from kongming.datasource.cipher import get_cipher
        from kongming.mcp.client import HttpMcpCaller
        from kongming.mcp.repository import SqlMcpRepo
        from kongming.mcp.service import McpService

        settings = get_settings()
        self._svc = McpService(
            SqlMcpRepo(session, get_cipher(settings.datasource_encrypt_key)),
            HttpMcpCaller(timeout_sec=settings.mcp_outbound_timeout_sec),
        )

    async def list_dynamic(self, *, tenant_id: str) -> list[dict[str, object]]:
        return await self._svc.list_dynamic_tools(tenant_id=tenant_id)


def _build_bot_rate_gate() -> ChatCostGate:
    """062:公开嵌入面速率闸(仅 (bot_key, ip) 分钟级;无用户/租户维度),
    复用 chat 域零框架闸模块(单一 fail-open 口径,不复制限流逻辑)。"""
    settings = get_settings()
    return ChatCostGate(
        limiter=RedisRateLimiter(get_redis()),
        bot_rate_per_minute=settings.bot_chat_rate_per_minute,
    )


def get_bot_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    agent: Annotated[AgentClient, Depends(get_agent_client)],
) -> BotService:
    from kongming.bot.repository import SqlBotRepo

    return BotService(
        SqlBotRepo(session),
        SqlPermRepo(session),
        _AgentReactRunner(agent),
        _BotGuardrailAdapter(),
        SqlOperLogRepo(session),  # R1-L2:写操作审计
        _McpDynamicToolLister(session),  # 028
        _UsageSinkAdapter(),  # 035:bot 用量请求内落库(独立短会话,B1-B9)
    )


def build_bot_service_with_session(session: AsyncSession) -> BotService:
    """非 DI 组合工厂(adopt-mcp-sdk2:/mcp 入站 ASGI 中间件消费;与
    get_bot_service 同源装配,防两处漂移)。agent 客户端经 _resolve_agent_client
    (覆写感知,测试替身注入路径与 DI 同形)。"""
    from kongming.bot.repository import SqlBotRepo

    return BotService(
        SqlBotRepo(session),
        SqlPermRepo(session),
        _AgentReactRunner(_resolve_agent_client()),
        _BotGuardrailAdapter(),
        SqlOperLogRepo(session),  # R1-L2:写操作审计
        _McpDynamicToolLister(session),  # 028
        _UsageSinkAdapter(),  # 035:bot 用量请求内落库(独立短会话,B1-B9)
    )


def _resolve_agent_client() -> AgentClient:
    """DI 覆写感知的 agent 客户端解析(非 DI 消费点唯一入口):主 app 的
    dependency_overrides 存在覆写时走覆写(测试替身),否则直调工厂。"""
    from kongming.chat.router import get_agent_client
    from kongming.main import app

    override = app.dependency_overrides.get(get_agent_client)
    if override is not None:
        return cast("AgentClient", override())
    return get_agent_client()


class _UsageSinkAdapter:
    """035 UsageSink 适配(llmusage 仓储;组合根装配,保 bot.service 零框架)。

    独立短会话落库(B1-B9):sink 在 agent 分钟级长外呼之后才被调用,若复用
    请求级会话,外呼期间白占连接池配额,且 sink 自 commit 会提前提交请求会话
    上的挂起写——与 _BotGuardrailAdapter 同款独立短会话范式。
    """

    async def insert_bot_usage(
        self, records: list[dict[str, object]], *, tenant_id: str, bot_id: str
    ) -> int:
        from kongming.db import get_sessionmaker
        from kongming.llmusage.repository import SqlLlmUsageRepo

        async with get_sessionmaker()() as s:
            return await SqlLlmUsageRepo(s).insert_bot_usage_batch(
                records, tenant_id=tenant_id, bot_id=bot_id
            )


async def _team_ids(session: AsyncSession, user_id: str) -> list[str]:
    """032:用户所属团队 id 集(列表可见性双支用)。"""
    from kongming.team.repository import SqlTeamRepo

    return list((await SqlTeamRepo(session).role_map(user_id=user_id)).keys())


async def _team_role(session: AsyncSession, user_id: str, team_id: str | None) -> str | None:
    """032:单资源的团队角色(编辑/删除判定);无团队 None。"""
    if team_id is None:
        return None
    from kongming.team.repository import SqlTeamRepo

    return await SqlTeamRepo(session).get_member_role(team_id=team_id, user_id=user_id)


async def _tenant_of(session: AsyncSession, user_id: str, tenant_key: str | None) -> str:
    """与 memory/semantic 同构(ACTIVE+default 优先+tiebreak)。
    SQL 单一真理源=kongming.tenant.resolve_tenant(B1-B10 收口)。"""
    from kongming.tenant import resolve_tenant

    resolved = await resolve_tenant(session, user_id=user_id, tenant_key=tenant_key)
    if resolved is None:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None
    return resolved[0]


# ── 管理端点(登录+chat:execute;归属收口创建者或超管) ──


class BotIn(BaseModel):
    bot_key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    name: str = Field(min_length=1, max_length=128)
    system_prompt: str = Field(min_length=1, max_length=4000)
    tools: list[str] = Field(default_factory=list)
    datasource_key: str = Field(default="default", pattern=r"^[a-z][a-z0-9_]{1,63}$")
    model: str | None = Field(default=None, max_length=64)
    is_public: bool = False


class BotOut(BaseModel):
    id: str
    bot_key: str
    name: str
    system_prompt: str
    tools: list[str]
    datasource_key: str
    model: str | None
    enabled: bool
    is_public: bool
    published_at: str | None = None  # 033:上架态(None=未上架)
    team_id: str | None = None  # 036⑥:归属回传(None=个人;契约纯增)
    created_at: str


def _iso_or_none(v: object) -> str | None:
    return v.isoformat() if isinstance(v, datetime) else None


def _bot_out(r: dict[str, object]) -> BotOut:
    return BotOut(
        id=str(r["id"]),
        bot_key=str(r["bot_key"]),
        name=str(r["name"]),
        system_prompt=str(r["system_prompt"]),
        tools=(
            [str(t) for t in cast("list[object]", r["tools"])]
            if isinstance(r["tools"], list)
            else []
        ),
        datasource_key=str(r["datasource_key"]),
        model=None if r["model"] is None else str(r["model"]),
        enabled=bool(r["enabled"]),
        is_public=bool(r["is_public"]),
        published_at=_iso_or_none(r.get("published_at")),
        team_id=str(r["team_id"]) if r.get("team_id") else None,  # 036⑥
        created_at=str(r["created_at"]),
    )


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
    return [_bot_out(r) for r in rows]


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


class BotUpdateIn(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    system_prompt: str | None = Field(default=None, max_length=4000)
    tools: list[str] | None = None
    datasource_key: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{1,63}$")
    model: str | None = Field(default=None, max_length=64)
    enabled: bool | None = None
    is_public: bool | None = None


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

    tid = await _tenant_of(session, str(user["sub"]), x_tenant_id)
    all_scope = await user_has_all_scope(session, str(user["sub"]))
    repo = SqlBotRepo(session)
    row = await repo.get_mine(
        bot_id=str(bot_id),
        tenant_id=tid,
        user_id=str(user["sub"]),
        is_admin=all_scope,
        team_ids=await _team_ids(session, str(user["sub"])),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    # 032:编辑=创建者或 MEMBER+(VIEWER 可见不可编辑)
    from kongming.team.models import can_edit as _can_edit

    if not _can_edit(
        team_id=None if row["team_id"] is None else str(row["team_id"]),
        creator_id=str(row["created_by"]),
        user_id=str(user["sub"]),
        member_role=await _team_role(
            session,
            str(user["sub"]),
            None if row["team_id"] is None else str(row["team_id"]),
        ),
    ):
        raise HTTPException(status_code=403, detail="TEAM_ROLE_FORBIDDEN") from None
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    # R1-J4:公开发布(is_public false→true)=创建者或 OWNER 全权——
    # MEMBER 可编辑但不可把团队 Bot 暴露到免登录公网渠道
    # 类型化 bool(|None):真值判断即语义——is True/is not True 字面量恒等
    # 比较对 bool 是冗余噪音,且对"truthy 非 True 值"零容忍反成脆弱点
    if fields.get("is_public") and not row["is_public"]:
        from kongming.team.models import can_manage as _cm4

        if not _cm4(
            team_id=None if row["team_id"] is None else str(row["team_id"]),
            creator_id=str(row["created_by"]),
            user_id=str(user["sub"]),
            member_role=await _team_role(
                session,
                str(user["sub"]),
                None if row["team_id"] is None else str(row["team_id"]),
            ),
        ):
            raise HTTPException(status_code=403, detail="TEAM_ROLE_FORBIDDEN") from None
    # R1-H2:编辑路径 datasource/model 复校
    if "datasource_key" in fields and str(fields["datasource_key"]) != "default":
        try:
            await svc._assert_datasource(tid, str(fields["datasource_key"]))  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        except InvalidBotKey as e:
            raise HTTPException(status_code=400, detail=str(e)) from None
    if "model" in fields:
        available = await svc._runner.list_models()  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        if str(fields["model"]) not in available:
            raise HTTPException(status_code=400, detail="模型未注册") from None
    # 编辑越权与审核:tools/system_prompt 变更时复校
    if "tools" in fields:
        # 审查循环 F3:与 create 同口径——须含 MCP 动态工具集,否则含
        # mcp__ 工具的 Bot 任何一次带 tools 的更新都被静态注册表误拒(工具集被冻结)
        mcp_names = await svc._mcp_names(tid)  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        allowed = tools_for_perms(
            await SqlPermRepo(session).list_codes(str(user["sub"])), mcp_names
        )
        try:
            validate_tool_subset(
                [str(t) for t in cast("list[object]", fields["tools"] or [])],
                allowed,
                registered_names=svc._registered_names(mcp_names),  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
            )
        except ToolSubsetViolation as e:
            raise HTTPException(status_code=400, detail=str(e)) from None
    # J6:name 变更纳入重审(上架后改名绕过审核面收口——
    # 上架内容的「名」与 greeting 同风险面)
    if "name" in fields and row["published_at"] is not None:
        from kongming.guardrail.repository import (
            SqlGuardrailAuditWriter,
            SqlGuardrailRepo,
        )
        from kongming.guardrail.service import GuardrailService

        filtered = await GuardrailService(
            SqlGuardrailRepo(session), SqlGuardrailAuditWriter()
        ).evaluate_outputs(
            trace_id="",
            user_id=str(user["sub"]),
            tenant_id=tid,
            texts=[str(fields["name"])],
            safe_replacement="内容未通过安全检查,已省略。",
        )
        if filtered[0] != str(fields["name"]):
            raise HTTPException(status_code=400, detail="GUARDRAIL_REJECTED") from None
    # L7:system_prompt 变更或开启公开(is_public=true)均重审
    if "system_prompt" in fields or fields.get("is_public"):
        prompt_to_check = str(fields.get("system_prompt") or row["system_prompt"])
        try:
            await svc.validate_prompt(
                user_id=str(user["sub"]), tenant_id=tid, prompt=prompt_to_check
            )
        except GuardrailRejected:
            raise HTTPException(status_code=400, detail="GUARDRAIL_REJECTED") from None
    ok = await repo.update_bot(bot_id=str(bot_id), fields=fields)
    if not ok:
        raise HTTPException(status_code=400, detail="EMPTY_UPDATE") from None
    await SqlOperLogRepo(session).add(  # R1-L2
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
    """R1-L5:Bot 删除(api-key 经 FK CASCADE;归属收口)。"""
    from kongming.bot.repository import SqlBotRepo

    tid = await _tenant_of(session, str(user["sub"]), x_tenant_id)
    all_scope = await user_has_all_scope(session, str(user["sub"]))
    repo = SqlBotRepo(session)
    row = await repo.get_mine(
        bot_id=str(bot_id),
        tenant_id=tid,
        user_id=str(user["sub"]),
        is_admin=all_scope,
        team_ids=await _team_ids(session, str(user["sub"])),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    # 032:删除=创建者或 OWNER(全权)
    from kongming.team.models import can_manage as _can_manage

    if not _can_manage(
        team_id=None if row["team_id"] is None else str(row["team_id"]),
        creator_id=str(row["created_by"]),
        user_id=str(user["sub"]),
        member_role=await _team_role(
            session,
            str(user["sub"]),
            None if row["team_id"] is None else str(row["team_id"]),
        ),
    ):
        raise HTTPException(status_code=403, detail="TEAM_ROLE_FORBIDDEN") from None
    hit = (
        await session.execute(
            text("DELETE FROM public.sys_bot_definition WHERE id=:i RETURNING id"),
            {"i": str(bot_id)},
        )
    ).scalar_one_or_none()
    if hit is None:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    await SqlOperLogRepo(session).add(  # R1-L2
        str(user["sub"]), "bot.delete", str(row["bot_key"]), {}
    )
    await session.commit()


class ApiKeyOut(BaseModel):
    id: str
    name: str
    revoked_at: str | None
    last_used_at: str | None
    created_at: str


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
    from kongming.bot.repository import SqlBotRepo

    tid = await _tenant_of(session, str(user["sub"]), x_tenant_id)
    all_scope = await user_has_all_scope(session, str(user["sub"]))
    row = await SqlBotRepo(session).get_mine(
        bot_id=str(bot_id),
        tenant_id=tid,
        user_id=str(user["sub"]),
        is_admin=all_scope,
        team_ids=await _team_ids(session, str(user["sub"])),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    # R1-J3:key 管理=创建者或 OWNER(全权)
    from kongming.team.models import can_manage as _cm

    if not _cm(
        team_id=None if row["team_id"] is None else str(row["team_id"]),
        creator_id=str(row["created_by"]),
        user_id=str(user["sub"]),
        member_role=await _team_role(
            session,
            str(user["sub"]),
            None if row["team_id"] is None else str(row["team_id"]),
        ),
    ):
        raise HTTPException(status_code=403, detail="TEAM_ROLE_FORBIDDEN") from None
    plain, key_id = await svc.create_key(
        bot_id=str(bot_id), tenant_id=tid, user_id=str(user["sub"]), name=name
    )
    await SqlOperLogRepo(session).add(  # R1-L2
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

    tid = await _tenant_of(session, str(user["sub"]), x_tenant_id)
    all_scope = await user_has_all_scope(session, str(user["sub"]))
    repo = SqlBotRepo(session)
    row = await repo.get_mine(
        bot_id=str(bot_id),
        tenant_id=tid,
        user_id=str(user["sub"]),
        is_admin=all_scope,
        team_ids=await _team_ids(session, str(user["sub"])),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    from kongming.team.models import can_manage as _cm

    if not _cm(
        team_id=None if row["team_id"] is None else str(row["team_id"]),
        creator_id=str(row["created_by"]),
        user_id=str(user["sub"]),
        member_role=await _team_role(
            session,
            str(user["sub"]),
            None if row["team_id"] is None else str(row["team_id"]),
        ),
    ):
        raise HTTPException(status_code=403, detail="TEAM_ROLE_FORBIDDEN") from None
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

    tid = await _tenant_of(session, str(user["sub"]), x_tenant_id)
    all_scope = await user_has_all_scope(session, str(user["sub"]))
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
    bot = await repo.get_mine(
        bot_id=str(row),
        tenant_id=tid,
        user_id=str(user["sub"]),
        is_admin=all_scope,
        team_ids=await _team_ids(session, str(user["sub"])),
    )
    if bot is None:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    # R1-J3 同款闸:key 管理=创建者或 OWNER——审查循环 F2:曾只过 get_mine
    # (团队任意角色成员含 VIEWER 命中),与 create/list 两端点自相矛盾
    from kongming.team.models import can_manage as _cm

    if not _cm(
        team_id=None if bot["team_id"] is None else str(bot["team_id"]),
        creator_id=str(bot["created_by"]),
        user_id=str(user["sub"]),
        member_role=await _team_role(
            session,
            str(user["sub"]),
            None if bot["team_id"] is None else str(bot["team_id"]),
        ),
    ):
        raise HTTPException(status_code=403, detail="TEAM_ROLE_FORBIDDEN") from None
    await repo.revoke_key(key_id=str(key_id), bot_id=str(row))
    await SqlOperLogRepo(session).add(  # R1-L2
        str(user["sub"]), "bot.key_revoke", str(bot["bot_key"]), {}
    )
    await session.commit()


# ── 公开渠道 ──


class BotChatIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class BotChatOut(BaseModel):
    success: bool
    summary: str
    error: str | None = None


@public_router.post(
    "/bot/{bot_key}/chat",
    response_model=BotChatOut,
    responses={429: {"description": "速率超限(062;detail=CHAT_RATE_LIMITED,X-Quota-Warning 预警)"}},
)
async def bot_chat_public(
    bot_key: str,
    req: BotChatIn,
    svc: Annotated[BotService, Depends(get_bot_service)],
) -> BotChatOut:
    """web 嵌入公开端点(免登录):is_public+enabled 闸;护栏与工具防御全量。
    062:(助手标识, 客户端 IP) 维度速率闸——该面无用户/租户维度可用,IP 取自
    ClientIpMiddleware contextvar(经 --forwarded-allow-ips 约束,非客户端可
    伪造),fail-open(Redis 不可用放行,与 019 工具限流同口径)。"""
    try:
        await _build_bot_rate_gate().check_bot(bot_key=bot_key, client_ip=client_ip_var.get())
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


class OpenAiMessage(BaseModel):
    role: str
    content: str


class OpenAiChatIn(BaseModel):
    model: str | None = None
    messages: list[OpenAiMessage]


@public_router.post(
    "/v1/chat/completions",
    responses={429: {"description": "配额超限(034;响应头 X-Quota-Warning 预警)"}},
)
async def openai_chat(
    req: OpenAiChatIn,
    svc: Annotated[BotService, Depends(get_bot_service)],
    x_api_key: Annotated[str | None, Header()] = None,
) -> object:
    """OpenAI 兼容端点:api-key 鉴权(scopes 限 bot);取最后一条 user 消息。
    034:配额切面(超额 429;达预警线 X-Quota-Warning 响应头标记)。"""
    if not x_api_key:
        raise HTTPException(status_code=401, detail="API_KEY_REQUIRED") from None
    try:
        key_info = await svc.resolve_api_key(x_api_key)
    except ApiKeyInvalid:
        raise HTTPException(status_code=401, detail="API_KEY_INVALID") from None
    question = next((m.content for m in reversed(req.messages) if m.role == "user"), "")
    if not question.strip():
        raise HTTPException(status_code=400, detail="EMPTY_QUESTION") from None
    # 034:配额切面(执行前 fail-closed;无策略无套餐=零判定零回归)
    from kongming.billing.repository import SqlBillingRepo
    from kongming.billing.service import BillingService
    from kongming.db import get_sessionmaker as _gsm

    quota_warn = ""
    async with _gsm()() as qs:
        # R1-S1:审计注入(spec「429 并落审计」)+提交(独立会话须显式
        # commit,否则审计随会话关闭回滚)
        from kongming.auth.repository import SqlOperLogRepo

        class _QuotaOper:
            def __init__(self, s: object) -> None:
                self._r = SqlOperLogRepo(s)  # type: ignore[arg-type]

            async def log(self, *, action: str, target: str, detail: dict[str, object]) -> None:
                await self._r.add(None, action, target, detail)

        allowed, dimension, quota_warn = await BillingService(
            SqlBillingRepo(qs), _QuotaOper(qs)
        ).check_gateway_quota(
            tenant_id=str(key_info["tenant_id"]),
            bot_id=str(key_info["bot_id"]),  # 035:BOT 级配额双路判定
        )
        if not allowed:
            await qs.commit()  # R1-S1:拒前落审计
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
    import time

    from fastapi.responses import JSONResponse as _JSONResponse

    body = {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),  # R1-M1:OpenAI SDK 必填
        "model": str(key_info["bot_key"]),
        "choices": [
            {
                "index": 0,
                # R2-F1:失败形态 summary 为空时 error 兜底(触顶/护栏拒绝/
                # 不可用)——集成方不再拿到静默空 content;error 已过输出护栏
                "message": {
                    "role": "assistant",
                    "content": str(out["summary"] or out["error"] or ""),
                },
                "finish_reason": "stop",
            }
        ],
        "usage": None,  # 用量经 sys_llm_usage(021),v1 响应不回填
    }
    # 034:达预警线标记(不拒绝)
    if quota_warn:
        return _JSONResponse(body, headers={"X-Quota-Warning": quota_warn})
    return body
