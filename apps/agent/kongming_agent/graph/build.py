"""LangGraph 图组装:understand → retrieve → generate → validate → execute → selfheal → summarize。

节点实现见 nodes.py;图的拓扑即产品架构。
路由铁律:任何重试必须经过 selfheal(计数),禁止绕行直达 generate,
否则 retry_count 永不增长,图将依赖 recursion_limit 兜底(曾经的缺陷)。
权限类(执行 BLOCKED / 预检非只读)直接终态,不进 selfheal、不消耗
重试配额(nl2sql-agent spec「selfheal 错误分类」)。
"""

from functools import lru_cache

from langgraph.graph import END, START, StateGraph

from kongming_agent.graph import nodes
from kongming_agent.graph.nodes import MAX_RETRY  # re-export(测试与调用方统一入口)
from kongming_agent.graph.state import AgentState
from kongming_agent.graph.streams import stage_wrapper

__all__ = ["MAX_RETRY", "build", "get_graph"]


def _after_generate(state: AgentState) -> str:
    """043:generate 输出哨兵短路产物(end_reason=no_data)直达失败终态——
    零执行零重试;正常产物照常进本地预检。"""
    if state.get("end_reason"):
        return "fail"
    return "validate"


def _after_validate(state: AgentState) -> str:
    validation = state.get("validation")
    if validation is None or validation.passed:
        return "execute"
    if validation.permission_block:
        return "fail"  # 权限类不重试(省配额)
    return "selfheal"


def _after_execute(state: AgentState) -> str:
    execution = state.get("execution")
    if execution is not None and execution.verdict == "PASS":
        return "summarize"
    if execution is not None and execution.verdict == "BLOCKED":
        return "fail"  # 权限/策略拦截:不重试
    return "selfheal"  # ERROR 一律进 selfheal 分类计数,不直达 generate


def _after_selfheal(state: AgentState) -> str:
    if state.get("end_reason"):
        return "fail"  # 重试预算耗尽(selfheal 已判定)
    return "retry"


@lru_cache
def get_graph():  # type: ignore[no-untyped-def]
    """编译产物单例(无状态,可跨请求共享)。"""
    g = StateGraph(AgentState)
    # 017 流式:节点挂 stage_wrapper(事件发射点;非流式调用静默跳过零行为变化,
    # nodes.py 业务实现零改动——prompt 资产纪律)
    # langgraph add_node/compile 签名自带库侧 Unknown(泛型未闭合),访问点豁免
    g.add_node(  # pyright: ignore[reportUnknownMemberType]  # langgraph 库侧签名含 Unknown
        "understand", stage_wrapper("understand", nodes.understand)
    )
    g.add_node(  # pyright: ignore[reportUnknownMemberType]  # langgraph 库侧签名含 Unknown
        "retrieve", stage_wrapper("retrieve", nodes.retrieve)
    )
    g.add_node(  # pyright: ignore[reportUnknownMemberType]  # langgraph 库侧签名含 Unknown
        "generate", stage_wrapper("generate", nodes.generate)
    )
    g.add_node(  # pyright: ignore[reportUnknownMemberType]  # langgraph 库侧签名含 Unknown
        "validate", stage_wrapper("validate", nodes.validate)
    )
    g.add_node(  # pyright: ignore[reportUnknownMemberType]  # langgraph 库侧签名含 Unknown
        "execute", stage_wrapper("execute", nodes.execute)
    )
    g.add_node(  # pyright: ignore[reportUnknownMemberType]  # langgraph 库侧签名含 Unknown
        "selfheal", stage_wrapper("selfheal", nodes.selfheal)
    )
    g.add_node(  # pyright: ignore[reportUnknownMemberType]  # langgraph 库侧签名含 Unknown
        "summarize", stage_wrapper("summarize", nodes.summarize)
    )

    # 024
    g.add_node(  # pyright: ignore[reportUnknownMemberType]  # langgraph 库侧签名含 Unknown
        "doc_answer", stage_wrapper("doc_answer", nodes.doc_answer)
    )

    g.add_edge(START, "understand")
    g.add_edge("understand", "retrieve")
    # 024:知识分流(知识意图+文档命中即分流;否则既有 generate;spec 修订后口径)
    g.add_conditional_edges(
        "retrieve",
        nodes.route_after_retrieve,
        {"generate": "generate", "doc_answer": "doc_answer"},
    )
    g.add_edge("doc_answer", END)
    # 043:generate→validate 改条件边(哨兵短路直达 fail;见 _after_generate)
    g.add_conditional_edges(
        "generate", _after_generate, {"validate": "validate", "fail": END}
    )
    g.add_conditional_edges(
        "validate", _after_validate, {"execute": "execute", "selfheal": "selfheal", "fail": END}
    )
    g.add_conditional_edges(
        "execute", _after_execute, {"summarize": "summarize", "selfheal": "selfheal", "fail": END}
    )
    g.add_conditional_edges(
        "selfheal",
        _after_selfheal,
        {"retry": "generate", "fail": END},
    )
    g.add_edge("summarize", END)
    # langgraph 库侧签名含 Unknown,访问点豁免(运行时即编译产物)
    return g.compile()  # pyright: ignore[reportUnknownMemberType]


def build():  # type: ignore[no-untyped-def]
    """兼容旧调用口(测试用);等价于 get_graph()。"""
    return get_graph()
