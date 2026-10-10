"""toolcall 用例编排(019):六层纵深防御 + handler 分发 + 写工具确认流。

零框架纪律:不 import fastapi/sqlalchemy/redis;SQL 全在 repository,外部能力
(执行路由/RAG 召回/回执落消息)经 ports 端口注入,组合根在 router。

防线顺序(spec「工具纵深防御(六层)」):
L1 内部令牌(router middleware 既有)+ 租户解析/绑定复校(repository);
L2 工具权限点(注册表绑定,角色集不含即拒);
L3 租户隔离(L1 复校 fail-closed 已覆盖);
L4 参数 JSON Schema 校验 + 工具层语义预检(SQL 工具首关键字白名单);
L5 审计全量落(独立短事务)+ 固定窗口限流;
L6 写工具幂等键 + require_confirm 人工确认闸。
"""

import json
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jsonschema
import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from kongming.sqlfw.firewall import check as sql_check
from kongming.toolcall.models import (
    ConfirmNotFound,
    KnowledgeSourceError,
    ToolAuditEntry,
    ToolDef,
    ToolOutcome,
    WebSearchError,
    args_digest,
    derive_idempotency_key,
)
from kongming.toolcall.ports import (
    AuditWriter,
    ConfirmReplyStore,
    DynamicToolResolver,
    IntentReviewer,
    KnowledgeSearchPort,
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
    DOMAIN_TOOLS,
    FAILURE_UNDELIVERED_PREFIX,
    WEBSEARCH_COUNT_DEFAULT,
)

log = logging.getLogger(__name__)

# 027:data_sql 注入载荷上限(JSON UTF-8 **字节**数,防内存放大——
# 1000 行常规结果集约数十 KB,1MB 已宽;R1-A1:按字节量,与 runner
# stdin 2MB 字节闸单位一致——曾按字符数,CJK 1 字符 3 字节会过闸后
# 撞 runner 截断,报文误导为协议 bug)
_DATA_INJECT_CAP = 1 * 1024 * 1024


class SandboxUnavailableError(Exception):
    """027:沙箱未启用(组合根未注入 runner 时的 handler 收敛;
    经分发兜底映射 ERROR+审计,不裸 500)。"""


# SQL 类工具的 sql 参数语义预检(L4 的一部分):JSON Schema 管结构,sqlglot
# 根节点判定管「必须是只读语句形态」——DROP 在到达防火墙前即被参数校验层
# 拒绝(spec scenario 字面语义)。R1-S2:首关键字白名单改为 AST 根判定——
# 注释形态(-- /* */ 前缀的合法 SELECT)不再误拒;语法畸形放行给下游
# (防火墙/执行路由给出整洁错误,LLM 自纠)。
# 027:run_python_code 的 data_sql(可选参数)同规——映射表驱动,参数名按工具取。
_READONLY_ROOTS = (exp.Select, exp.Union, exp.Subquery)

_SQL_PARAM_BY_TOOL: dict[str, str] = {
    "validate_sql": "sql",
    "execute_sql": "sql",
    "run_python_code": "data_sql",
}


