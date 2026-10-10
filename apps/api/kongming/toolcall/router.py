"""toolcall 路由(019,tool-calling spec):

- 内部面:POST /internal/tool/execute(全局内部令牌 middleware 守门,ADR-002);
- 用户面:POST /chat/tool-confirm(写工具人工确认;chat:execute + 归属校验);
- 管理面:GET /system/tools(注册表清单只读)+ GET /system/tools/audit/page
  (system:tool:manage,行级可见性非 ALL 强制本人——014/015 同口径)。

组合根:端口绑定在此(internal 执行路由适配/RagService 同源工厂/chat 仓储适配)。
"""

import logging
import shutil
import uuid
from datetime import datetime
from functools import lru_cache
from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.repository import SqlPermRepo
from kongming.auth.router import current_user, require_perm
from kongming.chat.repository import ChatRepository
from kongming.config import get_settings
from kongming.datasource.cipher import get_cipher
from kongming.db import get_session
from kongming.internal import service as internal_service
from kongming.mcp.client import HttpMcpCaller
from kongming.mcp.repository import SqlMcpRepo
from kongming.mcp.service import McpService
from kongming.rag.router import get_rag_service
from kongming.redis_client import get_redis
from kongming.sandbox.repository import SqlSandboxRunRepo
from kongming.sandbox.runner import PodmanSandboxRunner
from kongming.sandbox.service import SandboxService
from kongming.toolcall import registry
from kongming.toolcall.models import (
    ConfirmNotFound,
    TenantBindingForbidden,
    TenantNotFound,
    ToolDef,
    ToolOutcome,
)
from kongming.toolcall.ratelimit import RedisRateLimiter
from kongming.toolcall.registry import LOCAL_HOSTS, REGISTRY, is_local_base_url
from kongming.toolcall.repository import (
    SqlToolCallRepo,
    ToolAuditWriter,
    user_has_all_scope,
)
from kongming.toolcall.service import ConfirmOutcome, ToolCallService
from kongming.toolgateway.gateway import ToolGateway
from kongming.toolgateway.knowledge import KnowledgeExecutor, KnowledgePolicy
from kongming.toolgateway.models import DOMAIN_TOOLS, load_manifest

log = logging.getLogger(__name__)

# 内部面(守门由 main 全局 middleware 承担)与管理面/用户面三个 router
internal_router = APIRouter(prefix="/internal/tool", tags=["internal"])
router = APIRouter(prefix="/system/tools", tags=["toolcall"])
chat_router = APIRouter(prefix="/chat", tags=["chat"])


# ── 端口适配(组合根) ──


class _InternalSqlExecutor:
    """execute_sql 工具委托:既有执行路由全链路(防火墙+data_scope+只读池)零复制。"""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def run(
        self,
        *,
        trace_id: str,
        tenant_key: str,
        datasource_key: str,
        sql_text: str,
        user_id: str,
        question: str | None,
    ) -> dict[str, object]:
        result = await internal_service.execute(
            self._s,
            trace_id=trace_id,
            tenant_key=tenant_key,
            datasource_key=datasource_key,
            sql_text=sql_text,
            user_id=user_id,
            question=question,
            limit=1000,
        )
        return {
            "verdict": result.verdict,
            "reason": result.reason,
            "columns": result.columns,
            "rows": result.rows,
            "row_count": result.row_count,
            "duration_ms": result.duration_ms,
        }


