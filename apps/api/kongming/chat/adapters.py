"""chat 组合根适配器:其他域服务 → chat 端口绑定。

chat service/models 受 import-linter 零框架契约,不可间接 import 携
sqlalchemy 的域服务;跨域装配与内部异常→chat 域异常的翻译收口在本模块。
"""

import logging
import uuid
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from kongming.attachment.repository import SqlAttachmentRepo
from kongming.auth.repository import SqlOperLogRepo, SqlPermRepo
from kongming.chat.domain_errors import InvalidDatasource, TenantForbidden
from kongming.chat.ports import ExplainOutcome, SqlRunOutcome
from kongming.config import get_settings
from kongming.db import get_sessionmaker
from kongming.internal import service as internal_service
from kongming.internal.meta import MetaTableOut
from kongming.toolcall.repository import SqlToolCallRepo

if TYPE_CHECKING:
    from kongming.connector.service import ConnectorService
    from kongming.mcp.service import McpService

log = logging.getLogger(__name__)


class InternalExplainRunner:
    """组合根适配器:chat 域 ExplainRunner 端口 ← internal.service.explain。

    chat service/models 受 import-linter 零框架契约,不可间接 import internal
    (携 sqlalchemy);内部异常在此翻译为 chat 域异常,不越域泄漏。
    """

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def explain(
        self,
        *,
        trace_id: str,
        tenant_key: str,
        datasource_key: str,
        sql_text: str,
        user_id: str,
    ) -> ExplainOutcome:
        try:
            r = await internal_service.explain(
                self._s,
                trace_id=trace_id,
                tenant_key=tenant_key,
                datasource_key=datasource_key,
                sql_text=sql_text,
                user_id=user_id,
                question=None,
            )
        except internal_service.InvalidDatasource as e:
            # 竞态兜底(chat 已预检):禁用/删除发生在预检与执行之间
            raise InvalidDatasource(str(e)) from e
        except (internal_service.TenantNotFound, internal_service.TenantBindingForbidden) as e:
            raise TenantForbidden(str(e)) from e
        return ExplainOutcome(verdict=r.verdict, reason=r.reason, plan=r.plan)


class InternalExecuteRunner:
    """将 Chat 的执行端口绑定到受控 SQL 执行服务，复用其权限与只读检查。"""

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
    ) -> SqlRunOutcome:
        # 行数上限配置化(与导出 export_max_rows 同语义不同阀;缺省 1000 行为不变)
        try:
            return await internal_service.execute(
                self._s,
                trace_id=trace_id,
                tenant_key=tenant_key,
                datasource_key=datasource_key,
                sql_text=sql_text,
                user_id=user_id,
                question=None,
                limit=get_settings().chat_result_max_rows,
            )
        except internal_service.InvalidDatasource as e:
            # 竞态兜底(chat 已预检):禁用/删除发生在预检与执行之间(与 explain 适配器同范式)
            raise InvalidDatasource(str(e)) from e
        except (internal_service.TenantNotFound, internal_service.TenantBindingForbidden) as e:
            raise TenantForbidden(str(e)) from e


class ChatUsageCounter:
    """租户窗口调用计数适配(独立短会话——闸判定不占用请求作用域会话)。

    口径:sys_llm_usage 行数按 created_at >= 窗口起点 count(含失败尝试行;
    「失败调用也采集」既有语义,重试后仍成立)。
    """

    async def count_calls(
        self, *, tenant_id: str, day_start: object, month_start: object
    ) -> tuple[int, int]:
        from sqlalchemy import text as _text

        async with get_sessionmaker()() as s:
            row = (
                await s.execute(
                    _text(
                        "SELECT"
                        " COUNT(*) FILTER (WHERE created_at >= :d) AS day_calls,"
                        " COUNT(*) FILTER (WHERE created_at >= :m) AS month_calls"
                        " FROM public.sys_llm_usage WHERE tenant_id = CAST(:t AS uuid)"
                    ),
                    {"t": tenant_id, "d": day_start, "m": month_start},
                )
            ).one()
        return int(row[0]), int(row[1])


class _TeamRolesAdapter:
    """TeamRoles 绑定(team 域 role_map;独立短会话)。"""

    async def role_map(self, user_id: str) -> dict[str, str]:
        from kongming.team.repository import SqlTeamRepo

        async with get_sessionmaker()() as s:
            return await SqlTeamRepo(s).role_map(user_id=user_id)


