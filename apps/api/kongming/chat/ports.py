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
        attachments: list[dict[str, object]] | None = None,  # 030
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


class ToolSpecProvider(Protocol):
    """按用户授权集产出可注入 ReAct 内核的工具 schema(019;唯一真理源=toolcall 注册表)。

    实现侧形态:权限点集查询 + 注册表过滤,独立短会话(同步/流式路径通用)。
    """

    async def authorized_tools(
        self, user_id: str, tenant_id: str
    ) -> list[dict[str, object]]: ...


class PendingToolReader(Protocol):
    """按 trace 聚合仍待确认的工具调用(019;chat 响应 pending_tool_calls 附加)。

    实现侧形态:独立短会话只读查询(PENDING_CONFIRM 行经独立事务提交,读已提交)。
    """

    async def list_pending(self, trace_id: str) -> list[dict[str, object]]: ...


class AttachmentChecker(Protocol):
    """图片附件归属校验(030;实现侧 attachment 域):全部 id 命中(当前用户+
    租户+UPLOADED)返回摘要列表(filename/mime/size_bytes),任一无效返回 None
    (404 同形由 service 抛出)。"""

    async def validate_owned(
        self, *, ids: list[str], user_id: str, tenant_id: str
    ) -> list[dict[str, object]] | None: ...


class GuardrailPort(Protocol):
    """prompt 安全护栏(020):chat 域消费面。输入判定返回 (blocked, 脱敏后文本序列
    [question, *history]);输出过滤返回处理后文本序列(拦截=安全文案替换)。
    实现侧 fail-closed;未注入(None)时链路行为与护栏引入前一致。"""

    async def check_input(
        self, *, trace_id: str, user_id: str, tenant_id: str, texts: list[str]
    ) -> tuple[bool, list[str]]: ...

    async def filter_output(
        self, *, trace_id: str, user_id: str, tenant_id: str, texts: list[str]
    ) -> list[str]: ...


class MemoryPort(Protocol):
    """用户长期记忆(022):chat 域消费面。recall_hint 返回注入段(空串=无注入
    零回归);explicit_intent 识别「记住…」;form_explicit/form_implicit 形成。
    实现侧全部失败降级(仅日志),永不阻断问答主链路。"""

    async def recall_hint(self, *, user_id: str, tenant_id: str, question: str) -> str: ...

    async def explicit_intent(self, question: str) -> str | None: ...

    async def form_explicit(self, *, user_id: str, tenant_id: str, content: str) -> bool: ...

    async def form_implicit(
        self, *, user_id: str, tenant_id: str, candidates: list[dict[str, str]]
    ) -> int: ...


class SqlRunOutcome(Protocol):
    """执行结果(026 rerun;internal ExecResult 解耦形态);
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
    """SQL 再执行端口(026):完整六层防御由实现侧保证(internal.service.execute)。"""

    async def run(
        self,
        *,
        trace_id: str,
        tenant_key: str,
        datasource_key: str,
        sql_text: str,
        user_id: str,
    ) -> SqlRunOutcome: ...
