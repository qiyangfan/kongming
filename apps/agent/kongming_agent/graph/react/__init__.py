"""ReAct 内核(tool-calling):LLM 自主选择并调用工具的通用 Agent 循环。"""

from kongming_agent.graph.react.build import get_react_graph, react_recursion_limit

__all__ = ["get_react_graph", "react_recursion_limit"]