class _RagServiceAdapter:
    """rag_retrieve 工具委托:既有三路召回(RagService.recall)。

    零命中回退(019 实施期评估实证):召回 schema/history 双空时回退实时内省
    ——与老链路 retrieve 节点「RAG 优先、内省兜底」同语义;ReAct 内核无独立
    内省工具,工具内回退使新租户/未扫描知识的数据源零质量回归(否则 LLM
    只能盲目探测 information_schema,评估实证循环打满上限)。
    """

    def __init__(self, session: AsyncSession) -> None:
        self._svc = get_rag_service(session)
        self._s = session

    async def _enrich(
        self, tenant_key: str, datasource_key: str, question: str, schema_hint: str
    ) -> dict[str, object]:
        """023 语义段组装(与 rag retrieve 端点同构;降级空串)。"""
        import re as _re

        from sqlalchemy import text as _text

        from kongming.semantic.repository import SqlSemanticRepo
        from kongming.semantic.service import SemanticService

        try:
            tid = (
                await self._s.execute(
                    _text(
                        "SELECT id FROM public.sys_tenant WHERE tenant_key=:k AND status='ACTIVE'"
                    ),
                    {"k": tenant_key},
                )
            ).scalar_one_or_none()
            if tid is None:
                return {"relations_hint": "", "metric_hints": ""}
            tables = _re.findall(r"^([\w]+\.[\w]+)\(", schema_hint, _re.M)
            relations_hint, metric_hints, _mhits = await SemanticService(
                SqlSemanticRepo(self._s)
            ).enrich(
                tenant_id=str(tid),
                datasource_key=datasource_key,
                question=question,
                tables=tables,
            )
            return {"relations_hint": relations_hint, "metric_hints": metric_hints}
        except Exception:
            log.warning("ReAct 语义段组装失败(降级空串)", exc_info=True)
            return {"relations_hint": "", "metric_hints": ""}

    async def retrieve(
        self, *, tenant_key: str, datasource_key: str, question: str
    ) -> dict[str, object]:
        ctx = await self._svc.recall(
            tenant_key=tenant_key, datasource_key=datasource_key, question=question
        )
        if ctx.schema_hint.strip() or ctx.history_sql.strip():
            out: dict[str, object] = {
                "schema_hint": ctx.schema_hint,
                "history_sql": ctx.history_sql,
            }
            # R1-M2:ReAct 链路语义段(与 /internal/rag/retrieve 同源组装;
            # 此前 ReAct 主路径对语义层零收益)
            out.update(await self._enrich(tenant_key, datasource_key, question, ctx.schema_hint))
            return out
        from kongming.internal.meta import MetaSchemaIn, meta_schema

        try:
            meta = await meta_schema(
                MetaSchemaIn(tenant_key=tenant_key, datasource_key=datasource_key), self._s
            )
        except Exception:
            # 内省失败降级空提示(不误导、不硬编码——与老链路内省失败同口径)
            log.warning("rag_retrieve 回退内省失败 tenant=%s ds=%s", tenant_key, datasource_key)
            return {"schema_hint": "", "history_sql": ""}
        out2: dict[str, object] = {
            "schema_hint": _format_meta_hint(meta),
            "history_sql": ctx.history_sql,
        }
        out2.update(
            await self._enrich(tenant_key, datasource_key, question, out2["schema_hint"])  # type: ignore[arg-type]
        )
        return out2


def _format_meta_hint(meta: object) -> str:
    """实时内省结果 → schema 提示文本(与 8 节点图 retrieve 回退同形态)。"""
    from kongming.internal.meta import MetaSchemaOut

    if not isinstance(meta, MetaSchemaOut):
        return ""
    lines: list[str] = []
    for t in meta.tables:
        header = f"表 {t.schema_}.{t.name}"
        if t.comment:
            header += f":{t.comment}"
        lines.append(header)
        for c in t.columns:
            col = f"  - {c.name} {c.data_type}"
            if c.comment:
                col += f"({c.comment})"
            lines.append(col)
    if meta.truncated:
        lines.append(f"(表数量超上限,仅列前 {len(meta.tables)} 张)")
    return "\n".join(lines)


class _ChatReplyStore:
    """确认回执落会话消息(chat 仓储适配;归属校验与 chat 404 同形)。"""

    def __init__(self, session: AsyncSession) -> None:
        self._repo = ChatRepository(session)

    async def session_owned(self, *, session_id: str, user_id: str, tenant_id: str) -> bool:
        return (
            await self._repo.find_session_owned(
                session_id=session_id, user_id=user_id, tenant_id=tenant_id
            )
            is not None
        )

    async def append_reply(self, *, session_id: str, content: str) -> None:
        await self._repo.insert_message(
            session_id=session_id, role="ASSISTANT", summary=content, status="SUCCESS"
        )


