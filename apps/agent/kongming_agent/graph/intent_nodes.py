"""三态意图路由(route_intent)与融合问答(fused_answer)。"""

import asyncio
import logging
import threading
from typing import cast

from kongming_agent import deps, numeric
from kongming_agent.graph.citations import sanitize_citations, surviving_markers
from kongming_agent.graph.dialogue import CHITCHAT_SEMANTIC_REPLY
from kongming_agent.graph.doc_prompt import FUSED_ANSWER_SYSTEM, INTENT_ROUTE_SYSTEM
from kongming_agent.graph.nodes import fallback_summary, parse_summary_json
from kongming_agent.graph.state import (
    FALLBACK_CHITCHAT,
    FALLBACK_CLARIFY,
    INTENT_CHITCHAT,
    VALID_INTENTS,
    AgentState,
    ExecutionResult,
    fused_takes_over,
)
from kongming_agent.graph.streams import emit_event
from kongming_agent.llm import LlmError
from kongming_agent.settings import Settings

log = logging.getLogger(__name__)

# 可观测计数(复语直通 snapshot_stats 同款形态;零新采集通道):
# 路由回退(LLM 失败/超时/低置信)、语义闲聊命中与融合兜底(生成失败回退纯数据答)
_intent_lock = threading.Lock()
_intent_stats = {
    "intent_route_fallback_total": 0,
    "fused_answer_fallback_total": 0,
    "chitchat_semantic_total": 0,
}


def snapshot_intent_route_stats() -> dict[str, int]:
    """回退/兜底计数快照(可观测:prompt 调优与阈值调整依据)。"""
    with _intent_lock:
        return dict(_intent_stats)


def parse_intent_route(raw: str) -> tuple[str, float, str, bool, str] | None:
    """路由 LLM 输出解析(纯函数):JSON {intent, ambiguous, clarify_question,
    confidence, reason}。intent 越域 / 置信缺失或不可解析 / 置信越界 [0,1]
    → None(调用方回退词表);ambiguous 无澄清问句时收 false(fail-open,
    不可执行的判定不放行)。"""
    parsed = parse_summary_json(raw)
    intent = str(parsed.get("intent", "")).strip().lower()
    if intent not in VALID_INTENTS:
        return None
    try:
        confidence = float(cast("float", parsed.get("confidence")))
    except (TypeError, ValueError):
        return None
    if not 0.0 <= confidence <= 1.0:
        return None
    ambiguous = parsed.get("ambiguous") is True
    clarify_raw = parsed.get("clarify_question")
    clarify = clarify_raw.strip() if isinstance(clarify_raw, str) else ""
    if ambiguous and not clarify:
        ambiguous = False
    return intent, confidence, str(parsed.get("reason", "")).strip(), ambiguous, clarify


