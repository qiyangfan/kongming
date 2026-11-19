"""toolcall 组合根适配器:SQL/RAG/会话回执/意图审核/网关域工具/MCP 绑定。"""

import logging
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession

from kongming.chat.repository import ChatRepository
from kongming.config import get_settings
from kongming.connector.models import (
    build_injection_spec,
    key_from_tool_name,
    params_to_schema,
    row_param_decls,
)
from kongming.connector.repository import SqlConnectorRepo
from kongming.datasource.cipher import get_cipher
from kongming.internal import service as internal_service
from kongming.mcp.service import McpService
from kongming.rag.dependencies import get_rag_service
from kongming.toolcall.models import ConnectorError, ToolDef
from kongming.toolgateway.gateway import ToolGateway
from kongming.toolgateway.models import CONNECTOR_DOMAIN, FAILURE_UNDELIVERED_PREFIX
from kongming.toolgateway.websearch import WebSearchError

log = logging.getLogger(__name__)


class InternalSqlExecutor:
    """将 SQL 工具绑定到受控执行服务，复用防火墙、数据范围和只读校验。"""

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


class RagServiceAdapter:
    """将 rag_retrieve 工具绑定到 RAG 三路召回服务。"""

    def __init__(self, session: AsyncSession) -> None:
        self._svc = get_rag_service(session)
        self._s = session

    async def _enrich(
        self, tenant_key: str, datasource_key: str, question: str, schema_hint: str
    ) -> dict[str, object]:
        """语义段组装(与 rag retrieve 端点同构;降级空串)。"""
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
            # ReAct 工具执行也传入语义提示，内容与 /internal/rag/retrieve 共用同一组装逻辑。
            out.update(await self._enrich(tenant_key, datasource_key, question, ctx.schema_hint))
            return out
        from kongming.internal.meta import MetaSchemaIn, meta_schema

        try:
            meta = await meta_schema(
                MetaSchemaIn(tenant_key=tenant_key, datasource_key=datasource_key), self._s
            )
        except Exception:
            # 实时内省失败时返回空提示。
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


class ChatReplyStore:
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


class GuardrailIntentReviewer:
    """意图审核端口绑定:guardrail 域服务(独立短会话读配置)。"""

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


class GatewayWebSearchClient:
    """WebSearchPort 绑定:出站经网关切面(出口校验→限流→出站→脱敏→
    重试分类),本适配层只做域方言整形(检索卡观察形态);失败收敛
    WebSearchError(分发兜底 ERROR+审计)。"""

    def __init__(self, gateway: ToolGateway) -> None:
        self._gw = gateway

    async def search(self, *, query: str, count: int) -> list[dict[str, object]]:
        out = await self._gw.execute(
            tool_name="web_search",
            args={"query": query, "count": count},
            domain="websearch",
            trace_id="-",  # trace 经工具分发层审计承载;适配层不重复落
            tenant_id="-",
            user_id="-",
        )
        if out["status"] != "executed":
            raise WebSearchError(
                str(out.get("reason") or f"{FAILURE_UNDELIVERED_PREFIX} 联网检索拒连")
            )
        result = cast("dict[str, object]", out.get("result") or {})
        return cast("list[dict[str, object]]", result.get("results") or [])


class McpDynamicResolver:
    """DynamicToolResolver 绑定:快照命中 → fail-closed ToolDef。"""

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


class ConnectorToolGate:
    """ConnectorToolGate 绑定:启用连接器行 → fail-closed ToolDef;
    出站执行经网关切面单域装配(出口校验→SSRF→出站→分类)。凭证仅在
    get_call_target(唯一明文出口)瞬时装配,不落日志/审计/任何响应。"""

    def __init__(self, session: AsyncSession) -> None:
        settings = get_settings()
        self._repo = SqlConnectorRepo(session, get_cipher(settings.datasource_encrypt_key))
        self._quota = settings.toolgateway_http_quota

    def _tool_def(self, row: dict[str, Any], name: str) -> ToolDef:
        spec = build_injection_spec(row)
        confirm = bool(row.get("requires_confirmation"))
        return ToolDef(
            name=name,
            description=str(spec["description"]),
            parameters=params_to_schema(row_param_decls(row)),
            perm_code="tool:http:call",
            write=confirm,
            require_confirm=confirm,
        )

    async def resolve(self, *, tenant_id: str, name: str) -> ToolDef | None:
        key = key_from_tool_name(name)
        if key is None:
            return None
        row = await self._repo.find_enabled_by_key(tenant_id=tenant_id, key=key)
        return None if row is None else self._tool_def(row, name)

    async def call(
        self, *, tenant_id: str, name: str, args: dict[str, object]
    ) -> dict[str, object]:
        from kongming.toolgateway.connector import build_connector_stack

        key = key_from_tool_name(name)
        if key is None:
            raise ConnectorError(f"{FAILURE_UNDELIVERED_PREFIX} 连接器工具名形态非法")
        # 禁用/删除竞态:唯一明文出口返回 None → 「未注册」语义
        row = await self._repo.get_call_target(tenant_id=tenant_id, key=key)
        if row is None:
            raise ConnectorError(f"{FAILURE_UNDELIVERED_PREFIX} 连接器不可用(已停用或删除)")
        spec, policy, executor = build_connector_stack(
            row,
            auth_secret=row.get("auth_secret_plain"),
            quota_per_minute=self._quota,
        )
        gw = ToolGateway(
            manifest={CONNECTOR_DOMAIN: spec},
            policies={CONNECTOR_DOMAIN: policy},
            domain_executors={CONNECTOR_DOMAIN: executor},
        )
        out = await gw.execute(
            tool_name=name,
            args=args,
            domain=CONNECTOR_DOMAIN,
            trace_id="-",  # trace 经工具分发层审计承载;适配层不重复落
            tenant_id=tenant_id,
            user_id="-",
        )
        if out["status"] != "executed":
            raise ConnectorError(str(out.get("reason") or "unreachable: 连接器调用失败"))
        return cast("dict[str, object]", out.get("result") or {})


class McpToolCaller:
    """McpToolCaller 绑定(出站执行经 mcp 域服务)。"""

    def __init__(self, svc: McpService) -> None:
        self._svc = svc

    async def call(
        self, *, tenant_id: str, qualified: str, args: dict[str, object]
    ) -> dict[str, object]:
        return await self._svc.call_tool(tenant_id=tenant_id, qualified=qualified, args=args)