class _GuardrailIntentReviewer:
    """020 意图审核端口绑定:guardrail 域服务(独立短会话读配置)。"""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def allow(
        self,
        *,
        trace_id: str,
        user_id: str,
        tenant_id: str,
        tool_write: bool,
        write_attempts: int,
    ) -> bool:
        from kongming.guardrail.repository import (
            SqlGuardrailAuditWriter,
            SqlGuardrailRepo,
        )
        from kongming.guardrail.service import GuardrailService

        svc = GuardrailService(SqlGuardrailRepo(self._s), SqlGuardrailAuditWriter())
        return await svc.review_tool_intent(
            trace_id=trace_id,
            user_id=user_id,
            tenant_id=tenant_id,
            tool_write=tool_write,
            write_attempts=write_attempts,
        )


def _build_sandbox() -> SandboxService | None:
    """027 组合根(design D6):enabled 且宿主 podman 可用才接 runner;
    否则 None(handler 收敛 ERROR「沙箱未启用」+审计,fail-closed 不裸 500)。
    模块级 import 安全:sandbox 域零副作用(podman 调用在运行期子进程)。"""
    settings = get_settings()
    if not settings.sandbox_enabled:
        return None
    if shutil.which("podman") is None:
        log.warning("KM_SANDBOX_ENABLED=true 但宿主无 podman,沙箱按未启用降级")
        return None
    return SandboxService(
        runner=PodmanSandboxRunner(
            image=settings.sandbox_image,
            timeout_sec=settings.sandbox_timeout_sec,
            mem_mb=settings.sandbox_mem_mb,
            cpus=settings.sandbox_cpus,
        ),
        writer=SqlSandboxRunRepo(),
    )


def _build_mcp_service(session: AsyncSession) -> McpService:
    """028:mcp 域服务装配(动态解析与出站执行共用同一服务实例)。"""
    settings = get_settings()
    return McpService(
        SqlMcpRepo(session, get_cipher(settings.datasource_encrypt_key)),
        HttpMcpCaller(timeout_sec=settings.mcp_outbound_timeout_sec),
    )


def _build_gateway() -> ToolGateway | None:
    """089:knowledge 域网关装配(端点未配置或主机非白名单 → None,零感知)。
    清单/策略器/执行器三件齐备才注册;策略器缺失的域不注册(fail-closed)。"""
    settings = get_settings()
    url = (settings.siku_base_url or "").strip()
    if not url or not is_local_base_url(url):
        return None
    manifest, warnings = load_manifest(
        [
            {
                "id": "knowledge",
                "endpoint": url.rstrip("/") + "/api/search",
                # 白名单单源(审查 P1-2):与注册闸 is_local_base_url 同一主机集,
                # 否则 host.docker.internal/::1 等部署形态「注册成功但每次必拒」
                "whitelist_hosts": sorted(LOCAL_HOSTS),
                "timeout_ms": settings.siku_timeout_ms,
                "quota_per_minute": settings.toolgateway_knowledge_quota,
                "redaction": "allowlist:knowledge",
            }
        ]
    )
    for w in warnings:
        log.warning("%s", w)
    if "knowledge" not in manifest:
        return None
    return ToolGateway(
        manifest=manifest,
        # 策略器白名单从清单派生(单一真理源)
        policies={
            "knowledge": KnowledgePolicy(whitelist_hosts=manifest["knowledge"].whitelist_hosts)
        },
        executor=KnowledgeExecutor(),
    )