async def route_intent(state: AgentState) -> dict[str, object]:
    """检索前判定问句意图；失败或置信度不足时回退到规则词表。

    回退时不写 intent，沿用原有分流规则；意图误判只影响答法，不触发数据操作。
    同一次调用收口两个兜底出口:闲聊(词表漏网的零取数意图输入)与澄清
    (高置信歧义,上一轮已澄清则硬门防循环)——两者均早退,零检索零生成零执行。
    """
    question = state.get("resolved_question")
    if not question:
        question = state["question"]  # pyright: ignore[reportTypedDictNotRequiredAccess]
    settings = Settings()
    parsed: tuple[str, float, str, bool, str] | None = None
    try:
        raw = await asyncio.wait_for(
            deps.get_llm().chat(
                INTENT_ROUTE_SYSTEM,
                f"用户问题:{question}\n\n输出 JSON:",
                temperature=0.1,
                max_tokens=200,
                task="INTENT_ROUTE",  # 任务路由:小模型档(multi-model-routing,缺省主模型)
                trace_id=state.get("trace_id"),
                tenant_key=state.get("tenant_key"),
            ),
            timeout=settings.intent_route_timeout_sec,
        )
        parsed = parse_intent_route(raw)
    except (LlmError, TimeoutError):
        parsed = None
    if parsed is None or parsed[1] < settings.intent_route_min_confidence:
        with _intent_lock:
            _intent_stats["intent_route_fallback_total"] += 1
        emit_event(
            "trace",
            {
                "node": "route_intent",
                "fields": {
                    "kind": "intent_route",
                    "source": "fallback",
                    "reason": "llm_low_confidence" if parsed is not None else "llm_failed",
                },
            },
        )
        return {}
    intent, confidence, reason, ambiguous, clarify = parsed
    # 闲聊兜底(spec「闲聊语义兜底判定」):判定口径=完全无取数意图;
    # 冲突时闲聊优先于澄清(无取数意图的输入无从澄清口径)
    if intent == INTENT_CHITCHAT:
        with _intent_lock:
            _intent_stats["chitchat_semantic_total"] += 1
        emit_event(
            "trace",
            {
                "node": "route_intent",
                "fields": {
                    "kind": "intent_route",
                    "source": "llm",
                    "intent": INTENT_CHITCHAT,
                    "confidence": confidence,
                },
            },
        )
        return {
            "end_reason": "no_data",
            "error": CHITCHAT_SEMANTIC_REPLY,
            "fallback_type": FALLBACK_CHITCHAT,
        }
    # 澄清早退(spec「歧义问句澄清引导」):高置信歧义且上一轮非澄清轮
    # (防循环硬门,确定性规则不依赖模型自觉);fail-open 路径在上方回退分支
    if ambiguous and state.get("last_fallback") != FALLBACK_CLARIFY:
        emit_event(
            "trace",
            {
                "node": "route_intent",
                "fields": {
                    "kind": "intent_route",
                    "source": "llm",
                    "decision": "clarify",
                    "confidence": confidence,
                },
            },
        )
        return {
            "end_reason": "no_data",
            "error": clarify,
            "fallback_type": FALLBACK_CLARIFY,
        }
    emit_event(
        "trace",
        {
            "node": "route_intent",
            "fields": {
                "kind": "intent_route",
                "source": "llm",
                "intent": intent,
                "confidence": confidence,
                "reason": reason,
            },
        },
    )
    return {
        "intent": intent,
        "intent_source": "llm",
        "intent_confidence": confidence,
        "intent_reason": reason,
    }


def _note_fused_fallback(trace_id: str | None, reason: str) -> None:
    """融合兜底入账 + trace 下发(可观测;回退纯数据答是 spec 契约非故障)。"""
    with _intent_lock:
        _intent_stats["fused_answer_fallback_total"] += 1
    emit_event(
        "trace",
        {"node": "fused_answer", "fields": {"kind": "fused_answer_fallback", "reason": reason}},
    )
    log.info("fused_answer 回退纯数据答 trace_id=%s reason=%s", trace_id, reason)


async def fused_answer(state: AgentState) -> dict[str, object]:
    """融合问答节点(summarize 后条件进入):SQL 通道 SUCCESS 且文档命中
    且结果非空时联合作答——正文=数据结论(接地结果面,数值纪律同审计)
    + 口径解释(【引用键】,净化与 sources 投影同机制,合法键=本节点下发
    父块集);融合正文即洞察位正文(insights 清空,design Open Question 裁定)。
    与 doc_answer 的 LlmError 上抛语义刻意不同:此处数据答已就绪,spec 明文
    「生成失败回退纯数据答正文(结果面不丢)」——上抛反而丢掉完整结果面。"""
    execution = state.get("execution")
    doc_hints = state.get("doc_hints") or []
    # 前置复核(_after_summarize 已拦,此处兜底防直调/拓扑改动漏网;判据
    # 单源=state.fused_takes_over,与条件边/流式抑制三处共用)。execution
    # 局部再收窄:谓词对类型检查不透明,后续 data_preview/审计直接解引用
    if not fused_takes_over(state) or execution is None:
        return {}
    question = state.get("resolved_question")
    if not question:
        question = state["question"]  # pyright: ignore[reportTypedDictNotRequiredAccess]
    stats = state.get("stats") or {}
    parts = _fused_doc_parts(doc_hints)
    user = _fused_user(question, execution, stats, parts)
    try:
        answer = await deps.get_llm().chat(
            FUSED_ANSWER_SYSTEM,
            user,
            temperature=0.1,
            task="FUSED_ANSWER",
            trace_id=state.get("trace_id"),
            tenant_key=state.get("tenant_key"),
        )
    except LlmError:
        _note_fused_fallback(state.get("trace_id"), "llm_failed")
        return {}
    summary = answer.strip()
    if not summary:
        # 空稿同兜底:数据答已在,不伪造融合内容
        _note_fused_fallback(state.get("trace_id"), "empty_answer")
        return {}
    summary, by_key = _sanitize_fused(summary, doc_hints)
    final = _audit_fused(summary, execution, stats, parts, state.get("compute_values") or [])
    return {
        "summary": final,
        "insights": [],
        "sources": _project_fused_sources(final, by_key),
    }


