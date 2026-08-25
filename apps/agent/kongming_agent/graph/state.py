"""LangGraph 编排骨架(继承前系统已验证的 8 节点设计)。

节点链:understand → retrieve → generate → validate → execute → selfheal → summarize
- execute:回调控制面 /internal/sql/execute(不是直连 DB)
- selfheal:错误分类重试;权限类(verdict=BLOCKED/预检非只读)不重试
"""

from typing import NotRequired, TypedDict

from pydantic import BaseModel


class ExecutionResult(BaseModel):
    """控制面执行路由三态响应的 state 载体(与 sql-execution spec 对齐)。"""

    verdict: str  # PASS / BLOCKED / ERROR
    reason: str | None = None
    columns: list[str] = []
    rows: list[list[object]] = []
    duration_ms: int = 0


class ValidationResult(BaseModel):
    """validate 节点本地预检结果。permission_block=True 即权限类,直接终态不重试。"""

    passed: bool
    permission_block: bool = False
    reason: str = ""


class AgentState(TypedDict, total=False):
    trace_id: str
    tenant_key: str
    datasource_key: str
    user_id: NotRequired[str | None]
    question: str  # 原始问句(审计/喂回以此为准)
    history: list[str]  # 多轮上下文:user 问句列表,旧→新(指代消解用)
    resolved_question: NotRequired[str]  # understand 产物(指代消解后)
    schema_hint: NotRequired[str]  # retrieve 产物(拼好的 schema 提示文本)
    history_sql: NotRequired[str]  # retrieve 产物(召回的历史成功 SQL 段,009)
    generated_sql: NotRequired[str]  # generate 产物
    validation: NotRequired[ValidationResult]  # validate 产物(本地预检)
    execution: NotRequired[ExecutionResult]  # execute 产物(回调)
    error: NotRequired[str]  # selfheal 写入,喂回 generate
    retry_count: NotRequired[int]
    summary: NotRequired[str]
    insights: NotRequired[list[dict[str, str]]]
    stats: NotRequired[dict[str, object]]
    end_reason: NotRequired[str]  # 终态分类(blocked 等)
    memory_hint: NotRequired[str]  # 022:召回的用户已知信息段(控制面注入)
    relations_hint: NotRequired[str]  # 023:召回集合内 JOIN path 段
    metric_hints: NotRequired[str]  # 023:命中指标口径段
    doc_hints: NotRequired[list[dict[str, str]]]  # 024:文档段(source_ref+content)
    has_attachments: NotRequired[bool]  # 036④:本轮含图片附件(直答分流信号)
