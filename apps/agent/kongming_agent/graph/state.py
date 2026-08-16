"""LangGraph 编排骨架(继承前系统已验证的 8 节点设计)。

节点链:understand → retrieve → generate → validate → execute → selfheal → summarize
- execute:回调控制面 /internal/sql/execute(不是直连 DB)
- selfheal:错误分类重试;权限类拒绝不重试
"""

from typing import NotRequired, TypedDict

from pydantic import BaseModel


class RetrievedContext(BaseModel):
    table_docs: list[dict[str, object]] = []
    sample_queries: list[dict[str, object]] = []
    history_queries: list[dict[str, object]] = []


class ExecutionResult(BaseModel):
    verdict_passed: bool
    verdict_reason: str | None = None
    columns: list[str] = []
    rows: list[list[object]] = []
    duration_ms: int = 0


class AgentState(TypedDict, total=False):
    trace_id: str
    tenant_id: str
    datasource_key: str
    user_id: NotRequired[str]
    question: str
    history: list[dict[str, object]]  # 多轮上下文(指代消解用)
    resolved_question: NotRequired[str]  # understand 产物
    retrieved: NotRequired[RetrievedContext]  # retrieve 产物
    generated_sql: NotRequired[str]  # generate 产物
    validation: NotRequired[dict[str, object]]  # validate 产物
    execution: NotRequired[ExecutionResult]  # execute 产物(回调)
    retry_count: NotRequired[int]
    summary: NotRequired[str]
    insights: NotRequired[list[str]]
    end_reason: NotRequired[str]  # fail 分支原因