def _compute_delta(args: dict[str, object]) -> dict[str, object]:
    """057:增幅百分比 (target-base)/|base|*100;零副作用纯计算。

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
    """057:占比 part/total;与 _compute_delta 同哲学。"""
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
    except Exception as e:
        # R1-S2:敌意/畸形 schema(外部 MCP 快照面)会抛非 ValidationError
        # (SchemaError/_WrappedReferencingError 等,评审实证)——收敛
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
                # 语法畸形:放行下游(防火墙 ParseError 语义接管);RecursionError
                # 继承 RuntimeError 不在 SqlglotError 族——深嵌套括号曾逃逸
                # 此层直裸 500 且绕过审计(审查循环 S1),同样按畸形放行
                stmts = []
            if len(stmts) > 1:
                # 多语句=非单条 SELECT(schema 明文语义;下游防火墙本会拦,
                # 在参数校验层即拒——审查循环 B1 对齐 spec scenario 层位)
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


class ToolCallService:
    def __init__(  # noqa: PLR0913 —— 端口平铺注入
        self,
        repo: ToolRepo,
        writer: AuditWriter,
        perms: PermReader,
        limiter: RateLimiter,
        sql_executor: SqlExecutor,
        rag_retriever: RagRetriever,
        reply_store: ConfirmReplyStore,
        intent: IntentReviewer | None = None,  # 020:工具意图审核(未注入=关闭)
        sandbox: SandboxExecutor | None = None,  # 027:沙箱执行(未注入=未启用)
        resolver: DynamicToolResolver | None = None,  # 028:动态工具(MCP,未注入=纯静态)
        mcp_caller: McpToolCaller | None = None,  # 028:MCP 出站执行
        knowledge: KnowledgeSearchPort | None = None,  # 081:个人知识库(未注入=工具未注册)
        knowledge_quota: int | None = None,  # 089:域配额(第二维度;None=不限)
        websearch: WebSearchPort | None = None,  # 094:联网检索(未注入=工具未注册)
        websearch_quota: int | None = None,  # 094:websearch 域配额(与 knowledge 分桶)
        *,
        rate_limit_per_minute: int,
        confirm_ttl_minutes: int,
    ) -> None:
        self._repo = repo
        self._writer = writer
        self._perms = perms
        self._limiter = limiter
        self._sql = sql_executor
        self._rag = rag_retriever
        self._reply = reply_store
        self._intent = intent
        self._sandbox = sandbox
        self._resolver = resolver
        self._mcp = mcp_caller
        self._knowledge = knowledge
        self._websearch = websearch
        # 094:域配额按域分桶(单一配置值当全域值会让新域挤占既有域配额;
        # knowledge 桶值不变=既有行为零回归)
        self._domain_quotas: dict[str, int | None] = {
            "knowledge": knowledge_quota,
            "websearch": websearch_quota,
        }
        self._rate_limit = rate_limit_per_minute
        self._confirm_ttl = confirm_ttl_minutes

    # ── 六层防御编排 ──

    async def execute_tool(  # noqa: PLR0913 —— 请求字段平铺
        self,
        *,
        trace_id: str,
        tenant_key: str,
        datasource_key: str,
        user_id: str,
        tool_name: str,
        args: dict[str, object],
        idempotency_key: str | None,
        channel: str = "chat",  # 025
    ) -> ToolOutcome:
        started = time.monotonic()
        # L1:租户解析 + 绑定复校(异常上抛,router 映射 404/403,不落审计——
        # 与 internal/sql/execute 同口径:无租户主体时不落)
        tenant_id = await self._repo.resolve_tenant(tenant_key)
        await self._repo.assert_bound(user_id=user_id, tenant_id=tenant_id)

        async def _finish(
            status: str,
            *,
            reason: str | None = None,
            result: dict[str, object] | None = None,
            confirm_id: str | None = None,
            idem: str | None = None,
            expires_at: datetime | None = None,
            args_json: dict[str, object] | None = None,
        ) -> ToolOutcome:
            elapsed = int((time.monotonic() - started) * 1000)
            row_id = await self._writer.record(
                ToolAuditEntry(
                    trace_id=trace_id,
                    user_id=user_id,
                    tenant_id=tenant_id,
                    tool_name=tool_name,
                    status=status,
                    args_digest=args_digest(args),
                    args_json=args_json,
                    error_reason=reason,
                    duration_ms=elapsed,
                    datasource_key=datasource_key,
                    channel=channel,
                    idempotency_key=idem,
                    expires_at=expires_at,
                    domain=DOMAIN_TOOLS.get(tool_name),
                )
            )
            # PENDING_CONFIRM 的 confirm_id 只能来自审计行 id;审计写失败则
            # fail-closed 转 ERROR(挂起态不可用,不假装已挂起)
            if status == "PENDING_CONFIRM":
                if row_id is None:
                    return ToolOutcome(status="ERROR", reason="审计写入失败", duration_ms=elapsed)
                confirm_id = row_id
            return ToolOutcome(
                status=status,  # type: ignore[arg-type]  # 调用方仅传合法枚举
                reason=reason,
                result=result,
                confirm_id=confirm_id,
                duration_ms=elapsed,
            )

        tool = await self._resolve_tool(tenant_id=tenant_id, name=tool_name)
        if tool is None:
            return await _finish("REJECTED", reason="TOOL_NOT_REGISTERED")

        # L2:工具权限点
        if tool.perm_code not in await self._perms.list_codes(user_id):
            return await _finish("REJECTED", reason=f"TOOL_PERMISSION_DENIED: {tool.perm_code}")

        # L4 之前:工具意图审核(020,spec「意图审核先于参数校验」)——
        # 同 trace 写工具调用超阈值 fail-closed 拒绝,双审计落库
        if tool.write and self._intent is not None:
            write_tools = [d.name for d in effective().values() if d.write]
            attempts = await self._repo.count_write_calls(trace_id, write_tools)
            if not await self._intent.allow(
                trace_id=trace_id,
                user_id=user_id,
                tenant_id=tenant_id,
                tool_write=True,
                write_attempts=attempts,
            ):
                return await _finish("REJECTED", reason="GUARDRAIL_BLOCKED: 工具意图审核未通过")

        # L4:参数 JSON Schema + 语义预检
        arg_err = validate_args(tool, args)
        if arg_err is not None:
            return await _finish("REJECTED", reason=arg_err)

        # L5a:限流(固定窗口;权限拒绝先于限流判定——无权请求不消耗配额)
        if not await self._limiter.allow(
            f"{tenant_id}:{user_id}", limit=self._rate_limit, window_sec=60
        ):
            return await _finish("RATE_LIMITED", reason="RATE_LIMITED")

        # L6:写工具幂等键 + 人工确认闸
        idem: str | None = None
        if tool.write:
            idem = idempotency_key or derive_idempotency_key(
                user_id=user_id, tool_name=tool_name, args=args
            )
            if tool.require_confirm:
                expires = datetime.now(UTC) + timedelta(minutes=self._confirm_ttl)
                return await _finish(
                    "PENDING_CONFIRM",
                    reason="需用户确认后执行",
                    idem=idem,
                    expires_at=expires,
                    args_json=args,  # 原文留存:确认后重放
                )

        # 089:域配额第二维度(网关域工具的固定窗口叠加;内置工具不受影响;
        # 094 起按域分桶,各域配额互不挤占)
        quota = self._domain_quotas.get(DOMAIN_TOOLS.get(tool_name, ""))
        if quota and not await self._limiter.allow(
            f"toolgw:{tenant_id}:{user_id}:{tool_name}", limit=quota, window_sec=60
        ):
            return await _finish("RATE_LIMITED", reason="RATE_LIMITED: 域配额超限")

        # handler 分发(执行期异常收敛 ERROR,防御层兜底)
        try:
            result = await self._dispatch(
                tool,
                args,
                trace_id=trace_id,
                tenant_key=tenant_key,
                tenant_id=tenant_id,
                user_id=user_id,
                datasource_key=datasource_key,
                idem=idem,
            )
        except Exception as e:
            log.warning("工具执行异常 tool=%s trace=%s: %s", tool_name, trace_id, e)
            return await _finish("ERROR", reason=str(e)[:200])
        return await _finish("EXECUTED", result=result, idem=idem)

    async def _resolve_tool(self, *, tenant_id: str, name: str) -> ToolDef | None:
        """静态注册表优先;未命中查动态解析(028 MCP 快照,未注入=纯静态)。"""
        tool = get_tool(name)
        if tool is not None:
            return tool
        if self._resolver is None:
            return None
        return await self._resolver.resolve(tenant_id=tenant_id, name=name)

    # ── handler 分发 ──

    async def _dispatch(  # noqa: PLR0913 —— 上下文平铺
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
        if tool.name == "validate_sql":
            verdict = sql_check(str(args["sql"]))
            return {"passed": verdict.passed, "reason": verdict.reason}
        if tool.name == "execute_sql":
            return await self._sql.run(
                trace_id=trace_id,
                tenant_key=tenant_key,
                # 工具参数可显式指定数据源(覆盖请求级缺省)
                datasource_key=str(args.get("datasource_key") or datasource_key),
                sql_text=str(args["sql"]),
                user_id=user_id,
                question=None,
            )
        if tool.name == "rag_retrieve":
            return await self._rag.retrieve(
                tenant_key=tenant_key,
                datasource_key=datasource_key,
                question=str(args["question"]),
            )
        if tool.name.startswith("mcp__"):
            if self._mcp is None:
                raise LookupError("MCP 出站执行未接线(部署缺陷)")
            return await self._mcp.call(tenant_id=tenant_id, qualified=tool.name, args=args)
        if tool.name == "knowledge_search":
            # 081:注册表已过两道注册条件才可能到达此处;端口缺失=装配漂移,
            # 收敛 KnowledgeSourceError(分发兜底 ERROR,不裸崩)
            if self._knowledge is None:
                raise KnowledgeSourceError("unreachable: 知识库检索未接线(部署缺陷)")
            # 缺省 5 与注册表 schema description 标注一致;L4 已校验 ∈[1,10],
            # 此处按非布尔 int 收窄(L4 之外的纵深一层)
            top_k = args.get("top_k")
            hits = await self._knowledge.search(
                query=str(args["query"]),
                top_k=top_k if isinstance(top_k, int) and not isinstance(top_k, bool) else 5,
            )
            # 观察整形:只透出文本与来源锚点(分数/内部 id 不进 LLM 上下文),
            # 空结果如实表达(count=0),由 LLM 决定改走他路或据实回答
            return {
                "count": len(hits),
                "hits": [
                    {
                        "source_name": hit.get("source_name"),
                        "note_path": hit.get("note_path"),
                        "anchor": hit.get("anchor"),
                        "text": hit.get("text"),
                    }
                    for hit in hits
                ],
            }
        if tool.name == "web_search":
            # 094:注册表已过注册条件(供应商+key 齐备)才可能到达此处;
            # 端口缺失=装配漂移,收敛 WebSearchError(分发兜底 ERROR,不裸崩)
            if self._websearch is None:
                raise WebSearchError(f"{FAILURE_UNDELIVERED_PREFIX} 联网检索未接线(部署缺陷)")
            # 缺省 5 与注册表 schema description 标注一致;L4 已校验 ∈[1,10],
            # 此处按非布尔 int 收窄(L4 之外的纵深一层)
            count = args.get("count")
            default = WEBSEARCH_COUNT_DEFAULT
            cards = await self._websearch.search(
                query=str(args["query"]),
                count=count if isinstance(count, int) and not isinstance(count, bool) else default,
            )
            # 观察整形:检索卡已过策略器 allowlist(评分/追踪参数不入上下文),
            # 空结果如实表达(count=0),由 LLM 决定改写问句或据实回答
            return {"count": len(cards), "results": cards}
        if tool.name == "run_python_code":
            return await self._run_python_code(
                args,
                trace_id=trace_id,
                tenant_key=tenant_key,
                tenant_id=tenant_id,
                user_id=user_id,
                datasource_key=datasource_key,
            )
        if tool.name == "create_system_announcement":
            ann_id, created = await self._repo.insert_announcement(
                tenant_id=tenant_id,
                title=str(args["title"]),
                content=str(args["content"]),
                publisher_id=user_id,
                idempotency_key=idem or "",
            )
            return {"announcement_id": ann_id, "created": created}
        if tool.name == "compute_delta":
            return _compute_delta(args)
        if tool.name == "compute_share":
            return _compute_share(args)
        raise LookupError(f"无 handler 的工具: {tool.name}")  # 注册表与分发漂移防御

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
        """027:沙箱执行 handler。data_sql 六层防御取数注入(行级/防火墙
        与 execute_sql 工具同构);1MB 注入闸防内存放大;未启用收敛
        SandboxUnavailableError(由分发兜底映射 ERROR)。"""
        if self._sandbox is None:
            raise SandboxUnavailableError("沙箱未启用(SANDBOX_DISABLED)")
        data: dict[str, object] | None = None
        data_sql = args.get("data_sql")
        if isinstance(data_sql, str) and data_sql.strip():
            sql_result = await self._sql.run(
                trace_id=trace_id,
                tenant_key=tenant_key,
                datasource_key=datasource_key,
                sql_text=data_sql,
                user_id=user_id,
                question=None,
            )
            if sql_result.get("verdict") != "PASS":
                # 取数未过检:沙箱不启动,原样返回( verdict/reason 供 LLM 自纠)
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
        return await self._sandbox.execute(
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
        row = await self._repo.get_for_update(confirm_id)
        # 存在性与归属同形 404(不暴露是哪种);会话归属含租户维度(chat 同构)
        if row is None:
            raise ConfirmNotFound(confirm_id)
        if row.user_id != user_id or not await self._reply.session_owned(
            session_id=session_id, user_id=user_id, tenant_id=row.tenant_id
        ):
            # R1-M2:归属拒绝落审计(spec「他人确认被拒」立约)——为尝试者记
            # REJECTED 新行,不触碰原挂起行(防存在性泄漏)
            await self._writer.record(
                ToolAuditEntry(
                    trace_id=f"confirm-{confirm_id[:24]}",
                    user_id=user_id,
                    tenant_id=row.tenant_id,
                    tool_name=row.tool_name,
                    status="REJECTED",
                    error_reason="CONFIRM_FORBIDDEN",
                )
            )
            raise ConfirmNotFound(confirm_id)
        # 幂等:已推进过 → 返回首次决策结果(R1-S1:含首次执行结果反查)
        if row.status != "PENDING_CONFIRM":
            result: dict[str, object] | None = None
            if row.status == "EXECUTED" and row.idempotency_key:
                ann_id = await self._repo.find_announcement(
                    tenant_id=row.tenant_id, idempotency_key=row.idempotency_key
                )
                if ann_id is not None:
                    result = {"announcement_id": ann_id, "created": False}
            return ConfirmOutcome(
                status=_status_to_confirm_out(row.status), reason="已处理(幂等)", result=result
            )
        # 过期:按拒绝语义处理并说明(spec)
        if row.expires_at is not None and row.expires_at < datetime.now(UTC):
            await self._repo.mark_status(row.id, "CONFIRM_EXPIRED", clear_reason=True)
            await self._reply.append_reply(
                session_id=session_id, content=f"操作「{row.tool_name}」的确认已过期,未执行。"
            )
            await self._repo.commit()
            return ConfirmOutcome(status="confirm_expired", reason="确认已过期")
        if decision == "reject":
            await self._repo.mark_status(row.id, "CONFIRM_REJECTED", clear_reason=True)
            await self._reply.append_reply(
                session_id=session_id, content=f"已取消操作「{row.tool_name}」,未执行任何变更。"
            )
            await self._repo.commit()
            return ConfirmOutcome(status="confirm_rejected", reason="用户拒绝")

        # confirm 复校(spec「六层全量再过一遍」的人工确认适用子集):
        # L1 用户-租户绑定复校(R1-M3:挂起后解绑租户则拒)+ L2 权限点复校 +
        # L4 参数复校;L5 限流不适用(人工低频动作),L3 随 L1,L6 即本路径本身
        await self._repo.assert_bound(user_id=user_id, tenant_id=row.tenant_id)
        # 028:确认重放同样走动态解析(MCP 写工具确认后不致误拒「权限已变更」)
        tool = await self._resolve_tool(tenant_id=row.tenant_id, name=row.tool_name)
        perms = await self._perms.list_codes(user_id)
        if tool is None or tool.perm_code not in perms:
            await self._repo.mark_status(row.id, "REJECTED", clear_reason=True)
            await self._reply.append_reply(
                session_id=session_id,
                content=f"操作「{row.tool_name}」未执行:权限已变更,不再具备执行条件。",
            )
            await self._repo.commit()
            return ConfirmOutcome(status="rejected", reason="TOOL_PERMISSION_DENIED")
        arg_err = validate_args(tool, row.args)
        if arg_err is not None:
            await self._repo.mark_status(row.id, "REJECTED", clear_reason=True)
            await self._reply.append_reply(
                session_id=session_id, content=f"操作「{row.tool_name}」未执行:参数校验未过。"
            )
            await self._repo.commit()
            return ConfirmOutcome(status="rejected", reason=arg_err)

        try:
            result = await self._dispatch(
                tool,
                row.args,
                trace_id=f"confirm-{row.id[:24]}",
                tenant_key="",  # 公告 handler 仅用 tenant_id;SQL 类工具不经确认路径
                tenant_id=row.tenant_id,
                user_id=user_id,
                datasource_key="default",
                idem=row.idempotency_key,
            )
        except Exception as e:
            log.warning("确认执行异常 confirm=%s: %s", confirm_id, e)
            await self._repo.mark_status(row.id, "ERROR", clear_reason=True)
            await self._reply.append_reply(
                session_id=session_id,
                content=f"操作「{row.tool_name}」执行失败:内部错误,请稍后重试。",
            )
            await self._repo.commit()
            return ConfirmOutcome(status="error", reason=str(e)[:200])
        await self._repo.mark_status(row.id, "EXECUTED", clear_reason=True)
        await self._reply.append_reply(
            session_id=session_id,
            content=f"已执行「{row.tool_name}」:{_announce_summary(row.tool_name, row.args)}",
        )
        await self._repo.commit()
        return ConfirmOutcome(status="executed", result=result)

    # ── 管理面 ──
    # 审计分页查询的行级可见性在 router 端点收口(非 ALL 强制本人,覆盖传值),
    # 与 015 audit 域「service 收口」形态不同:本域查询面无独立 service 用例,
    # 端点直调 repo(单行可见性判定无复用方,不过度抽象)。


def _status_to_confirm_out(status: str) -> str:
    return {
        "EXECUTED": "executed",
        "CONFIRM_REJECTED": "confirm_rejected",
        "CONFIRM_EXPIRED": "confirm_expired",
    }.get(status, "rejected")


def _announce_summary(tool_name: str, args: dict[str, object]) -> str:
    if tool_name == "create_system_announcement":
        return f"公告「{args.get('title', '')}」已发布。"
    return "执行完成。"
