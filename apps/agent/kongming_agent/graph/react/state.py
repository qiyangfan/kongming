"""ReAct 内核状态(019):与 8 节点图 AgentState 独立的并行状态面。

messages 为 OpenAI 对话累积(user/assistant/tool),pending_tool_calls 为
react_llm 产出待 tool_dispatch 消费的调用批;last_execution/last_sql 记
最后一次 execute_sql 的成功结果,供 finalize 构造与同步端点同构的终态。
"""

from typing import NotRequired, TypedDict

from kongming_agent.graph.state import ExecutionResult
from kongming_agent.llm import LlmToolCall


class ReactState(TypedDict, total=False):
    trace_id: str
    tenant_key: str
    datasource_key: str
    user_id: NotRequired[str | None]
    question: str  # 原始问句
    history: list[str]  # 多轮上下文:user 问句列表,旧→新
    tools: list[dict[str, object]]  # api 注入的授权工具 schema(唯一真理源=api)
    memory_hint: NotRequired[str]  # 022:召回的用户已知信息段(控制面注入)
    bot_system_prompt: NotRequired[str]  # 025:Bot 身份段(控制面注入)
    model_override: NotRequired[str]  # 025:Bot 模型覆盖(注册表名)
    channel: NotRequired[str]  # 025:渠道标记(工具审计平铺)
    messages: list[dict[str, object]]  # OpenAI 对话累积
    pending_tool_calls: list[LlmToolCall]  # 待执行的 LLM 工具调用批
    rounds: int  # 已执行的工具轮次
    last_sql: NotRequired[str]  # 最后一次 execute_sql 调用的 SQL
    last_execution: NotRequired[ExecutionResult]  # 最后一次 execute_sql 执行结果
    executed_sigs: NotRequired[list[str]]  # 重复熔断:已成功调用的 工具|参数 签名集
    final_answer: NotRequired[str]  # LLM 最终文本答案
    error: NotRequired[str]  # 轮次上限触顶等终态错误
    compute_values: NotRequired[list[float]]  # 057:本轮计算工具返回值(入白名单)
    numeric_whitelist: NotRequired[list[float]]  # 057:本轮数值白名单(compute 入参校验)
    todos: NotRequired[list[dict[str, str]]]  # 058:多步目标账本(仅 Agent 内部)
    # finalize 终态键(与 8 节点图 AgentState 同名字段同构,_build_query_out 直读)
    execution: NotRequired[ExecutionResult]
    summary: NotRequired[str]
    stats: NotRequired[dict[str, object]]
    retry_count: NotRequired[int]
    generated_sql: NotRequired[str]
