"""toolcall 端口(019):service 依赖反转,组合根在 router。

零框架纪律:本模块仅 typing.Protocol,不 import fastapi/sqlalchemy/redis。
"""

from datetime import datetime
from typing import Any, Protocol

from kongming.toolcall.models import PendingConfirm, ToolAuditEntry, ToolDef


class ToolRepo(Protocol):
    """sys_tool_audit / sys_announcement 存取 + L1 租户解析复校(仓储实现)。"""

    async def resolve_tenant(self, tenant_key: str) -> str: ...

    async def assert_bound(self, *, user_id: str, tenant_id: str) -> None: ...

    async def get_for_update(self, confirm_id: str) -> PendingConfirm | None: ...

    async def count_write_calls(self, trace_id: str, write_tools: list[str]) -> int:
        """同 trace 写工具已有调用数(全状态含挂起/拒绝;意图审核计数口径)。"""
        ...

    async def mark_status(
        self,
        confirm_id: str,
        status: str,
        *,
        clear_reason: bool = False,
        args_digest: str | None = None,
    ) -> None:
        """状态推进;args_digest 非空时覆写参数摘要(100 创建结果回填,
        None=不触碰;语义详见仓储实现)。"""
        ...

    async def find_announcement(self, *, tenant_id: str, idempotency_key: str) -> str | None: ...

    async def list_pending_by_trace(self, trace_id: str) -> list[dict[str, Any]]: ...

    async def insert_announcement(
        self,
        *,
        tenant_id: str,
        title: str,
        content: str,
        publisher_id: str,
        idempotency_key: str,
    ) -> tuple[str, bool]: ...

    async def page_audit(
        self,
        *,
        page: int,
        size: int,
        tool_name: str | None = None,
        status: str | None = None,
        user_id: str | None = None,
        tenant_id: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[list[dict[str, Any]], int]: ...

    async def commit(self) -> None: ...


class AuditWriter(Protocol):
    """执行审计独立短事务写入(失败仅日志,不阻断);返回行 id(失败 None)。"""

    async def record(self, entry: ToolAuditEntry) -> str | None: ...


class IntentReviewer(Protocol):
    """工具意图审核(020):在参数 JSON Schema 校验之前;fail-closed 由实现保证。"""

    async def allow(
        self,
        *,
        trace_id: str,
        user_id: str,
        tenant_id: str,
        tool_write: bool,
        write_attempts: int,
    ) -> bool: ...


class PermReader(Protocol):
    """用户权限点集查询(auth 域适配)。"""

    async def list_codes(self, user_id: str) -> list[str]: ...


class SqlExecutor(Protocol):
    """execute_sql 工具委托:既有执行路由全链路(internal.service.execute 适配)。

    返回 dict 而非 internal 类型——domain 不依赖基础设施层类型(端口解耦)。
    键:verdict/reason/columns/rows/row_count/duration_ms。
    """

    async def run(
        self,
        *,
        trace_id: str,
        tenant_key: str,
        datasource_key: str,
        sql_text: str,
        user_id: str,
        question: str | None,
    ) -> dict[str, object]: ...


class WebSearchPort(Protocol):
    """094:联网检索(websearch 域,检索供应商经网关出站)。

    实现侧(组合根)负责凭证装配、供应商协议适配、超时与错误分类;
    领域只消费「检索卡列表」(title/url/snippet),失败经 WebSearchError
    上浮分类(分发兜底映射 ERROR + 审计,不裸崩)。
    """

    async def search(self, *, query: str, count: int) -> list[dict[str, object]]: ...


class SandboxExecutor(Protocol):
    """run_python_code 工具委托(027):沙箱执行+审计(实现侧 sandbox 域)。
    data=None 纯计算;非 None={"columns","rows"}(六层防御取数结果,
    JSON 安全值)。返回 LLM 投影 dict(status/stdout/error/images_count/
    duration_ms[/injected/image_note])——images base64 不进投影(design
    D2 修正案:agent 观察截断 2000)。实现侧未启用由调用方判定(组合根
    未注入=工具不可用),运行期故障收敛投影 status=ERROR。"""

    async def execute(
        self,
        *,
        code: str,
        data: dict[str, object] | None,
        trace_id: str,
        user_id: str,
        tenant_id: str,
    ) -> dict[str, object]: ...


class RagRetriever(Protocol):
    """rag_retrieve 工具委托:既有三路召回(rag 域适配)。"""

    async def retrieve(
        self, *, tenant_key: str, datasource_key: str, question: str
    ) -> dict[str, object]: ...  # 键:schema_hint/history_sql


class DynamicToolResolver(Protocol):
    """动态工具解析(028 MCP 桥接):静态注册表未命中时查租户启用
    server 的快照;命中返回按 fail-closed 语义构造的 ToolDef
    (perm_code=tool:mcp:use,无 readOnlyHint=写工具+确认),未命中 None。"""

    async def resolve(self, *, tenant_id: str, name: str) -> ToolDef | None: ...


class McpToolCaller(Protocol):
    """MCP 工具出站执行(028;实现侧 mcp 域):解析→取明文(瞬时)→
    调用;超时/断连/协议错收敛 McpCallError(分发兜底映射 ERROR)。"""

    async def call(
        self, *, tenant_id: str, qualified: str, args: dict[str, object]
    ) -> dict[str, object]: ...


class ConnectorToolGate(Protocol):
    """连接器工具门(099 http-connector-domain;实现侧 connector 域 +
    网关切面适配):resolve=按租户解析启用连接器为 fail-closed ToolDef
    (perm_code=tool:http:call,写语义=requires_confirmation);
    call=经网关切面执行(出口校验→SSRF→出站→分类),失败收敛
    ConnectorError(分发兜底映射 ERROR + 审计)。"""

    async def resolve(self, *, tenant_id: str, name: str) -> ToolDef | None: ...

    async def call(
        self, *, tenant_id: str, name: str, args: dict[str, object]
    ) -> dict[str, object]: ...


class RateLimiter(Protocol):
    """固定窗口限流(Redis);不可用 fail-open 仅告警(与 007 吊销降级同口径)。"""

    async def allow(self, key: str, *, limit: int, window_sec: int) -> bool: ...


class ConfirmReplyStore(Protocol):
    """确认回执落会话消息(chat 域适配);归属校验与 chat 404 同形。"""

    async def session_owned(self, *, session_id: str, user_id: str, tenant_id: str) -> bool: ...

    async def append_reply(self, *, session_id: str, content: str) -> None: ...
