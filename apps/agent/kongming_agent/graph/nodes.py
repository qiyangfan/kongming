"""节点实现:LLM 接线(understand/generate/summarize)+ 控制面回调(retrieve/execute)。

prompt 资产见 prompts.py(逐字保值);user 模板的全角标点同样与上游逐字一致。
行为契约见 nl2sql/nl2sql-agent spec。
关键一跳:selfheal 把 validate/execute 的失败原因写入 state.error,
generate 读到 error 即拼「上次生成的 SQL 有误」喂回 LLM(自愈闭环)。
"""

import json
import logging

import sqlglot
from sqlglot import exp

from kongming_agent import deps
from kongming_agent.apiclient import SchemaMeta
from kongming_agent.graph.prompts import (
    SQL_GEN_SYSTEM,
    SUMMARIZE_SYSTEM,
    UNDERSTAND_MULTI_SYSTEM,
    UNDERSTAND_SINGLE_SYSTEM,
)
from kongming_agent.graph.state import AgentState, ExecutionResult, ValidationResult
from kongming_agent.llm import LlmError

log = logging.getLogger(__name__)

MAX_HISTORY = 5  # 多轮上下文截断(nl2sql spec:平衡 token 与连贯性)

MAX_RETRY = 2  # 重试上限(初始尝试不计):nl2sql spec「重试次数 < 上限(默认 2)」

# 与 api 防火墙只读根一致(sqlfw/firewall.py _READONLY_ROOTS)
_READONLY_ROOTS = (exp.Select, exp.Union, exp.Subquery)

_EMPTY_HINT = "（无法获取表结构，请基于问题尽力生成）"


# ── 节点1:问题理解(多轮指代消解/单轮改写)──
async def understand(state: AgentState) -> dict[str, object]:
    question = state["question"]
    history = state.get("history", [])[-MAX_HISTORY:]
    if history:
        system = UNDERSTAND_MULTI_SYSTEM
        history_text = "\n".join(f"第{i + 1}轮：{q}" for i, q in enumerate(history))
        prompt = (
            f"历史问题：\n{history_text}\n\n当前问题：{question}" "\n\n补全后的完整问题："
        )
    else:
        system = UNDERSTAND_SINGLE_SYSTEM
        prompt = f"用户问题：{question}\n请输出改写后的清晰问题（一行）。"
    rewritten = await deps.get_llm().chat(system, prompt, temperature=0.1)
    # LLM 返回空时兜底原问句(不中断链路)
    return {"resolved_question": rewritten.strip() or question}


def format_schema_hint(meta: SchemaMeta) -> str:
    """内省结果 → 提示文本(纯函数,可测);截断标志显式透传。"""
    lines: list[str] = []
    for t in meta.tables:
        header = f"表 {t.schema}.{t.name}"
        if t.comment:
            header += f"：{t.comment}"
        lines.append(header)
        for c in t.columns:
            col = f"  - {c.name} {c.data_type}"
            if c.comment:
                col += f"（{c.comment}）"
            lines.append(col)
    if meta.truncated:
        lines.append(f"（表数量超上限，仅列前 {len(meta.tables)} 张）")
    return "\n".join(lines)


# ── 节点2:schema 提示获取(009:RAG 召回优先,零命中/失败回退实时内省兜底)──
async def retrieve(state: AgentState) -> dict[str, object]:
    datasource_key = state.get("datasource_key", "default")
    # 召回用改写问句(指代消解后语义完整;多轮裸指代「这些影片」召回会漏)
    question = state.get("resolved_question") or state["question"]
    ctx = await deps.get_api().rag_retrieve(
        tenant_key=state["tenant_key"],
        datasource_key=datasource_key,
        question=question,
    )
    # 历史 SQL 独立于表说明命中(R2-S1):「知识未抓取但 query_history 已有成功
    # 记录」的真实形态下,schema 空回退内省、history 照常作 few-shot,不整体丢弃。
    history_sql = ctx.history_sql if ctx is not None else ""
    if ctx is not None and ctx.schema_hint.strip():
        return {"schema_hint": ctx.schema_hint, "history_sql": history_sql}
    # 回退:005 过渡形态(实时内省)——新租户/未抓取知识/召回服务失败零质量回归。
    meta = await deps.get_api().fetch_schema(
        tenant_key=state["tenant_key"],
        datasource_key=datasource_key,
    )
    if meta is None:
        # 内省失败降级空提示:不误导、不硬编码全表(spec 明文)
        return {"schema_hint": "", "history_sql": history_sql}
    return {"schema_hint": format_schema_hint(meta), "history_sql": history_sql}


