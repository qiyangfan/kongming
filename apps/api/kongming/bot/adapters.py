"""bot 组合根适配器:ReactRunner 适配 chat AgentClient;护栏/MCP/用量绑定。"""

from sqlalchemy.ext.asyncio import AsyncSession

from kongming.chat.agent_client import AgentClient
from kongming.db import get_sessionmaker


class AgentReactRunner:
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
            collect_usage=True,  # 仅 Bot 调用启用请求内用量采集。
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
            "usage_records": reply.usage_records,  # 同步采集透传
        }


class BotGuardrailAdapter:
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


class McpDynamicToolLister:
    """DynamicToolLister 绑定(mcp 域服务装配,与 toolcall 组合根同构)。"""

    def __init__(self, session: AsyncSession) -> None:
        from kongming.config import get_settings
        from kongming.datasource.cipher import get_cipher
        from kongming.mcp.breaker import build_mcp_caller, get_mcp_breaker
        from kongming.mcp.repository import SqlMcpRepo
        from kongming.mcp.service import McpService

        settings = get_settings()
        self._svc = McpService(
            SqlMcpRepo(session, get_cipher(settings.datasource_encrypt_key)),
            build_mcp_caller(settings),
            breaker=get_mcp_breaker(),
        )

    async def list_dynamic(self, *, tenant_id: str) -> list[dict[str, object]]:
        return await self._svc.list_dynamic_tools(tenant_id=tenant_id)


class UsageSinkAdapter:
    """UsageSink 适配(llmusage 仓储;组合根装配,保 bot.service 零框架)。

    独立短会话落库:sink 在 agent 分钟级长外呼之后才被调用,若复用
    请求级会话,外呼期间白占连接池配额,且 sink 自 commit 会提前提交请求会话
    上的挂起写——与 BotGuardrailAdapter 同款独立短会话范式。
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