class _GatewayKnowledgeClient:
    """089 KnowledgeSearchPort 绑定:出站经网关切面(出口校验→限流→出站→
    脱敏→重试分类),本适配层只做域方言整形(count/hits 观察形态与 081 逐字
    一致);失败收敛 KnowledgeSourceError(文案零漂移,分发兜底 ERROR+审计)。"""

    def __init__(self, gateway: ToolGateway) -> None:
        self._gw = gateway

    async def search(self, *, query: str, top_k: int) -> list[dict[str, object]]:
        from kongming.toolgateway.gateway import KnowledgeSourceError

        out = await self._gw.execute(
            tool_name="knowledge_search",
            args={"query": query, "top_k": top_k},
            domain="knowledge",
            trace_id="-",  # trace 经工具分发层审计承载;适配层不重复落
            tenant_id="-",
            user_id="-",
        )
        if out["status"] != "executed":
            raise KnowledgeSourceError(str(out.get("reason") or "unreachable: 知识库拒连"))
        result = cast("dict[str, object]", out.get("result") or {})
        hits = cast("list[dict[str, object]]", result.get("results") or [])
        return hits


@lru_cache(maxsize=1)
def _get_gateway() -> ToolGateway | None:
    """089:网关进程单例(停用集合跨请求一致;清单随部署配置,重启复位)。"""
    return _build_gateway()


def _build_knowledge_client() -> _GatewayKnowledgeClient | None:
    """081/089:端点未配置或主机非 localhost 白名单 → None(工具不注册)。"""
    gw = _get_gateway()
    return _GatewayKnowledgeClient(gw) if gw is not None else None


class _McpDynamicResolver:
    """028 DynamicToolResolver 绑定:快照命中 → fail-closed ToolDef。"""

    def __init__(self, svc: McpService) -> None:
        self._svc = svc

    async def resolve(self, *, tenant_id: str, name: str) -> ToolDef | None:
        entry = await self._svc.resolve_tool(tenant_id=tenant_id, qualified=name)
        if entry is None:
            return None
        read_only = bool(entry.get("read_only"))
        schema = entry.get("schema")
        return ToolDef(
            name=name,
            description=str(entry.get("description") or ""),
            parameters=cast("dict[str, Any]", schema)
            if isinstance(schema, dict)
            else {"type": "object"},
            perm_code="tool:mcp:use",
            write=not read_only,  # fail-closed:未标注 readOnlyHint=写工具
            require_confirm=not read_only,
        )


class _McpToolCaller:
    """028 McpToolCaller 绑定(出站执行经 mcp 域服务)。"""

    def __init__(self, svc: McpService) -> None:
        self._svc = svc

    async def call(
        self, *, tenant_id: str, qualified: str, args: dict[str, object]
    ) -> dict[str, object]:
        return await self._svc.call_tool(tenant_id=tenant_id, qualified=qualified, args=args)


def _get_service(session: Annotated[AsyncSession, Depends(get_session)]) -> ToolCallService:
    settings = get_settings()
    mcp_svc = _build_mcp_service(session)  # 028
    return ToolCallService(
        repo=SqlToolCallRepo(session),
        writer=ToolAuditWriter(),
        perms=SqlPermRepo(session),
        limiter=RedisRateLimiter(get_redis()),
        sql_executor=_InternalSqlExecutor(session),
        rag_retriever=_RagServiceAdapter(session),
        reply_store=_ChatReplyStore(session),
        intent=_GuardrailIntentReviewer(session),  # 020 意图审核
        sandbox=_build_sandbox(),  # 027
        resolver=_McpDynamicResolver(mcp_svc),  # 028
        mcp_caller=_McpToolCaller(mcp_svc),  # 028
        knowledge=_build_knowledge_client(),  # 081(未配置=None)
        knowledge_quota=getattr(get_settings(), "toolgateway_knowledge_quota", None),  # 089 域配额
        rate_limit_per_minute=settings.tool_rate_limit_per_minute,
        confirm_ttl_minutes=settings.tool_confirm_ttl_minutes,
    )


async def _mcp_tenant_or_none(
    user: dict[str, object], session: AsyncSession, header: str | None
) -> str | None:
    repo = ChatRepository(session)
    uid = str(user["sub"])
    if header is not None:
        return await repo.find_bound_tenant(user_id=uid, tenant_key=header)
    default = await repo.find_default_tenant(user_id=uid)
    return None if default is None else str(default[0])


# ── 内部面:通用工具执行回调 ──


