"""工具调用用例编排，按权限、租户归属、参数校验、审计、限流和人工确认执行防护。

SQL、RAG 与消息回写分别通过仓储和端口调用，端口由路由组合根装配。
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import jsonschema
import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from kongming.sqlfw.firewall import check as sql_check
from kongming.toolcall.models import (
    ConfirmNotFound,
    ConnectorError,
    TenantNotFound,
    ToolAuditEntry,
    ToolDef,
    ToolOutcome,
    ToolStatus,
    WebSearchError,
    args_digest,
    derive_idempotency_key,
)
from kongming.toolcall.ports import (
    AuditWriter,
    ConfirmReplyStore,
    ConnectorToolGate,
    DynamicToolResolver,
    IntentReviewer,
    McpToolCaller,
    PermReader,
    RagRetriever,
    RateLimiter,
    SandboxExecutor,
    SqlExecutor,
    ToolRepo,
    WebSearchPort,
)
from kongming.toolcall.registry import effective, get_tool
from kongming.toolgateway.models import (
    CONNECTOR_DOMAIN,
    CONNECTOR_TOOL_PREFIX,
    DOMAIN_TOOLS,
    FAILURE_UNDELIVERED_PREFIX,
    WEBSEARCH_COUNT_DEFAULT,
)

log = logging.getLogger(__name__)

# 注入载荷按 UTF-8 字节计数，1 MB 上限低于 runner 的 2 MB stdin 限制。
_DATA_INJECT_CAP = 1 * 1024 * 1024


class SandboxUnavailableError(Exception):
    """沙箱未启用(组合根未注入 runner 时的 handler 收敛;
    经分发兜底映射 ERROR+审计,不裸 500)。"""


# SQL 类工具的 sql 参数语义预检(L4 的一部分):JSON Schema 管结构,sqlglot
# 根节点判定管「必须是只读语句形态」——DROP 在到达防火墙前即被参数校验层
# 拒绝(spec scenario 字面语义)。首关键字白名单改为 AST 根判定——
# 注释形态(-- /* */ 前缀的合法 SELECT)不再误拒;语法畸形放行给下游
# (防火墙/执行路由给出整洁错误,LLM 自纠)。
# run_python_code 的 data_sql(可选参数)同规——映射表驱动,参数名按工具取。
_READONLY_ROOTS = (exp.Select, exp.Union, exp.Subquery)

_SQL_PARAM_BY_TOOL: dict[str, str] = {
    "validate_sql": "sql",
    "execute_sql": "sql",
    "run_python_code": "data_sql",
}


def _compute_delta(args: dict[str, object]) -> dict[str, object]:
    """增幅百分比 (target-base)/|base|*100;零副作用纯计算。

    入参白名单校验在 Agent 面(唯一持有本轮结果/stats 白名单);本 handler
    供工作流等控制面通道复用同一公式,不做跨请求白名单臆测。
    """
    base = args.get("base")
    target = args.get("target")
    if not isinstance(base, (int, float)) or isinstance(base, bool):
        return {"status": "error", "reason": "base 必须是数值"}
    if not isinstance(target, (int, float)) or isinstance(target, bool):
        return {"status": "error", "reason": "target 必须是数值"}
    if base == 0:
        return {"status": "error", "reason": "base=0,无法计算增幅"}
    pct = (float(target) - float(base)) / abs(float(base)) * 100.0
    return {
        "status": "executed",
        "delta_pct": round(pct, 4),
        "inputs": {"base": float(base), "target": float(target)},
    }


def _compute_share(args: dict[str, object]) -> dict[str, object]:
    """占比 part/total;与 _compute_delta 同哲学。"""
    part = args.get("part")
    total = args.get("total")
    if not isinstance(part, (int, float)) or isinstance(part, bool):
        return {"status": "error", "reason": "part 必须是数值"}
    if not isinstance(total, (int, float)) or isinstance(total, bool):
        return {"status": "error", "reason": "total 必须是数值"}
    if total == 0:
        return {"status": "error", "reason": "total=0,无法计算占比"}
    ratio = float(part) / float(total)
    return {
        "status": "executed",
        "share": round(ratio, 6),
        "share_pct": round(ratio * 100.0, 4),
        "inputs": {"part": float(part), "total": float(total)},
    }


def validate_args(tool: ToolDef, args: dict[str, object]) -> str | None:
    """L4 参数校验(纯函数):JSON Schema 结构校验 + SQL 工具语义预检。"""
    try:
        jsonschema.validate(args, tool.parameters)
    except jsonschema.ValidationError as e:
        return f"ARGS_INVALID: {e.message[:120]}"
    except Exception as e:  # noqa: BLE001
        # 敌意/畸形 schema(外部 MCP 快照面)会抛非 ValidationError
        # (SchemaError/_WrappedReferencingError 等)——收敛
        # ARGS_INVALID 结构化回灌,不裸 500;注册时 check_schema 降级是
        # 主闸,此处为纵深二层
        return f"ARGS_INVALID: schema 非法({type(e).__name__})"
    sql_param = _SQL_PARAM_BY_TOOL.get(tool.name)
    if sql_param is not None:
        sql = args.get(sql_param)
        if isinstance(sql, str) and sql.strip():
            try:
                stmts = [st for st in sqlglot.parse(sql, dialect="postgres") if st is not None]
            except (SqlglotError, RecursionError):
                # RecursionError 不属于 SqlglotError；也按语法错误交给防火墙处理，避免绕过审计。
                stmts = []
            if len(stmts) > 1:
                # 多语句=非单条 SELECT(schema 明文语义;下游防火墙本会拦,
                # 在参数校验层即拒)
                return f"ARGS_INVALID: {sql_param} 必须为单条语句"
            if stmts and not isinstance(stmts[0], _READONLY_ROOTS):
                return (
                    f"ARGS_INVALID: {sql_param} 必须为只读 SELECT 语句"
                    f"(根节点 {type(stmts[0]).__name__})"
                )
    return None


@dataclass(frozen=True)
class ConfirmOutcome:
    """确认端点结果(传输语义小写)。"""

    status: str  # executed / confirm_rejected / confirm_expired / rejected / error
    reason: str | None = None
    result: dict[str, object] | None = None


@dataclass(frozen=True)
class _HandlerCtx:
    """handler 分派的请求上下文(与工具无关的调用参数平铺)。"""

    trace_id: str
    tenant_key: str
    tenant_id: str
    user_id: str
    datasource_key: str
    idem: str | None


_BuiltinHandler = Callable[
    ["ToolCallService", dict[str, object], _HandlerCtx], Awaitable[dict[str, object]]
]
_FamilyHandler = Callable[
    ["ToolCallService", str, dict[str, object], _HandlerCtx], Awaitable[dict[str, object]]
]


def _sync_handler(
    fn: Callable[[dict[str, object]], dict[str, object]],
) -> _BuiltinHandler:
    """纯函数 handler 适配为分派协程(计算在事件循环内同步完成,与原实现一致)。"""

    async def _handler(
        _svc: ToolCallService, args: dict[str, object], _ctx: _HandlerCtx
    ) -> dict[str, object]:
        return fn(args)

    return _handler


@dataclass(frozen=True)
class _Resolved:
    """确认重放解析结果:工具定义与审计域标签(连接器命中覆写为 http)。"""

    tool: ToolDef
    domain: str | None


def _tool_domain(tool_name: str, connector_hit: bool | None) -> str | None:
    """域标签:连接器命中覆写为 http;静态域工具(含未注册)恒走映射表。"""
    return CONNECTOR_DOMAIN if connector_hit else DOMAIN_TOOLS.get(tool_name)


@dataclass(frozen=True)
class _ExecRequest:
    """工具执行请求(字段平铺;审计收口与各防御层共用)。"""

    trace_id: str
    tenant_key: str
    datasource_key: str
    user_id: str
    tool_name: str
    args: dict[str, object]
    idempotency_key: str | None
    channel: str


class _ExecFinisher:
    """执行审计收口:每次终态落一行审计并计量耗时;PENDING_CONFIRM 以审计行 id
    作确认号(写失败 fail-closed 转 ERROR,挂起态不可用,不假装已挂起)。"""

    def __init__(
        self,
        writer: AuditWriter,
        *,
        req: _ExecRequest,
        tenant_id: str,
        domain: str | None,
        started: float,
    ) -> None:
        self._writer = writer
        self._req = req
        self._tenant_id = tenant_id
        self._domain = domain
        self._started = started

    async def __call__(
        self,
        status: str,
        *,
        reason: str | None = None,
        result: dict[str, object] | None = None,
        confirm_id: str | None = None,
        idem: str | None = None,
        expires_at: datetime | None = None,
        args_json: dict[str, object] | None = None,
    ) -> ToolOutcome:
        req = self._req
        elapsed = int((time.monotonic() - self._started) * 1000)
        row_id = await self._writer.record(
            ToolAuditEntry(
                trace_id=req.trace_id,
                user_id=req.user_id,
                tenant_id=self._tenant_id,
                tool_name=req.tool_name,
                status=status,
                args_digest=args_digest(req.args),
                args_json=args_json,
                error_reason=reason,
                duration_ms=elapsed,
                datasource_key=req.datasource_key,
                channel=req.channel,
                idempotency_key=idem,
                expires_at=expires_at,
                # 域标签在解析期随工具落定(静态域=映射表;连接器=http)
                domain=self._domain,
            )
        )
        if status == "PENDING_CONFIRM":
            if row_id is None:
                return ToolOutcome(status="ERROR", reason="审计写入失败", duration_ms=elapsed)
            confirm_id = row_id
        from kongming.control.events import report_product_event

        report_product_event(
            "tool.call",
            tenant_id=self._tenant_id,
            user_id=req.user_id,
            attributes={
                "tool_name": req.tool_name,
                "status": status,
                "domain": self._domain or "",
                "channel": req.channel,
            },
        )
        return ToolOutcome(
            status=status,  # type: ignore[arg-type]  # 调用方仅传合法枚举
            reason=reason,
            result=result,
            confirm_id=confirm_id,
            duration_ms=elapsed,
        )


@dataclass(frozen=True)
class ToolPorts:
    """工具调用端口组:构造参数收敛,避免签名随能力扩张持续膨胀。"""

    repo: ToolRepo
    writer: AuditWriter
    perms: PermReader
    limiter: RateLimiter
    sql: SqlExecutor
    rag: RagRetriever
    reply: ConfirmReplyStore
    intent: IntentReviewer | None = None  # 工具意图审核(未注入=关闭)
    sandbox: SandboxExecutor | None = None  # 沙箱执行(未注入=未启用)
    resolver: DynamicToolResolver | None = None  # 动态工具(MCP,未注入=纯静态)
    mcp: McpToolCaller | None = None  # MCP 出站执行
    websearch: WebSearchPort | None = None  # 联网检索(未注入=工具未注册)
    connector: ConnectorToolGate | None = None  # HTTP 连接器(未注入=不可用)


@dataclass(frozen=True)
class ToolLimits:
    """速率/确认/域配额限制组(组合根按 Settings 装配)。"""

    rate_per_minute: int
    confirm_ttl_minutes: int
    websearch_quota: int | None = None  # websearch 域配额(独立分桶)
    connector_quota: int | None = None  # http 连接器域配额(独立分桶)


class ToolCallService:
    def __init__(self, ports: ToolPorts, *, limits: ToolLimits) -> None:
        """端口与限额分组注入;域配额按域分桶(单一配置值当全域值会让新域
        挤占既有域配额,http 桶同口径)。"""
        self._ports = ports
        self._limits = limits
        self._domain_quotas: dict[str, int | None] = {
            "websearch": limits.websearch_quota,
            CONNECTOR_DOMAIN: limits.connector_quota,
        }

    # ── 六层防御编排 ──

    async def execute_tool(
        self,
        *,
        trace_id: str,
        tenant_key: str,
        datasource_key: str,
        user_id: str,
        tool_name: str,
        args: dict[str, object],
        idempotency_key: str | None,
        channel: str = "chat",
    ) -> ToolOutcome:
        """请求字段平铺入口:收敛为请求对象后交给执行链。"""
        return await self._execute(
            _ExecRequest(
                trace_id=trace_id,
                tenant_key=tenant_key,
                datasource_key=datasource_key,
                user_id=user_id,
                tool_name=tool_name,
                args=args,
                idempotency_key=idempotency_key,
                channel=channel,
            )
        )

    async def _execute(self, req: _ExecRequest) -> ToolOutcome:
        """六层防御编排(租户→解析→权限/意图/参数→限流→配额→分发)。"""
        started = time.monotonic()
        # L1:租户解析 + 绑定复校(异常上抛,router 映射 404/403,不落审计——
        # 与 internal/sql/execute 同口径:无租户主体时不落)
        tenant_id = await self._ports.repo.resolve_tenant(req.tenant_key)
        await self._ports.repo.assert_bound(user_id=req.user_id, tenant_id=tenant_id)
        tool, connector_hit = await self._resolve_tool(tenant_id=tenant_id, name=req.tool_name)
        # 域标签:静态域工具(含未注册)恒走映射(既有口径);连接器
        # 命中覆写为 http(审计域标签与配额桶共用)
        resolved_domain = _tool_domain(req.tool_name, connector_hit)
        finish = _ExecFinisher(
            self._ports.writer,
            req=req,
            tenant_id=tenant_id,
            domain=resolved_domain,
            started=started,
        )
        denied = (
            "TOOL_NOT_REGISTERED"
            if tool is None
            else await self._precheck(tool, req=req, tenant_id=tenant_id)
        )
        if denied is not None:
            return await finish("REJECTED", reason=denied)
        tool = cast("ToolDef", tool)  # 未注册已在 denied 分流,余下必为工具定义
        # L5a:限流(固定窗口;权限拒绝先于限流判定——无权请求不消耗配额)
        if not await self._ports.limiter.allow(
            f"{tenant_id}:{req.user_id}", limit=self._limits.rate_per_minute, window_sec=60
        ):
            return await finish("RATE_LIMITED", reason="RATE_LIMITED")
        idem = self._write_idempotency(tool, req=req)
        if tool.write and tool.require_confirm:
            expires = datetime.now(UTC) + timedelta(minutes=self._limits.confirm_ttl_minutes)
            return await finish(
                "PENDING_CONFIRM",
                reason="需用户确认后执行",
                idem=idem,
                expires_at=expires,
                args_json=req.args,  # 原文留存:确认后重放
            )
        over_quota = await self._quota_exceeded(
            tenant_id=tenant_id, user_id=req.user_id, domain=resolved_domain
        )
        if over_quota:
            return await finish("RATE_LIMITED", reason="RATE_LIMITED: 域配额超限")
        # handler 分发(执行期异常收敛 ERROR,防御层兜底)
        try:
            result = await self._dispatch(
                tool,
                req.args,
                trace_id=req.trace_id,
                tenant_key=req.tenant_key,
                tenant_id=tenant_id,
                user_id=req.user_id,
                datasource_key=req.datasource_key,
                idem=idem,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("工具执行异常 tool=%s trace=%s: %s", req.tool_name, req.trace_id, e)
            return await finish("ERROR", reason=str(e)[:200])
        return await finish("EXECUTED", result=result, idem=idem)

    async def _precheck(self, tool: ToolDef, *, req: _ExecRequest, tenant_id: str) -> str | None:
        """L2 权限点 → 写工具意图审核 → L4 参数校验;返回拒绝原因或 None。"""
        if tool.perm_code not in await self._ports.perms.list_codes(req.user_id):
            return f"TOOL_PERMISSION_DENIED: {tool.perm_code}"
        # 先审核写工具意图，再校验参数；超阈值时拒绝并写入两条审计记录。
        if tool.write and self._ports.intent is not None:
            write_tools = [d.name for d in effective().values() if d.write]
            attempts = await self._ports.repo.count_write_calls(req.trace_id, write_tools)
            if not await self._ports.intent.allow(
                trace_id=req.trace_id,
                user_id=req.user_id,
                tenant_id=tenant_id,
                tool_write=True,
                write_attempts=attempts,
            ):
                return "GUARDRAIL_BLOCKED: 工具意图审核未通过"
        # L4:参数 JSON Schema + 语义预检
        return validate_args(tool, req.args)

    def _write_idempotency(self, tool: ToolDef, *, req: _ExecRequest) -> str | None:
        """L6 幂等键:仅写工具需要(调用方传入优先,否则按请求派生)。"""
        if not tool.write:
            return None
        return req.idempotency_key or derive_idempotency_key(
            user_id=req.user_id, tool_name=req.tool_name, args=req.args
        )

    async def _quota_exceeded(self, *, tenant_id: str, user_id: str, domain: str | None) -> bool:
        """域配额第二维度(网关域工具的固定窗口叠加;内置工具不受影响;
        按域分桶,各域配额互不挤占)。桶键=域标签:按 tool_name 分桶会让
        多工具域的实际吞吐放大 N 倍。"""
        quota = self._domain_quotas.get(domain or "")
        if not quota:
            return False
        return not await self._ports.limiter.allow(
            f"toolgw:{tenant_id}:{user_id}:{domain}", limit=quota, window_sec=60
        )

    async def _resolve_tool(self, *, tenant_id: str, name: str) -> tuple[ToolDef | None, bool]:
        """静态注册表优先;连接器域按前缀解析(租户行);未命中查动态
        解析(MCP 快照,未注入=纯静态)。
        返回 (工具定义, 是否连接器工具)——域标签不在解析期落定:静态域工具
        (含未注册形态)恒走 DOMAIN_TOOLS 映射(既有口径),仅连接器命中
        覆写为 http。"""
        tool = get_tool(name)
        if tool is not None:
            return tool, False
        if name.startswith(CONNECTOR_TOOL_PREFIX) and self._ports.connector is not None:
            resolved = await self._ports.connector.resolve(tenant_id=tenant_id, name=name)
            if resolved is not None:
                return resolved, True
        if self._ports.resolver is None:
            return None, False
        return await self._ports.resolver.resolve(tenant_id=tenant_id, name=name), False

    # ── handler 分发 ──

    async def _dispatch(
        self,
        tool: ToolDef,
        args: dict[str, object],
        *,
        trace_id: str,
        tenant_key: str,
        tenant_id: str,
        user_id: str,
        datasource_key: str,
        idem: str | None,
    ) -> dict[str, object]:
        """按工具名分派 handler:内置表 + 前缀族(动态派发参数随身携带)。"""
        ctx = _HandlerCtx(
            trace_id=trace_id,
            tenant_key=tenant_key,
            tenant_id=tenant_id,
            user_id=user_id,
            datasource_key=datasource_key,
            idem=idem,
        )
        handler = _BUILTIN_HANDLERS.get(tool.name)
        if handler is not None:
            return await handler(self, args, ctx)
        for prefix, family in _FAMILY_HANDLERS:
            if tool.name.startswith(prefix):
                return await family(self, tool.name, args, ctx)
        raise LookupError(f"无 handler 的工具: {tool.name}")  # 注册表与分发漂移防御

    async def validate_sql(self, args: dict[str, object], ctx: _HandlerCtx) -> dict[str, object]:
        verdict = sql_check(str(args["sql"]))
        return {"passed": verdict.passed, "reason": verdict.reason}

    async def execute_sql(self, args: dict[str, object], ctx: _HandlerCtx) -> dict[str, object]:
        return await self._ports.sql.run(
            trace_id=ctx.trace_id,
            tenant_key=ctx.tenant_key,
            # 工具参数可显式指定数据源(覆盖请求级缺省)
            datasource_key=str(args.get("datasource_key") or ctx.datasource_key),
            sql_text=str(args["sql"]),
            user_id=ctx.user_id,
            question=None,
        )

    async def rag_retrieve(self, args: dict[str, object], ctx: _HandlerCtx) -> dict[str, object]:
        return await self._ports.rag.retrieve(
            tenant_key=ctx.tenant_key,
            datasource_key=ctx.datasource_key,
            question=str(args["question"]),
        )

    async def web_search(self, args: dict[str, object], ctx: _HandlerCtx) -> dict[str, object]:
        """联网检索(经网关域执行器)。"""
        # 注册表已过注册条件(供应商+key 齐备)才可能到达此处;
        # 端口缺失=装配漂移,收敛 WebSearchError(分发兜底 ERROR,不裸崩)
        if self._ports.websearch is None:
            raise WebSearchError(f"{FAILURE_UNDELIVERED_PREFIX} 联网检索未接线(部署缺陷)")
        # 缺省 5 与注册表 schema description 标注一致;L4 已校验 ∈[1,10],
        # 此处按非布尔 int 收窄(L4 之外的纵深一层)
        count = args.get("count")
        default = WEBSEARCH_COUNT_DEFAULT
        cards = await self._ports.websearch.search(
            query=str(args["query"]),
            count=count if isinstance(count, int) and not isinstance(count, bool) else default,
        )
        # 观察整形:检索卡已过策略器 allowlist(评分/追踪参数不入上下文),
        # 空结果如实表达(count=0),由 LLM 决定改写问句或据实回答
        return {"count": len(cards), "results": cards}

    async def run_code(self, args: dict[str, object], ctx: _HandlerCtx) -> dict[str, object]:
        return await self._run_python_code(
            args,
            trace_id=ctx.trace_id,
            tenant_key=ctx.tenant_key,
            tenant_id=ctx.tenant_id,
            user_id=ctx.user_id,
            datasource_key=ctx.datasource_key,
        )

    async def announce(self, args: dict[str, object], ctx: _HandlerCtx) -> dict[str, object]:
        ann_id, created = await self._ports.repo.insert_announcement(
            tenant_id=ctx.tenant_id,
            title=str(args["title"]),
            content=str(args["content"]),
            publisher_id=ctx.user_id,
            idempotency_key=ctx.idem or "",
        )
        return {"announcement_id": ann_id, "created": created}

    async def mcp_family(
        self, name: str, args: dict[str, object], ctx: _HandlerCtx
    ) -> dict[str, object]:
        """MCP 动态工具:出站执行(未接线=部署缺陷)。"""
        if self._ports.mcp is None:
            raise LookupError("MCP 出站执行未接线(部署缺陷)")
        return await self._ports.mcp.call(tenant_id=ctx.tenant_id, qualified=name, args=args)

    async def connector_family(
        self, name: str, args: dict[str, object], ctx: _HandlerCtx
    ) -> dict[str, object]:
        """租户连接器经网关切面执行(出口校验→SSRF→出站→分类);
        端口缺失=装配漂移,收敛 ConnectorError(分发兜底 ERROR,不裸崩)。"""
        if self._ports.connector is None:
            raise ConnectorError(f"{FAILURE_UNDELIVERED_PREFIX} 连接器未接线(部署缺陷)")
        return await self._ports.connector.call(tenant_id=ctx.tenant_id, name=name, args=dict(args))

    async def _run_python_code(
        self,
        args: dict[str, object],
        *,
        trace_id: str,
        tenant_key: str,
        tenant_id: str,
        user_id: str,
        datasource_key: str,
    ) -> dict[str, object]:
        """沙箱执行 handler。data_sql 六层防御取数注入(行级/防火墙
        与 execute_sql 工具同构);1MB 注入闸防内存放大;未启用收敛
        SandboxUnavailableError(由分发兜底映射 ERROR)。"""
        if self._ports.sandbox is None:
            raise SandboxUnavailableError("沙箱未启用(SANDBOX_DISABLED)")
        data: dict[str, object] | None = None
        data_sql = args.get("data_sql")
        if isinstance(data_sql, str) and data_sql.strip():
            sql_result = await self._ports.sql.run(
                trace_id=trace_id,
                tenant_key=tenant_key,
                datasource_key=datasource_key,
                sql_text=data_sql,
                user_id=user_id,
                question=None,
            )
            if sql_result.get("verdict") != "PASS":
                # 取数未过检:沙箱不启动,原样返回(verdict/reason 供 LLM 自纠)
                return {
                    "status": "ERROR",
                    "error": f"data_sql 未过检: {sql_result.get('reason')}",
                    "verdict": sql_result.get("verdict"),
                }
            payload: dict[str, object] = {
                "columns": sql_result.get("columns") or [],
                "rows": sql_result.get("rows") or [],
            }
            if (
                len(json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"))
                > _DATA_INJECT_CAP
            ):
                return {
                    "status": "ERROR",
                    "error": f"数据注入超 {_DATA_INJECT_CAP // 1024 // 1024}MB 上限,"
                    "请收窄 data_sql(WHERE/LIMIT)",
                }
            data = payload
        return await self._ports.sandbox.execute(
            code=str(args["code"]),
            data=data,
            trace_id=trace_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )

    # ── 写工具确认流 ──

    async def confirm(
        self, *, user_id: str, confirm_id: str, decision: str, session_id: str
    ) -> ConfirmOutcome:
        row = await self._ports.repo.get_for_update(confirm_id)
        # 存在性与归属同形 404(不暴露是哪种);会话归属含租户维度(chat 同构)
        if row is None:
            raise ConfirmNotFound(confirm_id)
        if row.user_id != user_id or not await self._ports.reply.session_owned(
            session_id=session_id, user_id=user_id, tenant_id=row.tenant_id
        ):
            await self._record_forbidden(
                user_id=user_id, row=row, confirm_id=confirm_id, prefix="confirm"
            )
            raise ConfirmNotFound(confirm_id)
        # 幂等:已推进过 → 返回首次决策结果(含首次执行结果反查)
        if row.status != "PENDING_CONFIRM":
            result = await self._first_execution_result(row)
            return ConfirmOutcome(
                status=_status_to_confirm_out(row.status), reason="已处理(幂等)", result=result
            )
        # 过期或用户拒绝:按拒绝语义收口并说明(spec)
        ended = await self._end_without_execution(
            row, user_id=user_id, decision=decision, session_id=session_id
        )
        if ended is not None:
            return ended
        # confirm 复校(spec「六层全量再过一遍」的人工确认适用子集):
        # L1 用户-租户绑定复校(挂起后解绑租户则拒)+ L2 权限点复校 +
        # L4 参数复校;L5 限流不适用(人工低频动作),L3 随 L1,L6 即本路径本身
        resolved, recheck = await self._recheck(row, user_id=user_id)
        if recheck is not None:
            return await self._reject_confirm(
                row, tool_name=row.tool_name, session_id=session_id, reason=recheck
            )
        resolved = cast("_Resolved", resolved)  # 复校无拒绝即有解析结果
        # 确认型工具的域配额在真实出站点判定(执行=确认重放,PENDING
        # 期不消费;提前判定会让 require_confirm 工具整段绕过域配额)。
        # 超限不落终态:确认保留,窗口重置后可再次确认(幂等收口仍按
        # PENDING 复核)。
        if await self._quota_exceeded(
            tenant_id=row.tenant_id, user_id=user_id, domain=resolved.domain
        ):
            await self._ports.reply.append_reply(
                session_id=session_id,
                content=f"操作「{row.tool_name}」暂时无法执行:域配额超限,请稍后再次确认。",
            )
            await self._ports.repo.commit()
            return ConfirmOutcome(status="error", reason="RATE_LIMITED: 域配额超限")
        try:
            result = await self._dispatch_confirmed(
                resolved.tool, row, user_id=user_id, prefix="confirm"
            )
        except Exception as e:  # noqa: BLE001
            log.warning("确认执行异常 confirm=%s: %s", confirm_id, e)
            await self._ports.repo.mark_status(row.id, "ERROR", clear_reason=True)
            await self._ports.reply.append_reply(
                session_id=session_id,
                content=f"操作「{row.tool_name}」执行失败:内部错误,请稍后重试。",
            )
            await self._ports.repo.commit()
            return ConfirmOutcome(status="error", reason=str(e)[:200])
        await self._mark_executed(row)
        await self._ports.reply.append_reply(
            session_id=session_id,
            content=f"已执行「{row.tool_name}」:{announce_summary(row.tool_name, row.args)}",
        )
        await self._ports.repo.commit()
        return ConfirmOutcome(status="executed", result=result)

    async def _end_without_execution(
        self, row: Any, *, user_id: str, decision: str, session_id: str
    ) -> ConfirmOutcome | None:
        """过期/用户拒绝:确认行落终态并回写会话说明;需继续执行的返回 None。"""
        if row.expires_at is not None and row.expires_at < datetime.now(UTC):
            await self._ports.repo.mark_status(row.id, "CONFIRM_EXPIRED", clear_reason=True)
            await self._ports.reply.append_reply(
                session_id=session_id, content=f"操作「{row.tool_name}」的确认已过期,未执行。"
            )
            await self._ports.repo.commit()
            return ConfirmOutcome(status="confirm_expired", reason="确认已过期")
        if decision != "reject":
            return None
        await self._ports.repo.mark_status(row.id, "CONFIRM_REJECTED", clear_reason=True)
        await self._ports.reply.append_reply(
            session_id=session_id, content=f"已取消操作「{row.tool_name}」,未执行任何变更。"
        )
        await self._ports.repo.commit()
        return ConfirmOutcome(status="confirm_rejected", reason="用户拒绝")

    async def _recheck(self, row: Any, *, user_id: str) -> tuple[_Resolved | None, str | None]:
        """确认重放复校(绑定 → 动态解析 → 权限点 → 参数);返回 (已解析工具, 拒绝原因)。
        确认重放同样走动态解析(MCP 写工具确认后不致误拒「权限已变更」);
        连接器写工具确认后同源解析(域标签与配额桶共用)。"""
        await self._ports.repo.assert_bound(user_id=user_id, tenant_id=row.tenant_id)
        tool, connector_hit = await self._resolve_tool(tenant_id=row.tenant_id, name=row.tool_name)
        if tool is None or tool.perm_code not in await self._ports.perms.list_codes(user_id):
            return None, "TOOL_PERMISSION_DENIED"
        return _Resolved(
            tool=tool, domain=_tool_domain(row.tool_name, connector_hit)
        ), validate_args(tool, row.args)

    async def _reject_confirm(
        self, row: Any, *, tool_name: str, session_id: str, reason: str
    ) -> ConfirmOutcome:
        """复校未过:确认行置 REJECTED 并回写会话说明。"""
        await self._ports.repo.mark_status(row.id, "REJECTED", clear_reason=True)
        detail = (
            "权限已变更,不再具备执行条件。"
            if reason == "TOOL_PERMISSION_DENIED"
            else "参数校验未过。"
        )
        await self._ports.reply.append_reply(
            session_id=session_id, content=f"操作「{tool_name}」未执行:{detail}"
        )
        await self._ports.repo.commit()
        return ConfirmOutcome(status="rejected", reason=reason)

    async def resolve_for_task(
        self, *, user_id: str, tenant_key: str, confirm_id: str, decision: str
    ) -> ToolOutcome:
        """任务通道确认兑现:图从挂起点恢复后,编排经
        内部回调把任务域确认决策兑换为「动作执行/拒绝」观察。

        与 confirm 镜像同一复校顺序(L1 解析+归属→L2 权限→L4 参数→域配额→
        幂等键 dispatch),**刻意不复用不复写 chat 路径**(安全敏感面,
        零回归优先;两侧复校序变更须同改);任务通道无会话,零回复副作用;
        事务归内部端点提交(与 task 内部回调同口径)。"""
        started = time.monotonic()
        # L1:租户解析(与 execute_tool 同口径;解析失败按不存在处理,不落审计)
        try:
            tenant_id = await self._ports.repo.resolve_tenant(tenant_key)
        except TenantNotFound:
            return ToolOutcome(status="ERROR", reason="TENANT_NOT_FOUND")
        row = await self._ports.repo.get_for_update(confirm_id)
        if row is None:
            return ToolOutcome(status="ERROR", reason="CONFIRM_NOT_FOUND")
        if row.user_id != user_id or row.tenant_id != tenant_id:
            await self._record_forbidden(
                user_id=user_id, row=row, confirm_id=confirm_id, prefix="task-confirm"
            )
            return ToolOutcome(status="REJECTED", reason="CONFIRM_FORBIDDEN")
        final = await self._task_terminal_outcome(row, user_id=user_id, decision=decision)
        if final is not None:
            return final
        return await self._run_task_confirmed(
            row, user_id=user_id, confirm_id=confirm_id, started=started
        )

    async def _run_task_confirmed(
        self, row: Any, *, user_id: str, confirm_id: str, started: float
    ) -> ToolOutcome:
        """任务通道确认后段:复校(绑定/权限/参数)→域配额→分发。"""
        # 确认复校(与 confirm 同序:绑定/权限/参数;限流不适用人工动作)
        resolved, recheck = await self._recheck(row, user_id=user_id)
        if recheck is not None:
            await self._ports.repo.mark_status(row.id, "REJECTED", clear_reason=True)
            reason = (
                "TOOL_PERMISSION_DENIED: 权限已变更"
                if recheck == "TOOL_PERMISSION_DENIED"
                else recheck
            )
            return ToolOutcome(status="REJECTED", reason=reason)
        resolved = cast("_Resolved", resolved)  # 复校无拒绝即有解析结果
        # 超限不落终态:确认保留,窗口重置后可再恢复(幂等收口按 PENDING 复核)
        if await self._quota_exceeded(
            tenant_id=row.tenant_id, user_id=user_id, domain=resolved.domain
        ):
            return ToolOutcome(status="ERROR", reason="RATE_LIMITED: 域配额超限,请稍后重试")
        try:
            result = await self._dispatch_confirmed(
                resolved.tool, row, user_id=user_id, prefix="task-confirm"
            )
        except Exception as e:  # noqa: BLE001
            log.warning("任务确认执行异常 confirm=%s: %s", confirm_id, e)
            await self._ports.repo.mark_status(row.id, "ERROR", clear_reason=True)
            return ToolOutcome(status="ERROR", reason=str(e)[:200])
        await self._mark_executed(row)
        return ToolOutcome(
            status="EXECUTED",
            result=result,
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    async def _task_terminal_outcome(
        self, row: Any, *, user_id: str, decision: str
    ) -> ToolOutcome | None:
        """任务通道的终态分支:已决/过期/拒绝直接返回观察,需执行时返回 None。
        已决行反查首次执行结果回填——恢复重放缺结果会诱发模型把同一动作
        当未办再次发起(新确认行=绕过幂等的二次执行)。"""
        if (
            row.status == "PENDING_CONFIRM"
            and row.expires_at is not None
            and row.expires_at < datetime.now(UTC)
        ):
            await self._ports.repo.mark_status(row.id, "CONFIRM_EXPIRED", clear_reason=True)
            return ToolOutcome(status="ERROR", reason="CONFIRM_EXPIRED: 确认已过期")
        if row.status == "PENDING_CONFIRM":
            if decision == "approve":
                return None
            await self._ports.repo.mark_status(row.id, "CONFIRM_REJECTED", clear_reason=True)
            return ToolOutcome(status="REJECTED", reason="用户拒绝该动作,已跳过")
        result = await self._first_execution_result(row)
        return ToolOutcome(
            status=cast("ToolStatus", _status_to_confirm_out(row.status).upper()),
            reason="已处理(幂等)",
            result=result,
        )

    # ── 确认流公共小件 ──

    async def _record_forbidden(
        self, *, user_id: str, row: Any, confirm_id: str, prefix: str
    ) -> None:
        """归属拒绝落审计(spec「他人确认被拒」立约)——为尝试者记 REJECTED 新行,
        不触碰原挂起行(防存在性泄漏)。"""
        await self._ports.writer.record(
            ToolAuditEntry(
                trace_id=f"{prefix}-{confirm_id[:24]}",
                user_id=user_id,
                tenant_id=row.tenant_id,
                tool_name=row.tool_name,
                status="REJECTED",
                error_reason="CONFIRM_FORBIDDEN",
            )
        )

    async def _first_execution_result(self, row: Any) -> dict[str, object] | None:
        """已执行确认的首次结果反查(按幂等键找回公告);其余状态无结果。"""
        if row.status != "EXECUTED" or not row.idempotency_key:
            return None
        ann_id = await self._ports.repo.find_announcement(
            tenant_id=row.tenant_id, idempotency_key=row.idempotency_key
        )
        return {"announcement_id": ann_id, "created": False} if ann_id is not None else None

    async def _dispatch_confirmed(
        self, tool: ToolDef, row: Any, *, user_id: str, prefix: str
    ) -> dict[str, object]:
        """确认重放执行(公告 handler 仅用 tenant_id;SQL 类工具不经确认路径)。"""
        return await self._dispatch(
            tool,
            row.args,
            trace_id=f"{prefix}-{row.id[:24]}",
            tenant_key="",
            tenant_id=row.tenant_id,
            user_id=user_id,
            datasource_key="default",
            idem=row.idempotency_key,
        )

    async def _mark_executed(self, row: Any) -> None:
        """执行后重算参数摘要——域 handler 可把执行结果回填入参,
        确认终态行摘要携带执行结果;未回填的既有工具重算值与原摘要一致
        (确定性纯函数),零漂移。"""
        await self._ports.repo.mark_status(
            row.id, "EXECUTED", clear_reason=True, args_digest=args_digest(row.args)
        )

    # ── 管理面 ──
    # 审计分页查询的行级可见性在 router 端点收口(非 ALL 强制本人,覆盖传值),
    # 与 audit 域「service 收口」形态不同:本域查询面无独立 service 用例,
    # 端点直调 repo(单行可见性判定无复用方,不过度抽象)。


# 内置工具分派表(工具名 → handler);前缀族按声明顺序匹配,内置表未命中的
# 动态形态(mcp__/http__)在此分派。表在类定义后填充,避免类体内前向引用。
_BUILTIN_HANDLERS: dict[str, _BuiltinHandler] = {
    "validate_sql": ToolCallService.validate_sql,
    "execute_sql": ToolCallService.execute_sql,
    "rag_retrieve": ToolCallService.rag_retrieve,
    "web_search": ToolCallService.web_search,
    "run_python_code": ToolCallService.run_code,
    "create_system_announcement": ToolCallService.announce,
    # 纯计算 handler 同步返回,由 _sync_handler 包成协程签名统一分派
    "compute_delta": _sync_handler(_compute_delta),
    "compute_share": _sync_handler(_compute_share),
}

_FAMILY_HANDLERS: tuple[tuple[str, _FamilyHandler], ...] = (
    ("mcp__", ToolCallService.mcp_family),
    (CONNECTOR_TOOL_PREFIX, ToolCallService.connector_family),
)


def _status_to_confirm_out(status: str) -> str:
    return {
        "EXECUTED": "executed",
        "CONFIRM_REJECTED": "confirm_rejected",
        "CONFIRM_EXPIRED": "confirm_expired",
    }.get(status, "rejected")


def announce_summary(tool_name: str, args: dict[str, object]) -> str:
    if tool_name == "create_system_announcement":
        return f"公告「{args.get('title', '')}」已发布。"
    return "执行完成。"
