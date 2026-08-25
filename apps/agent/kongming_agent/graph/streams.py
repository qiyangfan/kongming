"""流式事件发射(017 query-streaming):节点 wrapper 在 custom 通道发过程事件。

设计约束(design §3):
- nodes.py/prompts.py 零侵入(prompt 资产纪律,HANDOFF)——事件发射点
  全部在本 wrapper 层,不改节点业务逻辑;
- 非流式调用(invoke/单测直调)下 get_stream_writer 抛 RuntimeError,
  静默跳过 → 同步路径行为零变化;
- 事件粒度=图节点边界,不虚构子步骤。
"""

import inspect
import logging
from collections.abc import Callable, Coroutine
from typing import Any

from langgraph.config import get_stream_writer

from kongming_agent.graph.state import AgentState, ValidationResult

log = logging.getLogger(__name__)

# 节点函数两种形态(understand/retrieve/generate/execute/summarize 为 async,
# validate/selfheal 为同步 def——wrapper 两者兼容,挂载后统一 async 形态;
# 返回签名与原生 async 节点一致以匹配 StateGraph.add_node 重载)
AsyncNodeFn = Callable[[AgentState], Coroutine[Any, Any, dict[str, object]]]
SyncNodeFn = Callable[[AgentState], dict[str, object]]


def emit_event(event: str, data: dict[str, object]) -> None:
    """custom 通道事件发射;非流式上下文静默跳过(上游同款容错)。"""
    try:
        writer = get_stream_writer()
    except RuntimeError:
        return  # invoke/单测直调:无流上下文,零副作用
    writer({"event": event, "data": data})


def stage_wrapper(name: str, fn: AsyncNodeFn | SyncNodeFn) -> Any:
    """节点 wrapper:stage(running/done)对发 + validate 通过发 sql +
    summarize 完成发 summary(单条完整 delta,契约见 query-streaming spec)。

    返回 Any 为刻意类型擦除:mypy 对 add_node 的 Protocol 联合重载,
    直接函数对象可匹配而 Callable 别名标注会触发 Never 推断失败
    (实验实证);wrapper 返回值即原节点形态的 async 函数。
    """

    async def wrapped(state: AgentState) -> dict[str, object]:
        emit_event(
            "stage",
            {
                "node": name,
                "status": "running",
                "retry_count": state.get("retry_count", 0),
            },
        )
        result = fn(state)
        out: dict[str, object] = (
            await result if inspect.isawaitable(result) else result
        )
        # selfheal 返回新 retry_count(本次重试序号);其余节点沿用 state 当前值
        retry = (
            out.get("retry_count")
            if isinstance(out.get("retry_count"), int)
            else state.get("retry_count", 0)
        )
        emit_event("stage", {"node": name, "status": "done", "retry_count": retry})
        if name == "validate":
            validation = out.get("validation")
            if isinstance(validation, ValidationResult) and validation.passed:
                # SQL 提前展示:校验通过即下发,内容与终态落库 SQL 一致
                emit_event("sql", {"sql": state.get("generated_sql") or ""})
        if name in ("summarize", "doc_answer"):  # 024:知识问答节点同样产出终稿
            summary = out.get("summary")
            if isinstance(summary, str) and summary:
                emit_event("summary", {"delta": summary})
        return out

    return wrapped
