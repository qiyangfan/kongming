"""bot 端口(025):service 依赖反转。零框架纪律:仅 Protocol。"""

from typing import Any, Protocol


class BotRepo(Protocol):
    """sys_bot_definition / sys_api_key 存取。"""

    async def commit(self) -> None: ...

    async def create_bot(self, **kw: Any) -> str: ...

    async def get_by_key(self, *, tenant_id: str, bot_key: str) -> dict[str, Any] | None: ...

    async def get_by_key_anytenant(self, bot_key: str) -> dict[str, Any] | None: ...

    async def get_mine(
        self, *, bot_id: str, tenant_id: str, user_id: str, is_admin: bool
    ) -> dict[str, Any] | None: ...

    async def list_bots(
        self, *, tenant_id: str, user_id: str, is_admin: bool
    ) -> list[dict[str, Any]]: ...

    async def update_bot(self, *, bot_id: str, fields: dict[str, Any]) -> bool: ...

    async def find_datasource(self, *, datasource_key: str) -> dict[str, Any] | None: ...

    async def find_user_status(self, user_id: str) -> str | None: ...

    async def insert_api_key(self, **kw: Any) -> str: ...

    async def find_api_key(self, *, key_hash: str) -> dict[str, Any] | None: ...

    async def list_keys(self, *, bot_id: str) -> list[dict[str, Any]]: ...

    async def revoke_key(self, *, key_id: str, bot_id: str) -> bool: ...

    async def touch_key_used(self, *, key_id: str) -> None: ...


class DynamicToolLister(Protocol):
    """租户 MCP 动态工具目录(028;实现侧 mcp 域):返回快照展开条目
    (qualified/description/schema/read_only/server_key),空集=纯静态。"""

    async def list_dynamic(self, *, tenant_id: str) -> list[dict[str, object]]: ...


class PermReader(Protocol):
    async def list_codes(self, user_id: str) -> list[str]: ...


class ReactRunner(Protocol):
    """ReAct 内核调用(chat.agent_client 适配)。"""

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
    ) -> dict[str, object]: ...

    async def list_models(self) -> list[str]:
        """021 注册表模型名(agent 回调;不可达返回空)。"""
        ...


class UsageSink(Protocol):
    """035:bot 链路用量落库口(llmusage 仓储适配;失败仅日志不阻断)。"""

    async def insert_bot_usage(
        self, records: list[dict[str, Any]], *, tenant_id: str, bot_id: str
    ) -> int: ...


class BotGuardrail(Protocol):
    """护栏(020)消费面:输入判定 + 输出过滤。"""

    async def check_input(
        self, *, trace_id: str, user_id: str, tenant_id: str, texts: list[str]
    ) -> tuple[bool, list[str]]: ...

    async def filter_output(
        self, *, trace_id: str, user_id: str, tenant_id: str, texts: list[str]
    ) -> list[str]: ...


class BotOperLogger(Protocol):
    """操作日志(auth 域 SqlOperLogRepo 适配;025 写操作落审计)。"""

    async def add(
        self,
        user_id: str | None,
        action: str,
        target: str | None,
        detail: dict[str, object],
    ) -> None: ...
