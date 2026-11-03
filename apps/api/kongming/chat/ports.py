"""chat 域端口,以 typing.Protocol 隔离用例层与存储实现。

ExplainRunner 等实现绑定在组合根(router 适配层):chat 域 service/models
受 import-linter 零框架契约,不得 import internal.service(携 sqlalchemy 引擎层)。
"""

from collections.abc import Callable, Coroutine
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, Protocol

from kongming.chat.models import Suggestion
from kongming.rag.ports import QueryHistoryWriter


class ChatStoreError(Exception):
    """会话或消息写入失败时使用的领域异常，供 service 层处理存储错误。"""


class ChatRepo(Protocol):
    """会话与消息持久化端口。写入及提交错误转换为 ChatStoreError;
    读取和回滚错误保留原异常。"""

    async def find_bound_tenant(self, *, user_id: str, tenant_key: str) -> str | None: ...
    async def find_default_tenant(self, *, user_id: str) -> tuple[str, str] | None: ...
    def create_session(
        self, *, user_id: str, tenant_id: str, title: str, datasource_key: str
    ) -> Coroutine[Any, Any, str]: ...
    async def find_session_owned(
        self, *, session_id: str, user_id: str, tenant_id: str
    ) -> str | None: ...
    async def find_explain_target(
        self, *, message_id: str, user_id: str, tenant_id: str
    ) -> dict[str, object] | None: ...
    def touch_session(self, session_id: str) -> Coroutine[Any, Any, None]: ...
    async def list_sessions(
        self, *, user_id: str, tenant_id: str, limit: int, keyword: str = ""
    ) -> list[dict[str, object]]: ...
    def rename_session(
        self, *, session_id: str, user_id: str, tenant_id: str, title: str
    ) -> Coroutine[Any, Any, dict[str, object] | None]: ...
    async def get_session_title(self, session_id: str) -> str | None: ...
    async def first_user_question(self, session_id: str) -> str | None: ...
    def update_title_if_current(
        self, *, session_id: str, expected: str, new_title: str
    ) -> Coroutine[Any, Any, int]: ...
    async def list_messages(self, *, session_id: str) -> list[dict[str, object]]: ...
    def delete_session(
        self, *, session_id: str, user_id: str, tenant_id: str
    ) -> Coroutine[Any, Any, bool]: ...
    def insert_message(
        self,
        *,
        session_id: str,
        role: str,
        question: str | None = None,
        sql_text: str | None = None,
        result_json: str | None = None,
        summary: str | None = None,
        status: str,
        attachments: list[dict[str, object]] | None = None,
        trace_id: str | None = None,  # 轮次质量信号(可选)
        retry_count: int = 0,
        resolved_question: str | None = None,
        prompt_version: str | None = None,
        fallback_type: str | None = None,
        stage_trace: list[dict[str, object]] | None = None,
    ) -> Coroutine[Any, Any, str]: ...
    async def recent_user_questions(self, session_id: str, *, limit: int = 5) -> list[str]: ...
    async def last_fallback_type(self, session_id: str) -> str | None: ...
    def commit(self) -> Coroutine[Any, Any, None]: ...
    async def rollback(self) -> None: ...


class OperLogger(Protocol):
    """操作日志写入端口，由组合根绑定认证域仓储。"""

    async def add(
        self, user_id: str | None, action: str, target: str | None, detail: dict[str, object]
    ) -> None: ...


