"""summarize 模型上下文装配:结果接地段 + 表说明段 + 槽位清单 + 人设段。

只处理提示词文本拼装,不读库不调模型;表说明段抽取规则与召回形态同口径。
"""

import re
from collections.abc import Sequence

from kongming_agent import sqlguard
from kongming_agent.graph import slots
from kongming_agent.graph.state import AgentState

# 召回形态表头行 `schema.table(别名)(列…) -- 表说明`;示例行与内省回退形态不注入
_HINT_HEADER_RE = re.compile(r"^[A-Za-z_]\w*\.[A-Za-z_]\w*\(")


def table_hint_lines(schema_hint: str) -> str:
    """取 schema_hint 的召回形态表头行(含业务称谓别名与表说明)。"""
    return "\n".join(ln for ln in schema_hint.splitlines() if _HINT_HEADER_RE.match(ln))


def scalar_summary(
    state: AgentState, question: str, columns: list[str], rows: list[list[object]]
) -> str | None:
    """单行单列数值的确定性作答;非该形态返回 None。"""
    if len(rows) != 1 or len(columns) != 1 or len(rows[0]) != 1:
        return None
    cell = rows[0][0]
    if not isinstance(cell, (int, float)) or isinstance(cell, bool):
        return None
    text = slots.scalar_answer(
        column=columns[0],
        value=cell,
        question=question,
        unit_question=str(state.get("question") or ""),
        aggregate=sqlguard.scalar_aggregate_kind(state.get("generated_sql") or ""),
    )
    slots.emit_contract_event(node="summarize", result=slots.ContractResult(text), tier="scalar")
    return text


def summarize_context(
    *,
    question: str,
    columns: Sequence[str],
    rows: Sequence[Sequence[object]],
    stats: dict[str, object],
    schema_hint: str,
    registry: slots.SlotRegistry,
    skill_persona: str,
    original_question: str = "",
) -> str:
    """summarize 的模型上下文:结果接地 + 表说明 + 槽位清单 + 人设。"""
    data_preview = f"列：{columns}\n总行数：{len(rows)}\n本地统计：{stats}\n前5行：{rows[:5]}"
    original = original_question.strip() or question
    intro = f"用户原始问题：{original}\n"
    if question.strip() and question.strip() != original:
        intro += f"本轮完整意图（内部改写，仅供理解，回答措辞以用户原始问题为准）：{question}\n"
    context = f"{intro}结果：\n{data_preview}"
    table_hint = table_hint_lines(schema_hint)
    if table_hint:
        context += f"\n所涉表(表名(业务称谓别名) 列 -- 表说明)：\n{table_hint}"
    context += f"\n\n{registry.prompt_block()}"
    if skill_persona.strip():
        context += f"\n回答风格与禁令(仅风格,不得臆测编表或放宽只读约束)：\n{skill_persona.strip()}"
    return context + "\n\n生成 JSON："