def _fused_doc_parts(doc_hints: list[dict[str, str]]) -> list[str]:
    """注入段:标注=引用键(与净化合法集同源;缺失回退 source_ref 作块头说明)。"""
    return [
        f"来源 {h.get('citation_key') or h['source_ref']}:\n{h['content'][:1500]}"
        for h in doc_hints[:5]
    ]


def _fused_user(
    question: str, execution: ExecutionResult, stats: dict[str, object], parts: list[str]
) -> str:
    """融合问答 user 文本(问题 + 数据结果 + 文档内容)。"""
    data_preview = (
        f"列:{execution.columns}\n总行数:{len(execution.rows)}\n"
        f"本地统计:{stats}\n前5行:{execution.rows[:5]}"
    )
    return (
        f"问题:{question}\n\n数据结果:\n{data_preview}\n\n文档内容:\n"
        + "\n\n".join(parts)
        + "\n\n回答:"
    )


def _sanitize_fused(
    summary: str, doc_hints: list[dict[str, str]]
) -> tuple[str, dict[str, dict[str, str]]]:
    """确定性净化(零模型):合法键集=本节点下发父块集。"""
    legal_keys = [str(h.get("citation_key") or "") for h in doc_hints]
    legal_keys = [k for k in legal_keys if k]
    sanitized = sanitize_citations(summary, legal_keys)
    by_key = {str(h.get("citation_key") or ""): h for h in doc_hints}
    if sanitized.stripped:
        emit_event(
            "trace",
            {
                "node": "fused_answer",
                "fields": {
                    "kind": "citation_sanitize",
                    "stripped": sanitized.stripped,
                    "surviving": list(sanitized.surviving_keys),
                },
            },
        )
    return sanitized.text, by_key


def _audit_fused(
    summary: str,
    execution: ExecutionResult,
    stats: dict[str, object],
    parts: list[str],
    compute_values: list[float],
) -> str:
    """数值纪律收口:白名单=结果面∪stats∪本轮计算值∪下发文档内容数字。"""
    doc_numbers = [t.value for part in parts for t in numeric.extract_numbers(part)]
    audited = numeric.audit_summarize_output(
        summary,
        [],
        columns=execution.columns,
        rows=execution.rows,
        stats=stats,
        compute_values=[*compute_values, *doc_numbers],
        structural=[float(len(execution.rows)), float(numeric.PREVIEW_ROWS)],
    )
    return audited.summary or fallback_summary(stats)


def _project_fused_sources(
    final: str, by_key: dict[str, dict[str, str]]
) -> list[dict[str, object]]:
    """sources 投影以过审终稿为准(全剥回退纯数据答时自然为空)。"""
    sources: list[dict[str, object]] = []
    for key in surviving_markers(final):
        hit = by_key.get(key)
        if hit is None:
            continue
        sources.append(
            {
                "citation_key": key,
                "source_ref": str(hit.get("source_ref") or ""),
                "doc_name": str(hit.get("source_ref") or "").split(" §")[0],
            }
        )
    return sources