class MemoryAdapter:
    """记忆端口绑定:独立短会话(同步/流式/后台任务通用);
    form_explicit 的敏感拒绝转 False(适配端口 bool 语义)。"""

    async def recall_hint(self, *, user_id: str, tenant_id: str, question: str) -> str:
        from kongming.memory.repository import SqlMemoryRepo
        from kongming.memory.service import MemoryService
        from kongming.rag.agent_callback import get_agent_rag_client
        from kongming.redis_client import get_redis

        async with get_sessionmaker()() as s:
            svc = MemoryService(
                SqlMemoryRepo(s),
                get_agent_rag_client(get_redis()),
                SqlOperLogRepo(s),
                _TeamRolesAdapter(),
            )
            return await svc.recall_hint(user_id=user_id, tenant_id=tenant_id, question=question)

    async def preference_hint(self, *, user_id: str, tenant_id: str) -> str:
        from kongming.memory.repository import SqlMemoryRepo
        from kongming.memory.service import MemoryService
        from kongming.rag.agent_callback import get_agent_rag_client
        from kongming.redis_client import get_redis

        async with get_sessionmaker()() as s:
            svc = MemoryService(
                SqlMemoryRepo(s),
                get_agent_rag_client(get_redis()),
                SqlOperLogRepo(s),
                _TeamRolesAdapter(),
            )
            return await svc.preference_hint(user_id=user_id, tenant_id=tenant_id)

    async def explicit_intent(self, question: str) -> str | None:
        from kongming.memory.models import extract_explicit_intent

        return extract_explicit_intent(question)

    async def form_explicit(self, *, user_id: str, tenant_id: str, content: str) -> bool:
        from kongming.memory.repository import SqlMemoryRepo
        from kongming.memory.service import MemoryService, SensitiveContentRejected
        from kongming.rag.agent_callback import get_agent_rag_client
        from kongming.redis_client import get_redis

        async with get_sessionmaker()() as s:
            svc = MemoryService(
                SqlMemoryRepo(s),
                get_agent_rag_client(get_redis()),
                SqlOperLogRepo(s),
                _TeamRolesAdapter(),
            )
            try:
                await svc.form_explicit(user_id=user_id, tenant_id=tenant_id, content=content)
            except SensitiveContentRejected:
                return False
        return True

    async def form_implicit(
        self,
        *,
        user_id: str,
        tenant_id: str,
        candidates: list[dict[str, object]],
        pref_revision: int | None = None,
    ) -> int:
        from kongming.memory.repository import SqlMemoryRepo
        from kongming.memory.service import MemoryService
        from kongming.rag.agent_callback import get_agent_rag_client
        from kongming.redis_client import get_redis

        async with get_sessionmaker()() as s:
            svc = MemoryService(
                SqlMemoryRepo(s),
                get_agent_rag_client(get_redis()),
                SqlOperLogRepo(s),
                _TeamRolesAdapter(),
            )
            return await svc.form_implicit(
                user_id=user_id,
                tenant_id=tenant_id,
                candidates=candidates,
                base_revision=pref_revision,
            )

    async def current_revision(self, *, user_id: str, tenant_id: str) -> int | None:
        from kongming.memory.repository import SqlMemoryRepo
        from kongming.memory.service import MemoryService
        from kongming.rag.agent_callback import get_agent_rag_client
        from kongming.redis_client import get_redis

        async with get_sessionmaker()() as s:
            svc = MemoryService(
                SqlMemoryRepo(s),
                get_agent_rag_client(get_redis()),
                SqlOperLogRepo(s),
                _TeamRolesAdapter(),
            )
            return await svc.current_revision(user_id=user_id, tenant_id=tenant_id)


class AttachmentChecker:
    """AttachmentChecker 绑定(attachment 域仓储,请求会话)。"""

    def __init__(self, session: AsyncSession) -> None:
        self._repo = SqlAttachmentRepo(session)

    async def validate_owned(
        self, *, ids: list[str], user_id: str, tenant_id: str
    ) -> list[dict[str, object]] | None:
        out: list[dict[str, object]] = []
        # 去重保序——同 id 重复引用过检后第二次取件必 404
        # (一次性消费),整轮 fail-closed 且文案误导
        for att_id in dict.fromkeys(ids):
            row = await self._repo.find_owned(att_id=att_id, tenant_id=tenant_id, user_id=user_id)
            if row is None or row["status"] != "UPLOADED":
                return None
            out.append(
                {
                    "id": str(row["id"]),
                    "filename": str(row["filename"]),
                    "mime": str(row["mime"]),
                    "size_bytes": int(row["size_bytes"]),
                }
            )
        return out


