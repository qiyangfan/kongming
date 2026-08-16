"""LangGraph 图组装:understand → retrieve → generate → validate → execute → selfheal → summarize。

节点实现按能力域逐个接入(先骨架,后 LLM 接线);图的拓扑即产品架构。
路由铁律:任何重试必须经过 selfheal(计数与分类),禁止绕行直达 generate,
否则 retry_count 永不增长,图将依赖 recursion_limit 兜底(曾经的缺陷)。
"""

from langgraph.graph import END, START, StateGraph

from kongming_agent.graph.state import AgentState

MAX_RETRY = 2


def understand(state: AgentState) -> AgentState:
    """意图理解 + 多轮指代消解。TODO: LLM 接线。"""
    return {**state, "resolved_question": state["question"]}


def retrieve(state: AgentState) -> AgentState:
    """RAG 召回:回调控制面 /internal/rag/retrieve(向量 + few-shot + 历史)。TODO。"""
    return state


def generate(state: AgentState) -> AgentState:
    """SQL 生成。TODO: LLM 接线。"""
    return {**state, "generated_sql": ""}


def validate(state: AgentState) -> AgentState:
    """语法预检 + 权限预判。TODO。"""
    return state


def execute(state: AgentState) -> AgentState:
    """回调控制面 /internal/sql/execute——唯一的执行路径(ADR-002)。TODO。"""
    return state


def selfheal(state: AgentState) -> AgentState:
    """错误反馈重生成。TODO: LLM 接线。计数在此递增(路由铁律)。"""
    return {**state, "retry_count": state.get("retry_count", 0) + 1}


def summarize(state: AgentState) -> AgentState:
    """结构化洞察 {summary, insights, stats}。TODO: LLM 接线。"""
    return {**state, "summary": "", "insights": []}


def _is_permission_block(state: AgentState) -> bool:
    exe = state.get("execution")
    reason = (exe.verdict_reason if exe is not None else "") or ""
    return reason.startswith("BLOCKED")


def _after_validate(state: AgentState) -> str:
    validation = state.get("validation") or {}
    return "execute" if validation.get("passed", True) else "selfheal"


def _after_execute(state: AgentState) -> str:
    exe = state.get("execution")
    if exe is not None and exe.verdict_passed:
        return "summarize"
    return "selfheal"  # 失败一律进 selfheal 分类计数,不直达 generate


def _after_selfheal(state: AgentState) -> str:
    if _is_permission_block(state):
        return "fail"  # 权限类不重试(省配额,nl2sql-agent spec)
    if state.get("retry_count", 0) >= MAX_RETRY:
        return "fail"
    return "retry"


def build():  # type: ignore[no-untyped-def]
    g = StateGraph(AgentState)
    g.add_node("understand", understand)
    g.add_node("retrieve", retrieve)
    g.add_node("generate", generate)
    g.add_node("validate", validate)
    g.add_node("execute", execute)
    g.add_node("selfheal", selfheal)
    g.add_node("summarize", summarize)

    g.add_edge(START, "understand")
    g.add_edge("understand", "retrieve")
    g.add_edge("retrieve", "generate")
    g.add_edge("generate", "validate")
    g.add_conditional_edges(
        "validate", _after_validate, {"execute": "execute", "selfheal": "selfheal"}
    )
    g.add_conditional_edges(
        "execute", _after_execute, {"summarize": "summarize", "selfheal": "selfheal"}
    )
    g.add_conditional_edges(
        "selfheal",
        _after_selfheal,
        {"retry": "generate", "fail": END},
    )
    g.add_edge("summarize", END)
    return g.compile()
