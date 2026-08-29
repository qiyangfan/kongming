"""mcp 端口(028):出站调用与配置存储反转,用例层零框架依赖。"""

from typing import Any, Protocol

from kongming.mcp.models import DiscoveredTool


class McpCallError(Exception):
    """出站调用结构化错误(分类回灌 agent 可自愈改道;文案不含凭证)。"""

    def __init__(self, kind: str, detail: str) -> None:
        super().__init__(f"{kind}: {detail}")
        self.kind = kind  # timeout / conn / protocol / remote
        self.detail = detail


class McpCaller(Protocol):
    """MCP 出站调用(028):控制面唯一出站点(ADR-002 延伸)。

    headers 为调用瞬时解密明文(实现侧与调用方 MUST NOT 落日志/审计)。
    超时/大小闸在实现侧(默认 15s/1MB)。"""

    async def handshake(self, *, endpoint: str, headers: dict[str, str]) -> int:
        """initialize+tools/list 探测(连通性测试);返回发现工具数。"""
        ...

    async def list_tools(
        self, *, endpoint: str, headers: dict[str, str]
    ) -> list[DiscoveredTool]: ...

    async def call(
        self,
        *,
        endpoint: str,
        headers: dict[str, str],
        tool_name: str,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        """tools/call;返回 {content, truncated, original_size}。"""
        ...


class McpRepo(Protocol):
    """配置存储端口(端口反转:service 不 import 基础设施实现)。"""

    async def create(
        self,
        *,
        tenant_id: str,
        server_key: str,
        name: str,
        endpoint: str,
        headers: dict[str, str],
    ) -> str: ...

    async def update(
        self,
        *,
        server_id: str,
        tenant_id: str,
        name: str,
        endpoint: str,
        headers: dict[str, str | None],
    ) -> None: ...

    async def set_enabled(self, *, server_id: str, tenant_id: str, enabled: bool) -> bool: ...

    async def delete(self, *, server_id: str, tenant_id: str) -> bool: ...

    async def get(self, *, server_id: str, tenant_id: str) -> dict[str, Any] | None: ...

    async def list_masked(self, *, tenant_id: str) -> list[dict[str, Any]]: ...

    async def save_snapshot(
        self, *, server_id: str, tenant_id: str, tools: list[dict[str, Any]], error: str
    ) -> None: ...

    async def find_enabled_by_tool(
        self, *, tenant_id: str, qualified: str
    ) -> dict[str, Any] | None: ...

    async def list_enabled(self, *, tenant_id: str) -> list[dict[str, Any]]: ...

    async def get_call_target(
        self, *, tenant_id: str, server_key: str, require_enabled: bool = True
    ) -> tuple[str, dict[str, str]] | None: ...

    async def commit(self) -> None: ...