@dataclass
class StreamStore:
    """流式终态落库单元(断线续存):独立数据库会话上的端口组装。

    前端断开后请求作用域会话随响应取消而关闭,assistant 落库/回写/标题任务
    必须走本独立单元。
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


class ToolSpecProvider(Protocol):
    """按用户授权集产出可注入 ReAct 内核的工具 schema(唯一真理源=toolcall 注册表)。

    实现侧形态:权限点集查询 + 注册表过滤,独立短会话(同步/流式路径通用)。
    """

    async def authorized_tools(self, user_id: str, tenant_id: str) -> list[dict[str, object]]: ...


class PendingToolReader(Protocol):
    """按 trace 聚合仍待确认的工具调用(chat 响应 pending_tool_calls 附加)。

    实现侧形态:独立短会话只读查询(PENDING_CONFIRM 行经独立事务提交,读已提交)。
    """

    async def list_pending(self, trace_id: str) -> list[dict[str, object]]: ...


class AttachmentChecker(Protocol):
    """图片附件归属校验(实现侧 attachment 域):全部 id 命中(当前用户+
    租户+UPLOADED)返回摘要列表(filename/mime/size_bytes),任一无效返回 None
    (404 同形由 service 抛出)。"""

    async def validate_owned(
        self, *, ids: list[str], user_id: str, tenant_id: str
    ) -> list[dict[str, object]] | None: ...


class GuardrailPort(Protocol):
    """输入判定接收问题和历史文本，返回是否拦截及脱敏文本；输出过滤返回处理后的回答文本。"""

    async def check_input(
        self, *, trace_id: str, user_id: str, tenant_id: str, texts: list[str]
    ) -> tuple[bool, list[str]]: ...

    async def filter_output(
        self, *, trace_id: str, user_id: str, tenant_id: str, texts: list[str]
    ) -> list[str]: ...


class MemoryPort(Protocol):
    """提供长期记忆与偏好提示。空提示表示无内容；查询失败由实现记录并降级，不阻断问答。"""

    async def recall_hint(self, *, user_id: str, tenant_id: str, question: str) -> str: ...

    async def preference_hint(self, *, user_id: str, tenant_id: str) -> str: ...

    async def explicit_intent(self, question: str) -> str | None: ...

    async def form_explicit(self, *, user_id: str, tenant_id: str, content: str) -> bool: ...

    async def form_implicit(
        self,
        *,
        user_id: str,
        tenant_id: str,
        candidates: list[dict[str, object]],
        pref_revision: int | None = None,
    ) -> int: ...

    async def current_revision(self, *, user_id: str, tenant_id: str) -> int | None: ...


class SuggestionTenantSource(Protocol):
    """建议问题租户复校窄端口(ChatRepository 结构化匹配,语义同
    ChatService._resolve_tenant 的两步解析)。"""

    async def find_bound_tenant(self, *, user_id: str, tenant_key: str) -> str | None: ...
    async def find_default_tenant(self, *, user_id: str) -> tuple[str, str] | None: ...


class SuggestionRepo(Protocol):
    """建议缓存持久化端口。find 错误保留原异常;锁、写入、删除和提交错误
    转换为 ChatStoreError。锁由事务结束释放,写入靠租户+数据源唯一键幂等。"""

    async def find(self, *, tenant_id: str, datasource_key: str) -> Suggestion | None: ...
    def lock(self, lock_key: str) -> Coroutine[Any, Any, None]: ...
    def upsert(self, suggestion: Suggestion) -> Coroutine[Any, Any, None]: ...
    def delete_by_datasource(self, datasource_key: str) -> Coroutine[Any, Any, None]: ...
    def commit(self) -> Coroutine[Any, Any, None]: ...
    async def rollback(self) -> None: ...


class SchemaOverviewSource(Protocol):
    """schema 摘要来源窄端口(组合根绑定 internal/meta 同源内省)。

    返回供 LLM 生成建议问句的结构摘要文本(保留表过滤/注释/封顶语义由实现
    保证);数据源不可用/内省失败返回 None(service 据此收敛空清单)。"""

    async def overview(self, *, tenant_key: str, datasource_key: str) -> str | None: ...


class Suggestor(Protocol):
    """建议问题生成端口(实现=AgentClient.suggest_questions,结构化匹配)。

    降级契约:任何失败(不可达/超时/非 200/产出畸形)返回空清单,不抛异常
    ——service 据此按「生成失败」收敛且不落缓存。"""

    async def suggest_questions(self, digest: str) -> list[str]: ...


class SqlRunOutcome(Protocol):
    """执行结果(rerun;internal ExecResult 解耦形态);
    只读 property 形态——frozen dataclass 结构化匹配(mypy 对可变属性
    要求可写,只读 Protocol 兼容只读实现)。"""

    @property
    def verdict(self) -> str: ...
    @property
    def reason(self) -> str | None: ...
    @property
    def columns(self) -> list[str]: ...
    @property
    def rows(self) -> list[list[object]]: ...


class ExecuteRunner(Protocol):
    """SQL 再执行端口:完整六层防御由实现侧保证(internal.service.execute)。"""

    async def run(
        self,
        *,
        trace_id: str,
        tenant_key: str,
        datasource_key: str,
        sql_text: str,
        user_id: str,
    ) -> SqlRunOutcome: ...
