"""ReAct 图组装:react ⇄ tool_dispatch 循环 + finalize 收束。

与既有 8 节点图完全隔离(独立编译产物);stage 事件发射点在 react_stage_wrapper
(节点业务零侵入);finalize 完成发 summary 事件(对齐 query-streaming 契约)。
轮次上限路由(前移):tool_dispatch 后判定——达上限直接 finalize
构造错误终态,不再空转一次必然被拦的 react LLM 调用。
"""

import inspect
from functools import lru_cache
from typing import Any

from langgraph.graph import END, START, StateGraph

from kongming_agent.graph.checkpoints import InternalCheckpointSaver
from kongming_agent.graph.react import nodes
from kongming_agent.graph.react.state import ReactState
from kongming_agent.graph.streams import emit_event
from kongming_agent.settings import Settings


def _after_react(state: ReactState) -> str:
    calls = state.get("pending_tool_calls") or []
    if not calls:
        return "done"
    return "tools"


def _after_dispatch(state: ReactState) -> str:
    """任务通道在途确认优先挂图(挂起先于轮次收口——待确认动作不得被上限
    finalize 吞掉);其余轮次上限:本轮已达上限即收束(LLM 本轮
    仍在调工具=未收敛)。"""
    if state.get("confirm_inflight"):
        return "confirm"
    if state.get("rounds", 0) >= Settings().max_tool_rounds:
        return "done"  # finalize 按「上限触顶」构造错误终态
    return "react"


def react_stage_wrapper(name: str, fn: Any) -> Any:
    """节点 wrapper:stage(running/done)对发 + finalize 发 summary。

    与 streams.stage_wrapper 分离的原因:ReactState 字段面不同(rounds 而非
    retry_count),且无 validate/sql 特判;复用 emit_event 通道。
    """

    async def wrapped(state: ReactState) -> dict[str, object]:
        emit_event(
            "stage",
            {"node": name, "status": "running", "retry_count": state.get("rounds", 0)},
        )
        result = fn(state)
        # finalize 为同步 def(与 validate/selfheal 同款双形态兼容)
        out: dict[str, object] = await result if inspect.isawaitable(result) else result
        emit_event(
            "stage",
            {
                "node": name,
                "status": "done",
                "retry_count": out.get("rounds", state.get("rounds", 0)),
            },
        )
        if name == "finalize":
            summary = out.get("summary")
            if isinstance(summary, str) and summary:
                emit_event("summary", {"delta": summary})
        return out

    return wrapped


@lru_cache
def _compile(checkpointed: bool):  # type: ignore[no-untyped-def]
    """编译产物单例(无状态,可跨请求共享;与 get_graph 并列)。检查点形态
    仅任务通道使用(线程恢复/图内挂起),交互通道裸图。"""
    g = StateGraph(ReactState)
    # langgraph add_node/compile 签名自带库侧 Unknown(泛型未闭合),访问点豁免
    g.add_node(  # pyright: ignore[reportUnknownMemberType]  # langgraph 库侧签名含 Unknown
        "react", react_stage_wrapper("react", nodes.react_llm)
    )
    g.add_node(  # pyright: ignore[reportUnknownMemberType]  # langgraph 库侧签名含 Unknown
        "tool_dispatch", react_stage_wrapper("tool_dispatch", nodes.tool_dispatch)
    )
    g.add_node(  # pyright: ignore[reportUnknownMemberType]  # langgraph 库侧签名含 Unknown
        "finalize", react_stage_wrapper("finalize", nodes.finalize)
    )
    # 图内确认点(仅任务通道可达):tool_dispatch 确认分流 →
    # confirm_gate(interrupt 挂图)→ tool_resolve(决策兑现观察)→ 回 react 循环
    g.add_node(  # pyright: ignore[reportUnknownMemberType]  # langgraph 库侧签名含 Unknown
        "confirm_gate", react_stage_wrapper("confirm_gate", nodes.confirm_gate)
    )
    g.add_node(  # pyright: ignore[reportUnknownMemberType]  # langgraph 库侧签名含 Unknown
        "tool_resolve", react_stage_wrapper("tool_resolve", nodes.tool_resolve)
    )
    g.add_edge(START, "react")
    g.add_conditional_edges("react", _after_react, {"tools": "tool_dispatch", "done": "finalize"})
    g.add_conditional_edges(
        "tool_dispatch",
        _after_dispatch,
        {"react": "react", "done": "finalize", "confirm": "confirm_gate"},
    )
    g.add_edge("confirm_gate", "tool_resolve")
    g.add_edge("tool_resolve", "react")
    g.add_edge("finalize", END)
    # langgraph 库侧签名含 Unknown,访问点豁免(运行时即编译产物)
    return g.compile(  # pyright: ignore[reportUnknownMemberType]
        checkpointer=InternalCheckpointSaver() if checkpointed else None
    )


def get_react_graph(checkpointed: bool = False):  # type: ignore[no-untyped-def]
    """编译产物入口:checkpointed=True 供任务编排(线程恢复/图内挂起)。"""
    return _compile(checkpointed)


def react_recursion_limit() -> int:
    """每轮 2 步(react+dispatch)+finalize+余量;防 max_tool_rounds 调大后
    撞 LangGraph 默认 25 步上限。"""
    return Settings().max_tool_rounds * 2 + 5
