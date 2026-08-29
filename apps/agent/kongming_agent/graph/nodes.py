"""节点实现:LLM 接线(understand/generate/summarize)+ 控制面回调(retrieve/execute)。

prompt 资产见 prompts.py(逐字保值);user 模板的全角标点同样与上游逐字一致。
行为契约见 nl2sql/nl2sql-agent spec。
关键一跳:selfheal 把 validate/execute 的失败原因写入 state.error,
generate 读到 error 即拼「上次生成的 SQL 有误」喂回 LLM(自愈闭环)。
"""

import json
import logging
import re
from typing import cast

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

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

# 043 生成纪律:空结构占位文案与哨兵机制联动(如实说明,严禁教唆编表)
_EMPTY_HINT = (
    "（未能检索到与问题相关的表结构；若问题所需数据不在可用范围内，"
    "只输出 NO_RELEVANT_TABLE——严禁臆测表名或列名。）"
)

# 043:知识未覆盖哨兵(生成侧如实说明的唯一出口);短路零执行零重试
# 046:语义补全两级——无支撑本问题的表(表级)/所需关键信息项无列且无法推导(列级)
_NO_DATA_SENTINEL = "NO_RELEVANT_TABLE"
_NO_DATA_ERROR = "当前数据源未找到回答该问题所需的数据,可切换数据源或调整问法后重试"


def _is_no_data_sentinel(raw: str) -> bool:
    """哨兵识别(纯函数):剥包裹标记后大小写折叠全等;子串出现不触发
    (夹在解释句中的哨兵按普通输出走既有防御链,防火墙兜底)。"""
    text = raw.strip().strip("`").strip()
    if text.lower().startswith("sql\n"):
        text = text[4:]
    return text.strip("`").strip().lower() == _NO_DATA_SENTINEL.lower()


# ── 节点1:问题理解(多轮指代消解/单轮改写)──
async def understand(state: AgentState) -> dict[str, object]:
    # 入口 state 恒写入 question(main._initial_state);缺键属编程错误,保留下标
    question = state["question"]  # pyright: ignore[reportTypedDictNotRequiredAccess]
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
    rewritten = await deps.get_llm().chat(
        system,
        prompt,
        temperature=0.1,
        task="UNDERSTAND",
        trace_id=state.get("trace_id"),
        tenant_key=state.get("tenant_key"),
    )
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
    # 召回用改写问句(指代消解后语义完整;多轮裸指代「这些影片」召回会漏);
    # 两处下标:入口 state 恒写入(缺键属编程错误),保留下标求值序
    question = state.get("resolved_question")
    if not question:
        question = state["question"]  # pyright: ignore[reportTypedDictNotRequiredAccess]
    ctx = await deps.get_api().rag_retrieve(
        tenant_key=state["tenant_key"],  # pyright: ignore[reportTypedDictNotRequiredAccess]
        datasource_key=datasource_key,
        question=question,
    )
    # 历史 SQL 独立于表说明命中(R2-S1):「知识未抓取但 query_history 已有成功
    # 记录」的真实形态下,schema 空回退内省、history 照常作 few-shot,不整体丢弃。
    history_sql = ctx.history_sql if ctx is not None else ""
    doc_hints = list(ctx.doc_hints) if ctx is not None else []
    if ctx is not None and ctx.schema_hint.strip():
        return {
            "schema_hint": ctx.schema_hint,
            "history_sql": history_sql,
            "relations_hint": ctx.relations_hint,
            "metric_hints": ctx.metric_hints,
            "doc_hints": doc_hints,
        }
    # 回退:005 过渡形态(实时内省)——新租户/未抓取知识/召回服务失败零质量回归。
    meta = await deps.get_api().fetch_schema(
        tenant_key=state["tenant_key"],  # pyright: ignore[reportTypedDictNotRequiredAccess]
        datasource_key=datasource_key,
    )
    if meta is None:
        # 内省失败降级空提示:不误导、不硬编码全表(spec 明文);
        # R1-L3:doc_hints 照常透传(知识分流不依赖 schema 通道);
        # 语义段(relations/metric)与内省成败无关(纯函数于召回结果),
        # 与下方两个兄弟分支同口径透传(审查循环 F5:曾在此分支静默丢弃)
        out0: dict[str, object] = {"schema_hint": "", "history_sql": history_sql}
        if ctx is not None:
            out0["relations_hint"] = ctx.relations_hint
            out0["metric_hints"] = ctx.metric_hints
            out0["doc_hints"] = list(ctx.doc_hints)
        return out0
    out: dict[str, object] = {
        "schema_hint": format_schema_hint(meta),
        "history_sql": history_sql,
    }
    # R1-M2:回退内省形态仍透传语义段(metric_hints 按问句命中,不受 schema
    # 命中约束;relations_hint 在召回空时本就为空)
    if ctx is not None:
        out["relations_hint"] = ctx.relations_hint
        out["metric_hints"] = ctx.metric_hints
        out["doc_hints"] = list(ctx.doc_hints)
    return out