def clean_sql(raw: str) -> str:
    """剥 LLM 输出的 markdown 标记(```sql ... ```)。"""
    sql = raw.strip().strip("`").strip()
    if sql.lower().startswith("sql\n"):
        sql = sql[4:]
    return sql.strip()


# ── 节点3:SQL 生成 ──
async def generate(state: AgentState) -> dict[str, object]:
    question = state.get("resolved_question") or state["question"]
    schema_hint = state.get("schema_hint") or ""
    if not schema_hint.strip():
        schema_hint = _EMPTY_HINT
    # 历史成功 SQL 段(009):召回命中时拼在表结构之后——人类验证过的 SQL 置信度
    # 高于表说明内嵌示例(强 few-shot);系统模板资产零改动,只动 user 拼接。
    history_sql = (state.get("history_sql") or "").strip()
    history_part = f"\n\n参考历史成功 SQL：\n{history_sql}" if history_sql else ""
    extra = ""
    error = state.get("error") or ""
    if error:
        # 自愈:把上次错误喂回 LLM
        extra = f"\n\n上次生成的 SQL 有误：{error}\n请修正。"
    user = (
        f"问题：{question}\n\n可用表结构：\n{schema_hint}{history_part}{extra}\n\n生成 SQL："
    )
    sql = await deps.get_llm().chat(SQL_GEN_SYSTEM, user, temperature=0.1)
    # 生成成功即清掉上轮错误(错误已消费)
    return {"generated_sql": clean_sql(sql), "error": ""}


# ── 节点4:本地预检(轻量四规则;权威校验在 api 防火墙,不复刻)──
def validate(state: AgentState) -> dict[str, object]:
    sql = state.get("generated_sql") or ""
    result: ValidationResult
    if not sql.strip():
        result = ValidationResult(passed=False, reason="EMPTY_SQL")
    else:
        try:
            statements = [st for st in sqlglot.parse(sql, dialect="postgres") if st is not None]
        except sqlglot.errors.SqlglotError as e:
            # ParseError 与 TokenError(未闭合引号等)同族,均为可重试的语法类失败
            result = ValidationResult(passed=False, reason=f"PARSE_ERROR: {e}")
        else:
            if not statements:
                result = ValidationResult(passed=False, reason="EMPTY_SQL")
            elif len(statements) > 1:
                # LLM 输出形态问题,值得 selfheal 重试
                result = ValidationResult(passed=False, reason="MULTI_STATEMENT")
            elif not isinstance(statements[0], _READONLY_ROOTS):
                # 写操作/DDL:权限类,prompt 已明令禁止,重试只会再犯——直接终态
                result = ValidationResult(
                    passed=False,
                    permission_block=True,
                    reason=f"BLOCKED_ROOT: {type(statements[0]).__name__}",
                )
            else:
                result = ValidationResult(passed=True)
    out: dict[str, object] = {"validation": result}
    if not result.passed and result.permission_block:
        out["end_reason"] = "blocked"
        out["error"] = result.reason
    return out


# ── 节点5:执行(回调控制面 /internal/sql/execute——唯一执行路径,ADR-002)──
async def execute(state: AgentState) -> dict[str, object]:
    outcome = await deps.get_api().sql_execute(
        trace_id=state["trace_id"],
        tenant_key=state["tenant_key"],
        sql_text=state.get("generated_sql") or "",
        user_id=state.get("user_id"),
        question=state["question"],  # 审计记原始问句(用户真实输入)
        datasource_key=state.get("datasource_key", "default"),
    )
    execution = ExecutionResult(
        verdict=outcome.verdict,
        reason=outcome.reason,
        columns=outcome.columns,
        rows=outcome.rows,
        duration_ms=outcome.duration_ms,
    )
    out: dict[str, object] = {"execution": execution}
    if outcome.verdict == "BLOCKED":
        # 权限/策略类拦截:直接终态不重试(省 LLM 配额,nl2sql-agent spec)
        out["end_reason"] = "blocked"
        out["error"] = outcome.reason or "BLOCKED"
    return out


