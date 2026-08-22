"""chat 域端口(typing.Protocol 反转,宪法 Article II)。

ExplainRunner 由组合根(router 适配层)绑定 internal.service.explain——chat 域
service/models 受 import-linter 零框架契约,不可间接 import internal.service
(携 sqlalchemy 引擎层),007 uuid7 拆分同款教训;适配层组合与 009 rag⇄datasource 同构。
"""

from collections.abc import Callable, Coroutine
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, Protocol

from kongming.rag.ports import QueryHistoryWriter


class ChatStoreError(Exception):
    """chat 持久化基础设施失败(013 构造反转):service 容错段据此捕获,
    替代原 SQLAlchemyError 直捕——领域层由此零 sqlalchemy import。
    由 ChatRepository 写路径方法与 commit 的转译边界抛出(读路径不转译)。"""


class ChatRepo(Protocol):
    """chat 持久化端口(013 构造反转):镜像 ChatRepository 公开方法 +
    commit/rollback 事务原语;service 仅持本端口,不触碰 session。

    写路径方法(create_session/touch_session/rename_session/
    update_title_if_current/insert_message/delete_session)与 commit
    失败抛 ChatStoreError(已转译);读路径方法失败抛基础设施原异常
    (service 不容错,冒出转 500 为既定语义);rollback 不转译(容错段内
    再失败逃逸为既定语义)。
    """

    async def find_bound_tenant(self, *, user_id: str, tenant_key: str) -> str | None: ...
    async def find_default_tenant(self, *, user_id: str) -> tuple[str, str] | None: ...
    async def create_session(
        self, *, user_id: str, tenant_id: str, title: str, datasource_key: str
    ) -> str: ...
    async def find_session_owned(
        self, *, session_id: str, user_id: str, tenant_id: str
    ) -> str | None: ...
    async def find_explain_target(
        self, *, message_id: str, user_id: str, tenant_id: str
    ) -> dict[str, object] | None: ...
    async def touch_session(self, session_id: str) -> None: ...
    async def list_sessions(
        self, *, user_id: str, tenant_id: str, limit: int, keyword: str = ""
    ) -> list[dict[str, object]]: ...
    async def rename_session(
        self, *, session_id: str, user_id: str, tenant_id: str, title: str
    ) -> dict[str, object] | None: ...
    async def get_session_title(self, session_id: str) -> str | None: ...
    async def first_user_question(self, session_id: str) -> str | None: ...
    async def update_title_if_current(
        self, *, session_id: str, expected: str, new_title: str
    ) -> int: ...
    async def list_messages(self, *, session_id: str) -> list[dict[str, object]]: ...
    async def delete_session(
        self, *, session_id: str, user_id: str, tenant_id: str
    ) -> bool: ...
    async def insert_message(
        self,
        *,
        session_id: str,
        role: str,
        question: str | None = None,
        sql_text: str | None = None,
        result_json: str | None = None,
        summary: str | None = None,
        status: str,
    ) -> str: ...
    async def recent_user_questions(self, session_id: str, *, limit: int = 5) -> list[str]: ...
    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...


class OperLogger(Protocol):
    """操作日志写入窄端口(015 导出审计;实现=auth 域 SqlOperLogRepo,
    组合根绑定——chat 域不直接依赖 auth 基础设施,与 013 反转同范式)。"""

    async def add(
        self, user_id: str | None, action: str, target: str | None, detail: dict[str, object]
    ) -> None: ...


@dataclass
class StreamStore:
    """流式终态落库单元(017 断线续存):独立数据库会话上的端口组装。

    前端断开后请求作用域会话随响应取消而关闭,assistant 落库/回写/标题任务
    必须走本独立单元(design §4;010 title_tasks 同款模式)。
    """

    repo: ChatRepo
    history_writer: QueryHistoryWriter | None = None
    # 标题改写触发(组合根绑定 title_tasks.rewrite_session_title;None=未接线)
    trigger_title_rewrite: Callable[[str], Coroutine[Any, Any, None]] | None = None


class StreamStoreFactory(Protocol):
    """断线续存落库单元工厂:每次调用开启一个独立会话(异步上下文管理器)。"""

    def __call__(self) -> AbstractAsyncContextManager[StreamStore]: ...


@dataclass(frozen=True)
class ExplainOutcome:
    """执行计划领域结果(chat 域视角,与 internal ExplainResult 解耦)。"""

    verdict: str  # PASS / BLOCKED / ERROR
    reason: str | None = None
    plan: object | None = None


class ExplainRunner(Protocol):
    """执行计划运行端口;完整防线(防火墙/改写/复检/只读池)由实现侧保证。"""

    async def explain(
        self,
        *,
        trace_id: str,
        tenant_key: str,
        datasource_key: str,
        sql_text: str,
        user_id: str,
    ) -> ExplainOutcome: ...