class ToolExecuteIn(BaseModel):
    """Agent 面 → 控制面的工具执行回调(ADR-002:Agent 不持凭证,只传标识与文本)。"""

    trace_id: str = Field(min_length=1, max_length=64)
    tenant_key: str = Field(min_length=1, max_length=64)
    datasource_key: str = Field(default="default", pattern=r"^[a-z][a-z0-9_]{1,63}$")
    user_id: uuid.UUID  # 必填:工具权限点校验需要真实主体(无系统调用豁免,spec)
    tool_name: str = Field(min_length=1, max_length=64)
    args: dict[str, object] = {}
    idempotency_key: str | None = Field(default=None, max_length=128)
    # 025:渠道标记(审计可溯;agent 透传)
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


def _to_out(outcome: ToolOutcome) -> ToolExecuteOut:
    return ToolExecuteOut(
        status=_OUT_STATUS[outcome.status],  # type: ignore[arg-type]  # 五态全覆盖
        reason=outcome.reason,
        result=outcome.result,
        confirm_id=outcome.confirm_id,
        duration_ms=outcome.duration_ms,
    )


@internal_router.post("/execute", response_model=ToolExecuteOut)
async def tool_execute(
    req: ToolExecuteIn,
    svc: Annotated[ToolCallService, Depends(_get_service)],
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
    return _to_out(outcome)


# ── 用户面:写工具人工确认 ──


class ToolConfirmIn(BaseModel):
    confirm_id: uuid.UUID
    decision: Literal["confirm", "reject"]
    session_id: uuid.UUID


class ToolConfirmOut(BaseModel):
    status: Literal["executed", "confirm_rejected", "confirm_expired", "rejected", "error"]
    reason: str | None = None
    result: dict[str, object] | None = None


_CONFIRM_OUT_STATUS = {
    "executed": "executed",
    "confirm_rejected": "confirm_rejected",
    "confirm_expired": "confirm_expired",
    "rejected": "rejected",
    "error": "error",
}


@chat_router.post("/tool-confirm", response_model=ToolConfirmOut)
async def tool_confirm(
    req: ToolConfirmIn,
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[ToolCallService, Depends(_get_service)],
    _: Annotated[None, Depends(require_perm("chat:execute"))],
) -> ToolConfirmOut:
    try:
        outcome: ConfirmOutcome = await svc.confirm(
            user_id=str(user["sub"]),
            confirm_id=str(req.confirm_id),
            decision=req.decision,
            session_id=str(req.session_id),
        )
    except ConfirmNotFound:
        # 不存在/不属本人/会话不属本人同形 404(不暴露存在性)
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    except TenantBindingForbidden:
        # R1-M3:确认时 L1 复校——挂起后用户被解绑租户,拒绝执行
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None
    return ToolConfirmOut(
        status=_CONFIRM_OUT_STATUS[outcome.status],  # type: ignore[arg-type]
        reason=outcome.reason,
        result=outcome.result,
    )


# ── 管理面:工具清单与审计查询 ──


class ToolItemOut(BaseModel):
    name: str
    description: str
    perm_code: str
    write: bool
    require_confirm: bool
    enabled: bool = True  # 注册表即启用集(配置化启停随运营诉求评估,挂账)
    server_key: str | None = None  # 028:MCP 工具溯源(内置为 None)


class ToolListOut(BaseModel):
    tools: list[ToolItemOut]


@router.get("", response_model=ToolListOut)
async def list_tools(
    _: Annotated[None, Depends(require_perm("system:tool:manage"))],
    user: Annotated[dict[str, object], Depends(current_user)],
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
    # 028:合并租户 MCP 动态工具(溯源 server_key);租户未绑定 → 仅静态集
    tenant_id = await _mcp_tenant_or_none(user, session, x_tenant_id)
    if tenant_id is not None:
        mcp_svc = _build_mcp_service(session)
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
    # 089 裁决批准(2026-10-10 随批落地):域标签读面投影(存量行/内置工具
    # 为 NULL→None);纯增可选,老客户端零回归
    domain: str | None = None
    idempotency_key: str | None
    expires_at: datetime | None
    created_at: datetime


class ToolAuditPageOut(BaseModel):
    items: list[ToolAuditItem]
    total: int
    page: int
    size: int


def _audit_item(r: dict[str, object]) -> ToolAuditItem:
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


# ── 089 域治理面:清单摘要(不含凭证与白名单明细)+ 运行时启停(重启随部署清单复位)──


def _summary_ids(gw: ToolGateway | None) -> set[str]:
    return {str(d["id"]) for d in (gw.summary() if gw else [])}


@router.get(
    "/domains",
    dependencies=[Depends(require_perm("system:tool:manage"))],
)
async def list_domains() -> list[dict[str, object]]:
    """治理面清单摘要(不含凭证与白名单明细;disabled 反映本进程运行时状态)。"""
    gw = _get_gateway()
    return gw.summary() if gw else []


def _sync_runtime_disabled() -> None:
    """停用域 → 其工具从注入集与分发解析同源消失(spec 域治理面场景;
    DOMAIN_TOOLS 反演域下工具集)。"""
    gw = _get_gateway()
    disabled_domains: set[str] = set()
    if gw is not None:
        disabled_domains = {str(d["id"]) for d in gw.summary() if bool(d.get("disabled"))}
    registry.set_runtime_disabled(
        frozenset(t for t, d in DOMAIN_TOOLS.items() if d in disabled_domains)
    )


async def load_persisted_domain_states() -> None:
    """0044:启动装载持久停用集(lifespan 调用;测试可直调模拟重启)。
    库为运维意图单源:装载=替换语义;无网关(清单空)静默跳过。
    装载后同步运行时注册表(_sync_runtime_disabled,与 disable 端点同口径)——
    否则重启后停用域的工具仍入注入集、分发仍可解析,持久化语义静默降级为
    「执行期才被网关拒」。"""
    gw = _get_gateway()
    if gw is None:
        return
    from kongming.db import get_sessionmaker
    from kongming.toolcall.repository import list_domain_states

    async with get_sessionmaker()() as session:
        states = await list_domain_states(session)
    gw.apply_disabled(frozenset(d for d, dis in states.items() if dis))
    _sync_runtime_disabled()


@router.post("/domains/{domain_id}/disable", status_code=204)
async def disable_domain(
    domain_id: str,
    user: Annotated[dict[str, object], Depends(current_user)],
    _: Annotated[None, Depends(require_perm("system:tool:manage"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """停用域(0044 持久化:本进程立即生效 + 写穿;重启与多副本以库收敛)。"""
    gw = _get_gateway()
    if gw is None or domain_id not in _summary_ids(gw):
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    gw.disable(domain_id)
    _sync_runtime_disabled()
    from kongming.toolcall.repository import upsert_domain_state

    await upsert_domain_state(
        session, domain_id=domain_id, disabled=True, updated_by=str(user["sub"])
    )
    await session.commit()


@router.post("/domains/{domain_id}/enable", status_code=204)
async def enable_domain(
    domain_id: str,
    user: Annotated[dict[str, object], Depends(current_user)],
    _: Annotated[None, Depends(require_perm("system:tool:manage"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """启用域(0044 持久化,写穿口径同 disable)。"""
    gw = _get_gateway()
    if gw is None or domain_id not in _summary_ids(gw):
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    gw.enable(domain_id)
    _sync_runtime_disabled()
    from kongming.toolcall.repository import upsert_domain_state

    await upsert_domain_state(
        session, domain_id=domain_id, disabled=False, updated_by=str(user["sub"])
    )
    await session.commit()


@router.get("/audit/page", response_model=ToolAuditPageOut)
async def tool_audit_page(
    user: Annotated[dict[str, object], Depends(current_user)],
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
    viewer_id = str(user["sub"])
    # 行级可见性(spec,014/015 同口径):非 ALL 角色强制本人(覆盖传值)
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
        items=[_audit_item(it) for it in items], total=total, page=page, size=size
    )
