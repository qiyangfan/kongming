"""连接器端口绑定:操作审计、输入护栏与测试调用审计适配。"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.repository import SqlOperLogRepo
from kongming.toolcall.models import ToolAuditEntry
from kongming.toolcall.repository import ToolAuditWriter


class OperLogger:
    """操作审计端口绑定(与业务共用同一 session 事务)。"""

    def __init__(self, session: AsyncSession, actor_id: str) -> None:
        self._repo = SqlOperLogRepo(session)
        self._actor = actor_id

    async def log(self, *, action: str, target: str, detail: dict[str, object]) -> None:
        await self._repo.add(self._actor, action, target, detail)


class ConnectorGuardrailAdapter:
    """输入护栏端口绑定(独立短会话,与 bot 上架护栏同闸)。"""

    async def check(
        self, *, trace_id: str, user_id: str, tenant_id: str, texts: list[str]
    ) -> tuple[bool, list[str]]:
        from kongming.db import get_sessionmaker
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


class ConnectorTestCallAuditor:
    """工具审计端口绑定(测试调用落既有审计表;ToolAuditWriter 自带独立短事务)。"""

    async def record_test_call(
        self,
        *,
        tool_name: str,
        status: str,
        error_reason: str | None,
        duration_ms: int,
        tenant_id: str,
        user_id: str,
        args_digest: str,
    ) -> None:
        await ToolAuditWriter().record(
            ToolAuditEntry(
                trace_id=f"connector-test-{uuid.uuid4().hex[:12]}",
                user_id=user_id,
                tenant_id=tenant_id,
                tool_name=tool_name,
                status=status,
                args_digest=args_digest,
                error_reason=error_reason,
                duration_ms=duration_ms,
                channel="test_call",
                domain="http",
            )
        )