def clean_sql(raw: str) -> str:
    """剥 LLM 输出的 markdown 标记(```sql ... ```)。"""
    sql = raw.strip().strip("`").strip()
    if sql.lower().startswith("sql\n"):
        sql = sql[4:]
    return sql.strip()


# ── 节点3:SQL 生成 ──
async def generate(state: AgentState) -> dict[str, object]:
    # 下标:入口 state 恒写入 question(缺键属编程错误),保留下标求值序
    question = state.get("resolved_question")
    if not question:
        question = state["question"]  # pyright: ignore[reportTypedDictNotRequiredAccess]
    schema_hint = state.get("schema_hint") or ""
    if not schema_hint.strip():
        schema_hint = _EMPTY_HINT
    # 022:记忆段(用户已知信息)位于表结构之前;空则形态与引入前逐字一致
    memory_hint = (state.get("memory_hint") or "").strip()
    memory_part = f"{memory_hint}\n\n" if memory_hint else ""
    # 023 语义段:表关系/指标口径位于表结构之后、历史 SQL 之前;空则零痕迹
    relations_hint = (state.get("relations_hint") or "").strip()
    metric_hints = (state.get("metric_hints") or "").strip()
    _rel = f"表关系参考：\n{relations_hint}" if relations_hint else ""
    _met = f"指标口径参考：\n{metric_hints}" if metric_hints else ""
    _semantic = "\n\n".join(x for x in (_rel, _met) if x)
    semantic_part = f"\n\n{_semantic}" if _semantic else ""
    # 历史成功 SQL 段(009):召回命中时拼在表结构之后——人类验证过的 SQL 置信度
    # 高于表说明内嵌示例(强 few-shot);系统模板资产零改动,只动 user 拼接。
    history_sql = (state.get("history_sql") or "").strip()
    history_part = f"\n\n参考历史成功 SQL：\n{history_sql}" if history_sql else ""
    extra = ""
    error = state.get("error") or ""
    if error:
        # 自愈:把上次错误喂回 LLM
        extra = f"\n\n上次生成的 SQL 有误：{error}\n请修正。"
        # 043:表不存在类错误=编表先验复发,附清单引导给哨兵出口,
        # 杜绝同因连败空转烧 LLM 配额(PG UndefinedTable 报错定式,大小写不敏感)
        if "does not exist" in error.lower():
            extra += (
                "所引用的表或列不在『可用表结构』清单中——核对清单；"
                "若清单确无所需数据，只输出 NO_RELEVANT_TABLE。"
            )
    user = (
        f"问题：{question}\n\n{memory_part}可用表结构：\n"
        f"{schema_hint}{semantic_part}{history_part}{extra}\n\n生成 SQL："
    )
    sql = await deps.get_llm().chat(
        SQL_GEN_SYSTEM,
        user,
        temperature=0.1,
        task="SQL_GEN",
        trace_id=state.get("trace_id"),
        tenant_key=state.get("tenant_key"),
    )
    # 043:哨兵=模型如实说明知识未覆盖——短路产物直达失败终态,
    # 不进 validate/execute、不进自愈(零执行零重试;路由见 build._after_generate)
    if _is_no_data_sentinel(sql):
        return {"generated_sql": "", "error": _NO_DATA_ERROR, "end_reason": "no_data"}
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
        except SqlglotError as e:
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
    # 入口 state 恒含 trace_id/tenant_key/question(main._initial_state 写入),
    # 下标语义保留(缺键即编程错误)
    outcome = await deps.get_api().sql_execute(
        trace_id=state["trace_id"],  # pyright: ignore[reportTypedDictNotRequiredAccess]
        tenant_key=state["tenant_key"],  # pyright: ignore[reportTypedDictNotRequiredAccess]
        sql_text=state.get("generated_sql") or "",
        user_id=state.get("user_id"),
        # 审计记原始问句(用户真实输入)
        question=state["question"],  # pyright: ignore[reportTypedDictNotRequiredAccess]
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


# ── 024 知识分流:意图词表 + doc_answer 节点 ──

# 知识型意图词表(规则初判;误分流只影响答法无数据副作用,spec)
KNOWLEDGE_INTENT_WORDS = (
    "是什么", "为什么", "政策", "流程", "口径", "定义", "规范", "制度", "办法", "怎么规定",
)

# 036④:SQL 强意图词表(纯图片问题分流否决信号)——只收**几乎只在
# 问库出现**的强信号;通用量词(多少/数量/平均/最高/哪个)不收
# (真链路冒烟抓回:「这张图里哪个分类最高?数量是多少?」问图也带
# 「数量」,列入即误否决分流=答非所问复发)
SQL_INTENT_WORDS = (
    "数据", "数据库", "表里", "表中", "库中", "统计", "查询",
    "列出", "显示", "排名", "SQL", "sql",
    "sakila", "olist", "default",
)


def is_knowledge_intent(question: str) -> bool:
    """知识型意图规则判定(纯函数)。"""
    return any(w in question for w in KNOWLEDGE_INTENT_WORDS)


def is_sql_intent(question: str) -> bool:
    """036④:SQL 强意图判定(纯函数)——附件在场时命中即否决直答分流
    (「带图默认问图,带库词走 SQL」)。"""
    return any(w in question for w in SQL_INTENT_WORDS)


def route_after_retrieve(state: AgentState) -> str:
    """分流判定(spec「知识分流」):知识意图 + 文档命中即分流;
    schema 是否命中不作条件(024 冒烟实证:知识问题常有 schema 模糊命中,
    双条件过严——「退货政策是什么」召回 rental 表说明致不分流)。
    036④:附件在场且无 SQL 强意图→直答分流(纯图片问题不硬走 SQL)。"""
    doc_hints = state.get("doc_hints") or []
    # 下标:入口 state 恒写入 question(缺键属编程错误),保留下标求值序
    question = state.get("resolved_question")
    if not question:
        question = state["question"]  # pyright: ignore[reportTypedDictNotRequiredAccess]
    if doc_hints and is_knowledge_intent(question):
        return "doc_answer"
    if state.get("has_attachments") and not is_sql_intent(question):
        return "doc_answer"
    return "generate"


async def doc_answer(state: AgentState) -> dict[str, object]:
    """知识问答节点(024):LLM 直接按文档内容回答,无 SQL 执行面;
    终态 PASS 空结果集(022 纯文本同款先例),消息 SUCCESS。
    036④:doc_hints 为空(纯图片问题分流)时按问题(已含附件描述段)
    直答——换 IMAGE_ANSWER_SYSTEM(描述为依据,非「文档未提及」);
    **纯图片路径必须用原始拼接问题**(state["question"])——
    resolved_question 经指代消解重写会压掉描述段关键数据(冒烟实证:
    Sports/74 全丢致「图中无法确认」)。"""
    from kongming_agent.graph.doc_prompt import (
        DOC_ANSWER_SYSTEM,
        IMAGE_ANSWER_SYSTEM,
    )

    doc_hints = state.get("doc_hints") or []
    if doc_hints:
        # 036④:附件在场时用原始拼接问题——resolved_question 经指代消解改写
        # 会压掉描述段关键数据(Sports/74 教训);混合路径(附件+文档命中+
        # 知识意图)同样适用(审查循环 F3:曾只修纯图片分支)
        # 两处下标:入口 state 恒写入 question(缺键属编程错误),保留求值序
        if state.get("has_attachments"):
            question = state["question"]  # pyright: ignore[reportTypedDictNotRequiredAccess]
        else:
            resolved = state.get("resolved_question")
            question = resolved or state["question"]  # pyright: ignore[reportTypedDictNotRequiredAccess]
        parts = [
            f"来源 {h['source_ref']}:\n{h['content'][:1500]}" for h in doc_hints[:5]
        ]
        system = DOC_ANSWER_SYSTEM
        user = f"问题:{question}\n\n文档内容:\n" + "\n\n".join(parts) + "\n\n回答:"
    else:
        system = IMAGE_ANSWER_SYSTEM
        # 纯图片直答必须用原始拼接问题(含附件描述段;入口 state 恒写入)
        question = state["question"]  # pyright: ignore[reportTypedDictNotRequiredAccess]
        user = f"问题:{question}\n\n回答:"
    # LlmError 不在此吞掉:上抛经 _run_graph 统一收敛 llm_unavailable
    # (success=False)——吞掉后以 PASS+「文档中未提及相关内容。」出链,
    # 服务故障被表述为内容缺失,属伪造终态(审查循环 F4)
    answer = await deps.get_llm().chat(
        system,
        user,
        temperature=0.1,
        task="DOC_ANSWER",
        trace_id=state.get("trace_id"),
        tenant_key=state.get("tenant_key"),
    )
    return {
        "execution": ExecutionResult(verdict="PASS", columns=[], rows=[]),
        "summary": answer or "文档中未提及相关内容。",
        "insights": [],
        "stats": {},
    }


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


# 037:summarize 上下文的表说明段抽取——generate 与 summarize 共享召回
# schema_hint 的表说明段(rag spec「不再单开别名段」):只取召回形态表头行
# `schema.table(别名)(列…) -- 表说明`,剔除「示例:」行省 token;内省回退
# 形态(「表 x.y：…」表头+「  - 」列行,无别名且全限定名与称谓规则相悖)
# 整体不注入(R1-B1)。头行同时含 alias 与表说明中文——别名缺失时 LLM 可依
# 「回退表说明中的中文名称」规则取中文名。不做任何「表名(…)」正则解析:
# 该形态无法区分 alias 与列清单,无 alias 表会被解析出「别名=列清单」垃圾
# (实施期抓回)。
_HINT_HEADER_RE = re.compile(r"^[A-Za-z_]\w*\.[A-Za-z_]\w*\(")


def _table_hint_lines(schema_hint: str) -> str:
    """取 schema_hint 的召回形态表头行(含业务称谓别名与表说明)。"""
    return "\n".join(
        ln for ln in schema_hint.splitlines() if _HINT_HEADER_RE.match(ln)
    )


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
    # isinstance 收窄产物 dict[Unknown, Unknown] 仍报,守卫后收窄键型
    return cast("dict[str, object]", parsed) if isinstance(parsed, dict) else {}


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
    for item in cast("list[object]", raw):
        # isinstance 收窄产物键型仍 Unknown,守卫后 cast 再用(判定序不变)
        typed = cast("dict[str, object]", item) if isinstance(item, dict) else None
        if typed is not None and isinstance(typed.get("text"), str):
            out.append(
                {"type": str(typed.get("type", "")), "text": cast("str", typed["text"])}
            )
    return out


# ── 节点7:结构化洞察(stats 本地确定性 + LLM insights,多级降级)──
async def summarize(state: AgentState) -> dict[str, object]:
    execution = state.get("execution")
    if execution is None or not execution.rows:
        return {"summary": "查询无结果。", "insights": [], "stats": {}}
    stats = compute_stats(execution.columns, execution.rows)
    # 下标:入口 state 恒写入 question(缺键属编程错误),保留下标求值序
    question = state.get("resolved_question")
    if not question:
        question = state["question"]  # pyright: ignore[reportTypedDictNotRequiredAccess]
    # 上下文接地(018):行数+本地确定性统计+前 5 行——数值结论以 stats 为准,
    # 消除「只喂前 5 行以偏概全」的信息根源(005 冒烟 olist 状态分布说错最大值)
    data_preview = (
        f"列：{execution.columns}\n总行数：{len(execution.rows)}\n"
        f"本地统计：{stats}\n前5行：{execution.rows[:5]}"
    )
    # 037:表说明段直拼(含业务称谓别名;用户用语已在 question 中,LLM 按
    # SUMMARIZE_SYSTEM 称谓规则对齐:用户用语>alias>表说明中文名)
    schema_hint = state.get("schema_hint") or ""
    context = f"问题：{question}\n结果：\n{data_preview}"
    table_hint = _table_hint_lines(schema_hint)
    if table_hint:
        context += f"\n所涉表(表名(业务称谓别名) 列 -- 表说明)：\n{table_hint}"
    context += "\n\n生成 JSON："
    summary = ""
    insights: list[dict[str, str]] = []
    try:
        raw = await deps.get_llm().chat(
            SUMMARIZE_SYSTEM,
            context,
            task="SUMMARIZE",
            trace_id=state.get("trace_id"),
            tenant_key=state.get("tenant_key"),
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