class RegistryToolSpecProvider:
    """ReAct 注入工具的唯一真理源:权限点集查询 + toolcall 注册表过滤。
    合并租户 MCP 动态工具(持 tool:mcp:use 才可见——与执行面 L2 同判据)。
    合并租户 HTTP 连接器(持 tool:http:call 才可见;按请求动态装配,
    注册/启停即时生效;名称/描述过既有输入护栏,BLOCK 拒注入)。"""

    async def authorized_tools(self, user_id: str, tenant_id: str) -> list[dict[str, object]]:
        from kongming.toolcall.registry import filter_specs

        async with get_sessionmaker()() as s:
            codes = await SqlPermRepo(s).list_codes(user_id)
            specs: list[dict[str, object]] = [dict(spec) for spec in filter_specs(codes)]
            if "tool:mcp:use" in codes:
                mcp_svc = _build_mcp_service_for(s)
                for entry in await mcp_svc.list_dynamic_tools(tenant_id=tenant_id):
                    read_only = bool(entry.get("read_only"))
                    specs.append(
                        {
                            "name": str(entry.get("qualified") or ""),
                            "description": str(entry.get("description") or ""),
                            "parameters": (
                                entry.get("schema")
                                if isinstance(entry.get("schema"), dict)
                                else {"type": "object"}
                            ),
                            "write": not read_only,
                            "require_confirm": not read_only,
                        }
                    )
            if "tool:http:call" in codes:
                specs.extend(
                    await _build_connector_service_for(s).injection_specs(
                        tenant_id=tenant_id,
                        user_id=user_id,
                        trace_id=f"conn-{uuid.uuid4().hex[:12]}",
                    )
                )
        return specs


def _build_connector_service_for(session: AsyncSession) -> "ConnectorService":
    """装配(chat 适配层;与 connector 组合根同构)。"""
    from kongming.connector.repository import SqlConnectorRepo
    from kongming.connector.router import ConnectorGuardrailAdapter, ConnectorTestCallAuditor
    from kongming.connector.service import ConnectorService
    from kongming.datasource.cipher import get_cipher as _cipher

    settings = get_settings()
    return ConnectorService(
        SqlConnectorRepo(session, _cipher(settings.datasource_encrypt_key)),
        _cipher(settings.datasource_encrypt_key),
        ConnectorGuardrailAdapter(),
        timeout_max_ms=settings.connector_timeout_max_ms,
        auditor=ConnectorTestCallAuditor(),
    )


def _build_mcp_service_for(session: AsyncSession) -> "McpService":
    """装配(chat 适配层;与 toolcall 组合根同构)。"""
    from kongming.datasource.cipher import get_cipher
    from kongming.mcp.breaker import build_mcp_caller, get_mcp_breaker
    from kongming.mcp.repository import SqlMcpRepo
    from kongming.mcp.service import McpService

    settings = get_settings()
    return McpService(
        SqlMcpRepo(session, get_cipher(settings.datasource_encrypt_key)),
        build_mcp_caller(settings),
        breaker=get_mcp_breaker(),
    )


class PendingToolReader:
    """待确认写工具聚合:独立短会话只读(PENDING_CONFIRM 行经独立事务提交)。"""

    async def list_pending(self, trace_id: str) -> list[dict[str, object]]:
        async with get_sessionmaker()() as s:
            return await SqlToolCallRepo(s).list_pending_by_trace(trace_id)


class ChatGuardrail:
    """prompt 护栏端口绑定:独立短会话读配置;审计经 writer 独立事务。"""

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


_SUGGEST_TABLE_CAP = 30  # 摘要表数封顶(meta 自身另有 max_tables,双闸取严)
_SUGGEST_DIGEST_CHARS = 12_000  # 摘要字符封顶(防巨库撑爆生成 prompt)


def _render_schema_digest(tables: list[MetaTableOut], truncated: bool) -> str:
    """内省结果 → 结构摘要文本(仅元数据,零行数据,ADR-002)。"""
    lines: list[str] = []
    for t in tables[:_SUGGEST_TABLE_CAP]:
        header = f"{t.schema_}.{t.name}"
        if t.comment:
            header += f" —— {t.comment}"
        lines.append(header)
        for c in t.columns:
            col = f"  - {c.name} {c.data_type}"
            if c.comment:
                col += f" -- {c.comment}"
            lines.append(col)
    text = "\n".join(lines)
    if truncated or len(tables) > _SUGGEST_TABLE_CAP:
        text += "\n(表过多,以上仅为部分)"
    return text[:_SUGGEST_DIGEST_CHARS]


class SchemaOverviewSource:
    """组合根适配器:chat 域 SchemaOverviewSource ← internal/meta 同源
    内省(保留表过滤/注释/单 schema/default 直通语义全部单一真理源)。
    数据源不可用/内省失败 → None(service 收敛空清单,降级不 5xx)。"""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def overview(self, *, tenant_key: str, datasource_key: str) -> str | None:
        from kongming.internal.meta import MetaSchemaIn, meta_schema

        try:
            out = await meta_schema(
                MetaSchemaIn(tenant_key=tenant_key, datasource_key=datasource_key),
                self._s,
            )
        except Exception:  # noqa: BLE001  # 建议生成失败时返回空清单，不返回 5xx。
            log.warning("建议问题 schema 摘要内省失败 ds=%s", datasource_key, exc_info=True)
            return None
        return _render_schema_digest(out.tables, out.truncated)
