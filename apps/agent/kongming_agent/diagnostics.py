"""终态诊断字段提取(同步与流式共用,不依赖 HTTP 模型)。"""

from dataclasses import dataclass
from typing import cast

from kongming_agent.graph.state import ExecutionResult, ValidationResult


@dataclass(frozen=True)
class Diagnostics:
    """终态诊断字段(改写问句/语义命中/文档来源/兜底分类)。"""

    resolved: str | None
    metric_hits: list[dict[str, str | None]]
    sources: list[dict[str, str]]
    fallback: str | None


def flow_verdict(final: dict[str, object]) -> str | None:
    """终态判定:no_data 优先,其次执行/预检结论。"""
    execution = final.get("execution")
    validation = final.get("validation")
    if final.get("end_reason") == "no_data":
        # 知识未覆盖如实说明(短路,未执行)——判定最前:自愈轮短路时
        # state 残留上轮 execution(ERROR),不得遮蔽最终形态
        return "NO_DATA"
    if isinstance(execution, ExecutionResult):
        return execution.verdict
    if isinstance(validation, ValidationResult) and validation.permission_block:
        return "BLOCKED"  # 本地预检拦下的写操作,未达执行路由
    return None


def diagnostics(final: dict[str, object]) -> Diagnostics:
    """提取终态诊断字段(改写问句只在实际发生时保留)。"""
    # resolved_question 仅多轮改写实际发生且有 history 时非空
    # (understand 直通路径 state 该键即原问句,单轮/直通不背诊断信号)
    resolved_q = final.get("resolved_question")
    resolved = resolved_q if isinstance(resolved_q, str) and resolved_q else None
    question_raw = final.get("question")
    if resolved is not None and isinstance(question_raw, str) and resolved == question_raw:
        resolved = None  # 未发生改写(恒等)——诊断信号只记实际改写
    # 语义层命中随请求状态透传，不包含凭证；无命中时为空。
    hits_raw = final.get("metric_hits")
    metric_hits = (
        cast("list[dict[str, str | None]]", hits_raw) if isinstance(hits_raw, list) else []
    )
    # 文档道来源(净化后正文的存活标记投影;空=SQL 道/无命中)
    sources_raw = final.get("sources")
    sources = cast("list[dict[str, str]]", sources_raw) if isinstance(sources_raw, list) else []
    # 兜底轮分类(仅兜底早退轮非空;诊断字段,api 落库不进用户响应)
    fallback_raw = final.get("fallback_type")
    fallback = fallback_raw if isinstance(fallback_raw, str) and fallback_raw else None
    return Diagnostics(
        resolved=resolved, metric_hits=metric_hits, sources=sources, fallback=fallback
    )
