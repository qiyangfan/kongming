"""workflow 端口(029):节点执行器与存储反转,引擎零框架依赖。"""

from typing import Any, Protocol


class SqlRunner(Protocol):
    """sql 节点执行(029):实现侧=internal 执行全链路(六层防御,
    触发者身份);返回 {verdict, reason, columns, rows}。"""

    async def run_sql(
        self,
        *,
        sql: str,
        datasource_key: str,
        trace_id: str,
        tenant_key: str,
        user_id: str,
    ) -> dict[str, Any]: ...


class HttpRunner(Protocol):
    """http 节点执行(029):实现侧保证 SSRF 判定(私网/回环/链路本地/
    元数据/保留段拒绝,不发出请求)+不跟随重定向+1MB 截断+15s 超时;
    返回 {status, body} 或抛 SsrFBlocked。"""

    async def run_http(
        self,
        *,
        url: str,
        method: str,
        headers: dict[str, str],
        body: str | None,
    ) -> dict[str, Any]: ...


class SsrFBlocked(Exception):
    """SSRF 判定拒绝(节点 FAILED,非 run 级错误)。"""


class AgentRunner(Protocol):
    """agent 节点执行(029):实现侧=019 ReAct 回调(工具集=触发者
    权限点过滤,channel="workflow");返回 {success, summary, sql, columns, rows}。"""

    async def run_agent(
        self,
        *,
        instruction: str,
        datasource_key: str,
        trace_id: str,
        tenant_key: str,
        user_id: str,
    ) -> dict[str, Any]: ...


class WorkflowRepo(Protocol):
    """定义与 run 存储(端口反转)。"""

    async def next_version(self, *, tenant_id: str, wf_key: str) -> int: ...

    async def insert_def(
        self,
        *,
        tenant_id: str,
        wf_key: str,
        name: str,
        version: int,
        definition: dict[str, Any],
        created_by: str,
        team_id: Any = None,  # 032:继承上一版归属
    ) -> str: ...

    async def latest_def(self, *, tenant_id: str, wf_key: str) -> dict[str, Any] | None: ...

    async def get_def(
        self, *, tenant_id: str, wf_key: str, version: int
    ) -> dict[str, Any] | None: ...

    async def list_latest(
        self, *, tenant_id: str, user_id: str = "", team_ids: list[str] | None = None
    ) -> list[dict[str, Any]]: ...

    async def list_versions(self, *, tenant_id: str, wf_key: str) -> list[dict[str, Any]]: ...

    async def set_enabled(self, *, def_id: str, tenant_id: str, enabled: bool) -> bool: ...

    async def delete_key(self, *, tenant_id: str, wf_key: str) -> bool: ...

    async def insert_run(
        self,
        *,
        tenant_id: str,
        wf_key: str,
        version: int,
        trigger_source: str,
        inputs: dict[str, Any],
        status: str,
        node_runs: list[dict[str, Any]],
        error: str,
        duration_ms: int,
    ) -> str: ...

    async def page_runs(
        self, *, tenant_id: str, wf_key: str, page: int, size: int
    ) -> tuple[int, list[dict[str, Any]]]: ...

    async def get_run(
        self, *, tenant_id: str, wf_key: str, run_id: str
    ) -> dict[str, Any] | None: ...

    async def commit(self) -> None: ...