# ── 节点6:自愈(计数 + 失败原因写 error 喂回 generate)──
def selfheal(state: AgentState) -> dict[str, object]:
    reason = state.get("error") or ""
    validation = state.get("validation")
    execution = state.get("execution")
    if validation is not None and not validation.passed:
        # validate 拦的是本轮新 SQL,优先于上一轮残留的 execution 结果
        reason = f"SQL 预检未过：{validation.reason}"
    elif execution is not None and execution.verdict != "PASS":
        reason = execution.reason or f"执行失败({execution.verdict})"
    retry_count = state.get("retry_count", 0)
    if retry_count >= MAX_RETRY:
        # 预算耗尽:不增计数(计数=已实际发起的重试次数),标终态由路由收束
        return {"error": reason, "end_reason": "retry_exhausted"}
    return {"error": reason, "retry_count": retry_count + 1}


def compute_stats(columns: list[str], rows: list[list[object]]) -> dict[str, object]:
    """本地确定性统计:行数 + 数值列 max/min/avg(显式排除 bool)。"""
    stats: dict[str, object] = {"rows": len(rows)}
    if not columns or not rows:
        return stats
    for ci, col in enumerate(columns):
        values: list[int | float] = []
        for row in rows:
            if ci >= len(row):
                continue
            v = row[ci]
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                values.append(v)
        if values:
            stats[f"{col}_max"] = max(values)
            stats[f"{col}_min"] = min(values)
            stats[f"{col}_avg"] = round(sum(values) / len(values), 2)
    return stats


def parse_summary_json(raw: str) -> dict[str, object]:
    """解析 LLM 返回的 JSON(容错:剥 markdown 标记、取首尾花括号子串);失败返回空 dict。"""
    text = raw.strip().strip("`")
    if text.lower().startswith("json"):
        text = text[4:]
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return {}
        else:
            return {}
    return parsed if isinstance(parsed, dict) else {}


def fallback_summary(stats: dict[str, object]) -> str:
    """LLM 失败时用 stats 拼一句话总结。"""
    rows = stats.get("rows", 0)
    for k, v in stats.items():
        if k.endswith("_max") and isinstance(v, (int, float)):
            col = k[:-4]
            return (
                f"共 {rows} 行，{col} 最大值 {v}，"
                f"最小值 {stats.get(f'{col}_min', '?')}，"
                f"平均 {stats.get(f'{col}_avg', '?')}。"
            )
    return f"共 {rows} 行结果。"


def _normalize_insights(raw: object) -> list[dict[str, str]]:
    """LLM 产出的 insights 结构防御:只收 {type: str, text: str} 项。"""
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for item in raw:
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            out.append({"type": str(item.get("type", "")), "text": item["text"]})
    return out


# ── 节点7:结构化洞察(stats 本地确定性 + LLM insights,多级降级)──
async def summarize(state: AgentState) -> dict[str, object]:
    execution = state.get("execution")
    if execution is None or not execution.rows:
        return {"summary": "查询无结果。", "insights": [], "stats": {}}
    stats = compute_stats(execution.columns, execution.rows)
    question = state.get("resolved_question") or state["question"]
    data_preview = f"列：{execution.columns}\n前5行：{execution.rows[:5]}"
    summary = ""
    insights: list[dict[str, str]] = []
    try:
        raw = await deps.get_llm().chat(
            SUMMARIZE_SYSTEM, f"问题：{question}\n结果：\n{data_preview}\n\n生成 JSON："
        )
        parsed = parse_summary_json(raw)
        summary = str(parsed.get("summary", "")).strip()
        insights = _normalize_insights(parsed.get("insights", []))
    except LlmError:
        # LLM 失败降级:insights 空,summary 走 stats 兜底
        log.warning("summarize LLM 调用失败,降级 stats 兜底", exc_info=True)
    if not summary:
        summary = fallback_summary(stats)
    return {"summary": summary, "insights": insights, "stats": stats}
