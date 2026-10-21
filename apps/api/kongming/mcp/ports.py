"""mcp 端口:出站调用与配置存储反转,用例层零框架依赖。"""

from typing import Any, Protocol

from kongming.mcp.models import ProbeResult


class McpCallError(Exception):
    """出站调用结构化错误(分类回灌 agent 可自愈改道;文案不含凭证)。"""

    def __init__(self, kind: str, detail: str) -> None:
        super().__init__(f"{kind}: {detail}")
        self.kind = kind  # timeout / conn / protocol / remote / unsupported
        self.detail = detail


class McpCaller(Protocol):
    """MCP 出站调用(扩展三类原语探测与只读消费)。

    headers 为调用瞬时解密明文(实现侧与调用方 MUST NOT 落日志/审计)。
    超时/大小闸在实现侧(默认 15s/1MB)。"""

    async def probe(self, *, endpoint: str, headers: dict[str, str]) -> ProbeResult:
        """initialize 握手 + 三原语并行探测:能力未声明即时短路,
        方法不存在=不支持计数 0,超时/拒连/协议错=结构化失败。"""
        ...

    async def read_resource(
        self, *, endpoint: str, headers: dict[str, str], uri: str
    ) -> dict[str, Any]:
        """resources/read:文本形态限定(blob 拒绝),返回
        {content, truncated, original_size}。"""
        ...

    async def get_prompt(
        self,
        *,
        endpoint: str,
        headers: dict[str, str],
        name: str,
        arguments: dict[str, str],
    ) -> dict[str, Any]:
        """prompts/get:模板按名渲染(管理面预览数据源),返回 {text}。"""
        ...

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
    """MCP 配置存储端口，具体存储由组合根注入。"""

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
        self,
        *,
        server_id: str,
        tenant_id: str,
        tools: list[dict[str, Any]],
        resources: list[dict[str, Any]],
        prompts: list[dict[str, Any]],
        error: str,
    ) -> None: ...

    async def get_enabled_resources(
        self, *, tenant_id: str, server_key: str
    ) -> list[dict[str, Any]]:
        """read_resource 执行期的 uri 白名单(来自探测快照);
        仅启用 server(禁用/删除 → 空=全拒,与工具目录消失同形)。"""
        ...

    async def find_enabled_by_tool(
        self, *, tenant_id: str, qualified: str
    ) -> dict[str, Any] | None: ...

    async def list_enabled(self, *, tenant_id: str) -> list[dict[str, Any]]: ...

    async def get_call_target(
        self, *, tenant_id: str, server_key: str, require_enabled: bool = True
    ) -> tuple[str, dict[str, str]] | None: ...

    async def commit(self) -> None: ...
