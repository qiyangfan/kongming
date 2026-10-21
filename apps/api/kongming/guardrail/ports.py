"""guardrail 端口:service 依赖反转。零框架纪律:仅 Protocol。"""

from typing import Any, Protocol


class GuardrailConfigStore(Protocol):
    """规则配置覆盖(sys_guardrail_rule)读写。"""

    async def list_overrides(self) -> dict[str, dict[str, Any]]: ...

    async def upsert_override(
        self,
        *,
        rule_name: str,
        enabled: bool,
        action: str | None,
        threshold: int | None,
        updated_by: str,
    ) -> None: ...


class GuardrailAuditWriter(Protocol):
    """护栏审计独立短事务写入(失败仅日志不阻断)。"""

    async def record(
        self,
        *,
        trace_id: str,
        user_id: str | None,
        tenant_id: str,
        guardrail_type: str,
        rule_name: str,
        action: str,
        content_digest: str,
    ) -> None: ...
