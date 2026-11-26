"""生成 SQL 的语义预检、澄清与确定性自愈编排。"""

from __future__ import annotations

from kongming_agent import sqlsemantic
from kongming_agent.graph.state import FALLBACK_CLARIFY, AgentState, ValidationResult
from kongming_agent.graph.streams import emit_event
from kongming_agent.schemagraph import SchemaGraph, graph_for_prompt


def clarification_graph(graph: SchemaGraph, schema_hint: str) -> SchemaGraph:
    """时间口径澄清只看本轮召回/桥接图,不让完整图里的无关事件表参与提问。"""
    projected = graph_for_prompt(graph, schema_hint, include_outgoing=False)
    return projected if projected.tables else graph


def _semantic_clarification(
    state: AgentState, question: str, graph: SchemaGraph, verdict: sqlsemantic.SemanticGuardResult
) -> dict[str, object] | None:
    """时间口径歧义优先澄清;上轮已是澄清轮时不重复追问。"""
    if (
        sqlsemantic.CATEGORY_AMOUNT_TIME not in verdict.categories
        or state.get("last_fallback") == FALLBACK_CLARIFY
    ):
        return None
    clarify = sqlsemantic.time_basis_clarification(
        question, clarification_graph(graph, state.get("schema_hint") or "")
    )
    if clarify is None:
        return None
    emit_event(
        "trace",
        {"node": "validate", "fields": {"kind": "clarify", "reason": "time_basis"}},
    )
    return {
        "validation": ValidationResult(passed=False, reason=verdict.render_reason()),
        "end_reason": "no_data",
        "error": clarify,
        "fallback_type": FALLBACK_CLARIFY,
    }


def _semantic_autofix(
    sql: str,
    graph: SchemaGraph,
    question: str,
    verdict: sqlsemantic.SemanticGuardResult,
) -> dict[str, object] | None:
    """结构可确定的违规直接改写,不耗自愈轮次;其余返回 None 交给 selfheal。"""
    if set(verdict.categories) == {sqlsemantic.CATEGORY_FANOUT}:
        fixed = sqlsemantic.repair_event_count_fanout(sql, graph)
        if fixed is not None and sqlsemantic.validate_semantics(fixed, graph, question).passed:
            emit_event(
                "trace",
                {
                    "node": "validate",
                    "fields": {"kind": "semantic_autofix", "categories": verdict.categories},
                },
            )
            return {"generated_sql": fixed, "validation": ValidationResult(passed=True)}
    if set(verdict.categories) == {sqlsemantic.CATEGORY_BRIDGE_JOIN}:
        fixed = sqlsemantic.repair_bridge_join(sql, graph, question)
        if (
            fixed is not None
            and sqlsemantic.validate_semantics(fixed, graph, question).passed
            and sqlsemantic.validate_bridge_joins(fixed, graph, question).passed
        ):
            emit_event(
                "trace",
                {
                    "node": "validate",
                    "fields": {"kind": "semantic_autofix", "categories": verdict.categories},
                },
            )
            return {"generated_sql": fixed, "validation": ValidationResult(passed=True)}
    return None


def semantic_guard(state: AgentState, sql: str, graph: SchemaGraph) -> dict[str, object] | None:
    """跑既有语义规则与完整图桥接预检;违规时返回终态或自愈输入。"""
    question = state.get("resolved_question") or state["question"]  # pyright: ignore[reportTypedDictNotRequiredAccess]
    # 语义预检使用完整图:召回投影只服务提示词,不能拿它做表/列/角色校验,
    # 否则模型写入未召回的真实表时会绕过列校验与扇出/归属规则。
    normal = sqlsemantic.validate_semantics(sql, graph, question)
    bridge = sqlsemantic.validate_bridge_joins(sql, graph, question)
    violations = (*normal.violations, *bridge.violations)
    verdict = sqlsemantic.SemanticGuardResult(passed=not violations, violations=violations)
    emit_event(
        "trace",
        {
            "node": "guard",
            "fields": {
                "kind": "semantic_guard",
                "verdict": "pass" if verdict.passed else "reject",
                "categories": verdict.categories,
            },
        },
    )
    if verdict.passed:
        return None
    clarified = _semantic_clarification(state, question, graph, verdict)
    if clarified is not None:
        return clarified
    fixed = _semantic_autofix(sql, graph, question, verdict)
    if fixed is not None:
        return fixed
    return {"validation": ValidationResult(passed=False, reason=verdict.render_reason())}
