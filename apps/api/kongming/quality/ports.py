"""quality 域端口:仓储协议(router/service 依赖本端口,零 SQLAlchemy 泄漏)。

以 Protocol 隔离 domain 与框架及具体存储实现。
"""

from collections.abc import Coroutine
from typing import Any, Protocol


class QualityRepo(Protocol):
    """轮次质量信号聚合仓储(chat_message 四列 + chat_session 维度
    + 评分观测(chat_turn_score LEFT JOIN 只读)。"""

    def overview(
        self,
        *,
        tenant_ids: list[str] | None,
        start: object | None,
        end: object | None,
        low_score_threshold: int,
    ) -> Coroutine[Any, Any, dict[str, Any]]: ...

    def daily_trend(
        self, *, tenant_ids: list[str] | None, days: int
    ) -> Coroutine[Any, Any, list[dict[str, Any]]]: ...

    def by_datasource(
        self, *, tenant_ids: list[str] | None, start: object | None, end: object | None
    ) -> Coroutine[Any, Any, list[dict[str, Any]]]: ...

    def by_prompt_version(
        self, *, tenant_ids: list[str] | None, start: object | None, end: object | None
    ) -> Coroutine[Any, Any, list[dict[str, Any]]]: ...

    def page(
        self,
        *,
        tenant_ids: list[str] | None,
        status: str | None,
        datasource_key: str | None,
        fallback: str | None,
        start: object | None,
        end: object | None,
        page: int,
        size: int,
    ) -> Coroutine[Any, Any, tuple[int, list[dict[str, Any]]]]: ...

    def score_detail(
        self, *, tenant_ids: list[str] | None, message_id: str
    ) -> Coroutine[Any, Any, dict[str, Any] | None]: ...

    def session_context(
        self, *, tenant_ids: list[str] | None, message_id: str, limit: int
    ) -> Coroutine[Any, Any, dict[str, Any] | None]: ...

    def stage_trace_detail(
        self, *, tenant_ids: list[str] | None, message_id: str
    ) -> Coroutine[Any, Any, dict[str, Any] | None]: ...

    def export_rows(
        self,
        *,
        tenant_ids: list[str] | None,
        status: str | None,
        datasource_key: str | None,
        fallback: str | None,
        start: object | None,
        end: object | None,
        limit: int,
    ) -> Coroutine[Any, Any, list[dict[str, Any]]]: ...

    def list_bound_tenant_ids(self, *, user_id: str) -> Coroutine[Any, Any, list[str]]: ...

    def user_has_scope_all(self, *, user_id: str) -> Coroutine[Any, Any, bool]: ...
