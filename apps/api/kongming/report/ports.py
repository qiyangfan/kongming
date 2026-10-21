"""report 域端口,定义持久化、agent 网关和 SQL 执行接口。

具体实现由组合根装配,用例层不依赖存储和外部服务。
"""

from typing import Any, Protocol

from kongming.report.models import SectionNode


class ReportRepo(Protocol):
    async def insert_report(
        self,
        *,
        report_id: str,
        tenant_id: str,
        user_id: str,
        session_id: str | None,
        title: str,
        datasource_key: str,
        instruction: str,
        trace_id: str | None,
    ) -> str: ...

    async def get_report(
        self, *, report_id: str, tenant_id: str, user_id: str
    ) -> dict[str, Any] | None: ...

    async def set_phase(self, *, report_id: str, phase: str, tenant_id: str) -> None: ...

    async def finish_generation(
        self,
        *,
        report_id: str,
        status: str,
        fail_reason: str | None,
        usage_summary: dict[str, object] | None,
        version: int | None,
        version_meta: dict[str, object] | None,
        data_time_point: Any,
        created_by: str,
        sections: list[SectionNode],
    ) -> None:
        """终态落库(单事务):仅当行仍处于 GENERATING 才生效(删除竞态守卫);
        version 非 None 时同事务写版本与章节树。"""

    async def claim_next_version(
        self, *, report_id: str, tenant_id: str, user_id: str, expected_version: int
    ) -> int | None: ...

    async def cas_version(
        self, *, report_id: str, tenant_id: str, user_id: str, expected_version: int
    ) -> int | None:
        """乐观锁:仅当当前版本=expected 且状态可编辑时推进到 expected+1,
        返回新版本号;不匹配/不可编辑返回 None(冲突,不静默覆盖)。"""

    async def rename(self, *, report_id: str, tenant_id: str, user_id: str, title: str) -> None: ...

    async def set_status_editable(self, *, report_id: str, status: str) -> None:
        """READY/PARTIAL 之间的状态平移(刷新后回升 READY);其余状态不动。"""

    async def insert_version_with_sections(
        self,
        *,
        report_id: str,
        version: int,
        meta: dict[str, object],
        data_time_point: Any,
        created_by: str,
        sections: list[SectionNode],
    ) -> None: ...

    async def get_version_meta(self, *, report_id: str, version: int) -> dict[str, Any] | None: ...

    async def get_sections(self, *, version_id: str) -> list[dict[str, Any]]: ...

    async def list_versions(self, *, report_id: str) -> list[dict[str, Any]]: ...

    async def list_reports(
        self, *, tenant_id: str, user_id: str, page: int, size: int
    ) -> tuple[list[dict[str, Any]], int]: ...

    async def soft_delete(self, *, report_id: str, tenant_id: str, user_id: str) -> bool: ...


class AgentReportGateway(Protocol):
    async def generate_report(self, payload: dict[str, object]) -> dict[str, object] | None:
        """回调 agent /internal/generate-report;不可达/超时/畸形返回 None
        (调用方收敛 FAILED 终态,不裸抛)。"""


class SqlDefenseRunner(Protocol):
    async def run(
        self, *, trace_id: str, tenant_key: str, datasource_key: str, sql_text: str, user_id: str
    ) -> tuple[str, list[str], list[list[object]], str | None]: ...


class OperLogPort(Protocol):
    async def add(
        self, user_id: str | None, action: str, target: str | None, detail: dict[str, object]
    ) -> None: ...


class ChatCardWriter(Protocol):
    async def append_report_card(
        self,
        *,
        session_id: str,
        tenant_id: str,
        report_id: str,
        version: int,
        status: str,
        title: str,
        summary: str,
        trace_id: str | None,
    ) -> None:
        """生成完成后向发起会话追加 assistant 消息(result_json.report),
        前端经既有 artifacts 派生渲染 report 卡。"""
