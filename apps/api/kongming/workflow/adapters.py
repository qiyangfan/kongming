"""workflow 端口绑定:SQL 执行与 ReAct 回调适配(路由与触发链路共用)。"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.repository import SqlPermRepo
from kongming.chat.agent_client import AgentClient
from kongming.internal import service as internal_service
from kongming.internal.service import (
    InvalidDatasource,
    TenantBindingForbidden,
    TenantNotFound,
)
from kongming.toolcall.registry import filter_specs


class InternalSqlRunner:
    """绑定 internal SQL 执行服务，并沿用触发者身份校验。"""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def run_sql(
        self,
        *,
        sql: str,
        datasource_key: str,
        trace_id: str,
        tenant_key: str,
        user_id: str,
    ) -> dict[str, Any]:
        try:
            r = await internal_service.execute(
                self._s,
                trace_id=trace_id,
                tenant_key=tenant_key,
                datasource_key=datasource_key,
                sql_text=sql,
                user_id=user_id,
                question=None,
                limit=1000,
            )
        except (InvalidDatasource, TenantNotFound, TenantBindingForbidden) as e:
            # 竞态/非法源收敛(与 ExecuteRunner 同范式)
            return {"verdict": "BLOCKED", "reason": str(e), "columns": [], "rows": []}
        return {
            "verdict": r.verdict,
            "reason": r.reason,
            "columns": r.columns,
            "rows": r.rows,
        }


class AgentWorkflowRunner:
    """AgentRunner 绑定:ReAct 回调;工具=触发者权限点过滤集。"""

    def __init__(self, session: AsyncSession, agent: AgentClient) -> None:
        self._s = session
        self._agent = agent

    async def run_agent(
        self,
        *,
        instruction: str,
        datasource_key: str,
        trace_id: str,
        tenant_key: str,
        user_id: str,
    ) -> dict[str, Any]:
        perms = await SqlPermRepo(self._s).list_codes(user_id)
        tools = [dict(spec) for spec in filter_specs(perms)]
        try:
            reply = await self._agent.query_react(
                trace_id=trace_id,
                tenant_key=tenant_key,
                question=instruction,
                user_id=user_id,
                history=[],
                datasource_key=datasource_key,
                tools=tools,
                bot_system_prompt="",
                model=None,
                channel="workflow",  # 审计溯源
            )
        except Exception as e:  # noqa: BLE001  # 节点级收敛(fail-fast 由引擎定)
            return {"success": False, "error": str(e)[:200]}
        return {
            "success": reply.success,
            "summary": reply.summary,
            "sql": reply.sql,
            "columns": reply.columns,
            "rows": reply.rows,
            "error": reply.error,
        }
