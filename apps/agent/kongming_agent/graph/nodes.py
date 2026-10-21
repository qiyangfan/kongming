"""实现 NL2SQL 图节点，通过 LLM 生成与解释结果，并回调控制面检索和执行。

SQL 提示词见 prompts.py；自愈节点会把校验或执行错误传回 generate 供模型修正。
"""

import asyncio
import json
import logging
import re
import threading
from collections.abc import Sequence
from typing import cast

from kongming_agent import deps, numeric, sqlguard
from kongming_agent.apiclient import MetricHit, SchemaMeta
from kongming_agent.graph.budget import Segment, converge
from kongming_agent.graph.citations import sanitize_citations, surviving_markers
from kongming_agent.graph.doc_prompt import FUSED_ANSWER_SYSTEM, INTENT_ROUTE_SYSTEM
from kongming_agent.graph.prompts import (
    SQL_GEN_SYSTEM,
    SUMMARIZE_SYSTEM,
    UNDERSTAND_MULTI_SYSTEM,
    UNDERSTAND_SINGLE_SYSTEM,
)
from kongming_agent.graph.state import (
    INTENT_DATA,
    INTENT_FUSED,
    INTENT_KNOWLEDGE,
    VALID_INTENTS,
    AgentState,
    ExecutionResult,
    ValidationResult,
    fused_takes_over,
)
from kongming_agent.graph.streams import emit_event
from kongming_agent.graph.table_prune import prune_schema
from kongming_agent.llm import LlmError
from kongming_agent.settings import Settings

# 装配预算分段优先级(0=不可切;小者先裁)。schema/history 殿后(无表结构
# = 无 SQL 可生成);relations/metric 先行(与表结构信息部分冗余)
_PRI_RELATIONS = 1
_PRI_METRIC = 2
_PRI_MEMORY = 3
_PRI_SKILL_TERMS = 4
_PRI_SKILL_EXAMPLES = 5
_PRI_HISTORY_SQL = 6
_PRI_SCHEMA = 7


def assemble_generate_user_prompt(
    *,
    question: str,
    memory_hint: str,
    schema_hint: str,
    relations_hint: str,
    metric_hints: str,
    skill_terms: str,
    skill_examples: str,
    history_sql: str,
    extra: str,
    budget_tokens: int,
) -> tuple[str, dict[str, int], int]:
    """generate 的 user 提示八段装配 + 预算收敛(纯函数,可单测)。

    预算内:输出与既有装配逻辑逐字一致(锚);超限:按优先级从尾部收敛
    (schema/history 首位豁免);不可切段(纪律段 system/问句/selfheal 喂回)
    本身超预算 → PromptBudgetExceeded。返回 (user 文本, 裁切记录, 估算 tokens)。
    """
    segments = [
        # 纪律段(system)参与预算估算但不在 user 文本内;不可切
        Segment("system", SQL_GEN_SYSTEM),
        Segment("question", f"问题：{question}"),
        Segment("memory", memory_hint, priority=_PRI_MEMORY),
        Segment("schema_hint", schema_hint, priority=_PRI_SCHEMA, lines=True, keep_head=1),
        Segment("relations", relations_hint, priority=_PRI_RELATIONS),
        Segment("metric", metric_hints, priority=_PRI_METRIC),
        Segment("skill_terms", skill_terms, priority=_PRI_SKILL_TERMS, lines=True),
        # 示例段首行=禁令前缀(fewshot SQL 仅文本不执行),必须保留
        Segment(
            "skill_examples", skill_examples, priority=_PRI_SKILL_EXAMPLES, lines=True, keep_head=1
        ),
        Segment("history_sql", history_sql, priority=_PRI_HISTORY_SQL, lines=True, keep_head=1),
        # selfheal 喂回与哨兵引导:自愈语义属正确性,收敛后仍完整
        Segment("selfheal", extra),
    ]
    result = converge(segments, budget_tokens)
    texts = {s.key: s.text for s in result.segments}
    # 空段不改变装配格式；预算收敛后为空时也沿用相同分支。
    memory_part = f"{texts['memory']}\n\n" if texts["memory"] else ""
    _rel = f"表关系参考：\n{texts['relations']}" if texts["relations"] else ""
    _met = f"指标口径参考：\n{texts['metric']}" if texts["metric"] else ""
    _semantic = "\n\n".join(x for x in (_rel, _met) if x)
    semantic_part = f"\n\n{_semantic}" if _semantic else ""
    _st = f"业务术语参考：\n{texts['skill_terms']}" if texts["skill_terms"] else ""
    _se = f"问法示例：\n{texts['skill_examples']}" if texts["skill_examples"] else ""
    _skill = "\n\n".join(x for x in (_st, _se) if x)
    skill_part = f"\n\n{_skill}" if _skill else ""
    history_part = f"\n\n参考历史成功 SQL：\n{texts['history_sql']}" if texts["history_sql"] else ""
    user = (
        f"问题：{question}\n\n{memory_part}可用表结构：\n"
        f"{texts['schema_hint']}{semantic_part}{skill_part}{history_part}"
        f"{texts['selfheal']}\n\n生成 SQL："
    )
    return user, result.cut, result.estimated_tokens


def legacy_generate_user_prompt(
    *,
    question: str,
    memory_hint: str,
    schema_hint: str,
    relations_hint: str,
    metric_hints: str,
    skill_terms: str,
    skill_examples: str,
    history_sql: str,
    extra: str,
) -> str:
    """按原有分段布局组装用户提示词;预算充足时与预算收敛版逐字一致(测试锚)。"""
    memory_part = f"{memory_hint}\n\n" if memory_hint else ""
    _rel = f"表关系参考：\n{relations_hint}" if relations_hint else ""
    _met = f"指标口径参考：\n{metric_hints}" if metric_hints else ""
    _semantic = "\n\n".join(x for x in (_rel, _met) if x)
    semantic_part = f"\n\n{_semantic}" if _semantic else ""
    _st = f"业务术语参考：\n{skill_terms}" if skill_terms else ""
    _se = f"问法示例：\n{skill_examples}" if skill_examples else ""
    _skill = "\n\n".join(x for x in (_st, _se) if x)
    skill_part = f"\n\n{_skill}" if _skill else ""
    history_part = f"\n\n参考历史成功 SQL：\n{history_sql}" if history_sql else ""
    return (
        f"问题：{question}\n\n{memory_part}可用表结构：\n"
        f"{schema_hint}{semantic_part}{skill_part}{history_part}{extra}\n\n生成 SQL："
    )


log = logging.getLogger(__name__)

MAX_HISTORY = 5  # 多轮上下文截断(nl2sql spec:平衡 token 与连贯性)

MAX_RETRY = 2  # 重试上限(初始尝试不计):nl2sql spec「重试次数 < 上限(默认 2)」

# 生成纪律:空结构占位文案与哨兵机制联动(如实说明,严禁教唆编表)
_EMPTY_HINT = (
    "（未能检索到与问题相关的表结构；若问题所需数据不在可用范围内，"
    "只输出 NO_RELEVANT_TABLE——严禁臆测表名或列名。）"
)

# 知识未覆盖哨兵(生成侧如实说明的唯一出口);短路零执行零重试
# 语义补全两级——无支撑本问题的表(表级)/所需关键信息项无列且无法推导(列级)
_NO_DATA_SENTINEL = "NO_RELEVANT_TABLE"
_NO_DATA_ERROR = "当前数据源未找到回答该问题所需的数据,可切换数据源或调整问法后重试"


# ── 对话复语直通(闭合词表 + 全等判定,零依赖纯数据/纯函数)──
# 规范化:去首尾与内部空白、去中英文句读、拉丁字母小写。
# 全等匹配,SHALL NOT 包含/前缀/语义相似——"取消订单""确认切换"照常补全。
_PUNCT_RE = re.compile(r"[\s,。，、！？!?,.;:；：…\-—_\"'“”‘’()（）\[\]【】/\\~·「」『』《》〈〉]+")

# 闭合词表(规范化形态;随代码发布,不做管理台热配)。三类:
# 否定/终止 · 应答/致谢 · 招呼。多词英文条目去空格后入表(never mind→nevermind)。
DIALOGUE_REPLY_WORDS: frozenset[str] = frozenset(
    {
        # 否定/终止
        "不用了",
        "不需要",
        "不必",
        "算了",
        "取消",
        "终止",
        "停",
        "no",
        "nope",
        "cancel",
        "nevermind",
        # 应答/致谢
        "好的",
        "好",
        "知道了",
        "明白",
        "收到",
        "谢谢",
        "多谢",
        "thanks",
        "thankyou",
        "gotit",
        "ok",
        "okay",
        "nothanks",  # 「NO, thanks」形态(spec 场景:句读大小写变体)
        "nothankyou",
        # 招呼
        "你好",
        "您好",
        "hi",
        "hello",
        "hey",
    }
)


def normalize_dialogue_reply(text: str) -> str:
    """复语规范化(纯函数):去首尾与内部空白、去中英文句读、拉丁字母小写。"""
    return _PUNCT_RE.sub("", text).lower()


def is_dialogue_reply(question: str) -> bool:
    """复语判定(纯函数):规范化后全等于闭合词表成员。"""
    return normalize_dialogue_reply(question) in DIALOGUE_REPLY_WORDS


# 复语直通可观测计数(sqlguard.snapshot_stats 同款形态;零新采集通道)
_passthrough_lock = threading.Lock()
_passthrough_stats = {"dialogue_reply_passthrough_total": 0}


def snapshot_passthrough_stats() -> dict[str, int]:
    """复语直通命中计数快照(可观测:词表宽严调整依据)。"""
    with _passthrough_lock:
        return dict(_passthrough_stats)


def _note_dialogue_passthrough(trace_id: str | None) -> None:
    """命中入账 + 随 trace 日志下发(复用既有日志关联通道,零新采集)。"""
    with _passthrough_lock:
        _passthrough_stats["dialogue_reply_passthrough_total"] += 1
    log.info("071 复语直通命中 trace_id=%s", trace_id)


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
    # 复语直通——零模型调用、零检索、零执行,按既有第四态收敛;
    # 不设补全问句(迫使 retrieve/generate 不推进),判定只看当前问句与 history 无关
    if is_dialogue_reply(question):
        _note_dialogue_passthrough(state.get("trace_id"))
        return {
            "end_reason": "no_data",
            "error": _NO_DATA_ERROR,
            # 直通计数随 trace 下发(复用 QueryOut.stats 既有通道,零新采集)
            "stats": {"dialogue_reply_passthrough": 1},
        }
    history = state.get("history", [])[-MAX_HISTORY:]
    if history:
        system = UNDERSTAND_MULTI_SYSTEM
        history_text = "\n".join(f"第{i + 1}轮：{q}" for i, q in enumerate(history))
        prompt = f"历史问题：\n{history_text}\n\n当前问题：{question}\n\n补全后的完整问题："
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
    # LLM 返回空时使用原问句。
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


def _metric_hit_dicts(hits: Sequence[MetricHit]) -> list[dict[str, str | None]]:
    """MetricHit 域对象 → state 字典投影(纯函数;空列表=零命中)。"""
    return [
        {
            "metric_id": h.metric_id,
            "metric_name": h.metric_name,
            "sql_template": h.sql_template,
            "updated_at": h.updated_at,
        }
        for h in hits
    ]


# ── 节点2:schema 提示获取(RAG 召回优先,零命中/失败回退实时内省兜底)──
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
        # 多轮继承:最近 3 条历史透传(api 侧取末条走继承路补上轮依赖表);
        # 单轮请求省略 history 键，保持请求体兼容。
        history=list(state.get("history") or [])[-3:],
        # 调用者身份透传(入口 state 自 chat 请求写入;None=不携带键,
        # api 侧纯公共形态)
        user_id=state.get("user_id"),
    )
    # 历史 SQL 独立于表说明命中:「知识未抓取但 query_history 已有成功
    # 记录」的真实形态下,schema 空回退内省、history 照常作 few-shot,不整体丢弃。
    history_sql = ctx.history_sql if ctx is not None else ""
    doc_hints = list(ctx.doc_hints) if ctx is not None else []
    # 结构化口径命中(空=无命中;与 metric_hints 文字段同源)
    metric_hits = _metric_hit_dicts(ctx.metric_hits) if ctx is not None else []
    if ctx is not None and ctx.schema_hint.strip():
        return {
            "schema_hint": ctx.schema_hint,
            "history_sql": history_sql,
            "relations_hint": ctx.relations_hint,
            "metric_hints": ctx.metric_hints,
            "metric_hits": metric_hits,
            "doc_hints": doc_hints,
            "skill_terms": ctx.skill_terms,
            "skill_examples": ctx.skill_examples,
            "skill_persona": ctx.skill_persona,
        }
    # 召回服务不可用时，回退到实时表结构查询。
    meta = await deps.get_api().fetch_schema(
        tenant_key=state["tenant_key"],  # pyright: ignore[reportTypedDictNotRequiredAccess]
        datasource_key=datasource_key,
    )
    if meta is None:
        # 表结构不可用时返回空提示，但保留其他召回结果。
        out0: dict[str, object] = {"schema_hint": "", "history_sql": history_sql}
        if ctx is not None:
            out0["relations_hint"] = ctx.relations_hint
            out0["metric_hints"] = ctx.metric_hints
            out0["metric_hits"] = _metric_hit_dicts(ctx.metric_hits)
            out0["doc_hints"] = list(ctx.doc_hints)
            out0["skill_terms"] = ctx.skill_terms
            out0["skill_examples"] = ctx.skill_examples
            out0["skill_persona"] = ctx.skill_persona
        return out0
    # 表级预筛(问题+术语/示例/指标共现加权打分,top-N)。零命中=空表
    # 集 → 既有哨兵形态(不新增终态);关闭开关(对拍/应急)= 全量注入逐字回退。
    settings = Settings()
    if settings.schema_prune:
        assets = "\n\n".join(
            x
            for x in (
                ctx.skill_terms if ctx is not None else "",
                ctx.skill_examples if ctx is not None else "",
                ctx.metric_hints if ctx is not None else "",
            )
            if x
        )
        original_count = len(meta.tables)
        meta, hits = prune_schema(question, meta, assets=assets, top_n=settings.schema_prune_top_n)
        emit_event(
            "trace",
            {
                "node": "retrieve",
                "fields": {
                    "kind": "table_prune",
                    "kept": [h.table for h in hits],
                    "cut_count": original_count - len(hits),
                    "scores": {h.table: h.score for h in hits},
                    "sources": {h.table: list(h.sources) for h in hits},
                },
            },
        )
    out: dict[str, object] = {
        "schema_hint": format_schema_hint(meta),
        "history_sql": history_sql,
    }
    # 回退内省形态仍透传语义段(metric_hints 按问句命中,不受 schema
    # 命中约束;relations_hint 在召回空时本就为空)
    if ctx is not None:
        out["relations_hint"] = ctx.relations_hint
        out["metric_hints"] = ctx.metric_hints
        out["metric_hits"] = _metric_hit_dicts(ctx.metric_hits)
        out["doc_hints"] = list(ctx.doc_hints)
        out["skill_terms"] = ctx.skill_terms
        out["skill_examples"] = ctx.skill_examples
        out["skill_persona"] = ctx.skill_persona
    return out


def clean_sql(raw: str) -> str:
    """剥 LLM 输出的 markdown 标记(```sql ... ```)。"""
    sql = raw.strip().strip("`").strip()
    if sql.lower().startswith("sql\n"):
        sql = sql[4:]
    return sql.strip()


# ── 节点3:SQL 生成 ──


def _selfheal_guidance(error: str) -> str:
    """自愈喂回分类:按 PG 错误形态生成针对性修正指令。

    旧行为对一切 does-not-exist 附表哨兵出口——列不存在会被引向弃答
    (表存在,出口是改列);泛化「请修正」使机械错同因连败(
    missing FROM-clause 重试 2 轮同错)。纯函数,优先级自上而下:别名→
    列→关系;哨兵出口仅关系类(表/列以外的 does-not-exist 如函数/类型,
    维持泛化喂回,不误引弃答出口)。
    """
    low = error.lower()
    m = re.search(r'missing from-clause entry for table "([^"]+)"', low)
    if m:
        return (
            f"SQL 引用了「{m.group(1)}」但 FROM/JOIN 中并没有定义这个别名——"
            "为其补充对应表的 JOIN,或改用 FROM/JOIN 中已有的别名；"
            "只引用『可用表结构』清单中存在的表。"
        )
    if re.search(r'column "[^"]+" .*does not exist', low) or (
        low.startswith('column "') and "does not exist" in low
    ):
        return (
            "引用的列不在所引用表的『可用表结构』清单中——只使用清单内的列名"
            "并核对拼写；若该表确无支撑问句口径的列，不得用其他列近似顶替。"
        )
    if re.search(r'relation "[^"]+" .*does not exist', low) or (
        low.startswith('relation "') and "does not exist" in low
    ):
        return (
            "所引用的表或列不在『可用表结构』清单中——核对清单；"
            "若清单确无所需数据，只输出 NO_RELEVANT_TABLE。"
        )
    return ""


async def generate(state: AgentState) -> dict[str, object]:
    # 下标:入口 state 恒写入 question(缺键属编程错误),保留下标求值序
    question = state.get("resolved_question")
    if not question:
        question = state["question"]  # pyright: ignore[reportTypedDictNotRequiredAccess]
    schema_hint = state.get("schema_hint") or ""
    if not schema_hint.strip():
        schema_hint = _EMPTY_HINT
    # 记忆提示放在表结构前；为空时不改变提示词。
    memory_hint = (state.get("memory_hint") or "").strip()
    # 关系和指标提示放在表结构与历史 SQL 之间；为空时不改变提示词。
    relations_hint = (state.get("relations_hint") or "").strip()
    metric_hints = (state.get("metric_hints") or "").strip()
    # 历史成功 SQL 段:召回命中时拼在表结构之后——人类验证过的 SQL 置信度
    # 高于表说明内嵌示例(强 few-shot);系统模板资产零改动,只动 user 拼接。
    history_sql = (state.get("history_sql") or "").strip()
    # skill-pack 两段(可空;零段零痕迹)
    skill_terms = (state.get("skill_terms") or "").strip()
    skill_examples = (state.get("skill_examples") or "").strip()
    extra = ""
    error = state.get("error") or ""
    if error:
        # 自愈:把上次错误喂回 LLM;按错误形态附加针对性修正指令
        # (别名/列/表分类;列错不引向表哨兵出口,机械错不再同因连败)
        extra = f"\n\n上次生成的 SQL 有误：{error}\n请修正。"
        guidance = _selfheal_guidance(error)
        if guidance:
            extra += guidance
    # 装配预算(窗口 − 预留输出 − 安全边际,按 SQL_GEN 生效模型解析)。
    # 预算内逐字;超限按段优先级收敛(证据尾端首位豁免/纪律与问句不截);
    # 触发即随 trace 事件下发(可观测,SHALL NOT 静默降质);不可切段本身超预算
    # → PromptBudgetExceeded(如实失败分支,可归因)
    llm = deps.get_llm()
    budget = llm.prompt_budget("SQL_GEN")
    user, cut, estimated = assemble_generate_user_prompt(
        question=question,
        memory_hint=memory_hint,
        schema_hint=schema_hint,
        relations_hint=relations_hint,
        metric_hints=metric_hints,
        skill_terms=skill_terms,
        skill_examples=skill_examples,
        history_sql=history_sql,
        extra=extra,
        budget_tokens=budget,
    )
    if cut:
        emit_event(
            "trace",
            {
                "node": "generate",
                "fields": {
                    "kind": "prompt_budget",
                    "budget_tokens": budget,
                    "estimated_tokens": estimated,
                    "cut": cut,
                },
            },
        )
    sql = await llm.chat(
        SQL_GEN_SYSTEM,
        user,
        temperature=0.1,
        task="SQL_GEN",
        trace_id=state.get("trace_id"),
        tenant_key=state.get("tenant_key"),
    )
    # 哨兵=模型如实说明知识未覆盖——短路产物直达失败终态,
    # 不进 validate/execute、不进自愈(零执行零重试;路由见 build._after_generate)
    if _is_no_data_sentinel(sql):
        return {"generated_sql": "", "error": _NO_DATA_ERROR, "end_reason": "no_data"}
    # 生成成功即清掉上轮错误(错误已消费)
    return {"generated_sql": clean_sql(sql), "error": ""}


# ── 节点4:本地预检(轻量四规则;权威校验在 api 防火墙,不复刻)──
# 解析走 liannu 引擎适配(模式化:影子对拍/切主/降级),判定语义不变
def validate(state: AgentState) -> dict[str, object]:
    sql = state.get("generated_sql") or ""
    if not sql.strip():
        result = ValidationResult(passed=False, reason="EMPTY_SQL")
    else:
        result = sqlguard.validate(sql)
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


# ── 知识分流:意图词表 + doc_answer 节点 ──

# 知识型意图词表(规则初判;误分流只影响答法无数据副作用,spec)
KNOWLEDGE_INTENT_WORDS = (
    "是什么",
    "为什么",
    "政策",
    "流程",
    "口径",
    "定义",
    "规范",
    "制度",
    "办法",
    "怎么规定",
)

# 强文档意图词表——「是什么」是泛系系词,数据追问(「这些影片各自的
# 分类是什么」)与文档问句同形;多轮会话在场时仅系词命中不分流(结构性
# 条件替代指代词表,弱信号词表曾两次误伤的教训)
DOC_STRONG_INTENT_WORDS = tuple(w for w in KNOWLEDGE_INTENT_WORDS if w != "是什么")

# SQL 强意图词表用于识别明显的数据库查询请求，只收**几乎只在
# 问库出现**的强信号;通用量词(多少/数量/平均/最高/哪个)不收
# 图片问题也可能包含“数量”等通用词，不能据此否决文档问答分流。
SQL_INTENT_WORDS = (
    "数据",
    "数据库",
    "表里",
    "表中",
    "库中",
    "统计",
    "查询",
    "列出",
    "显示",
    "排名",
    "SQL",
    "sql",
    "sakila",
    "olist",
    "default",
)


def is_knowledge_intent(question: str) -> bool:
    """知识型意图规则判定(纯函数)。"""
    return any(w in question for w in KNOWLEDGE_INTENT_WORDS)


def is_strong_doc_intent(question: str) -> bool:
    """强文档意图判定(纯函数)——泛系系词之外的显式文档词。"""
    return any(w in question for w in DOC_STRONG_INTENT_WORDS)


def is_sql_intent(question: str) -> bool:
    """判断问题是否包含明确的数据库查询意图。"""
    return any(w in question for w in SQL_INTENT_WORDS)


def route_after_retrieve(state: AgentState) -> str:
    """融合和数据意图走 SQL 生成，知识意图有文档时直接回答；无意图时按词表与附件规则回退。"""
    doc_hints = state.get("doc_hints") or []
    # 下标:入口 state 恒写入 question(缺键属编程错误),保留下标求值序
    question = state.get("resolved_question")
    if not question:
        question = state["question"]  # pyright: ignore[reportTypedDictNotRequiredAccess]
    intent = state.get("intent")
    if intent == INTENT_FUSED:
        return "generate"
    if intent == INTENT_KNOWLEDGE:
        return "doc_answer" if doc_hints else "generate"
    if intent == INTENT_DATA and state.get("has_attachments") and not is_sql_intent(question):
        # 意图判定看不到图片内容;有附件且无 SQL 强意图时按图片问答处理。
        return "doc_answer"
    if intent is not None:  # 无附件的数据问题走 SQL 生成。
        return "generate"
    # 意图判断不可用时，按原有规则词表分流。
    if doc_hints and is_knowledge_intent(question):
        if state.get("history") and not is_strong_doc_intent(question):
            return "generate"
        return "doc_answer"
    if state.get("has_attachments") and not is_sql_intent(question):
        return "doc_answer"
    return "generate"


async def doc_answer(state: AgentState) -> dict[str, object]:
    """根据文档或图片描述回答问题，不执行 SQL。

    图片问答使用原始问句，避免指代改写丢失图片描述。
    """
    from kongming_agent.graph.doc_prompt import (
        DOC_ANSWER_SYSTEM,
        IMAGE_ANSWER_SYSTEM,
    )

    doc_hints = state.get("doc_hints") or []
    if doc_hints:
        # 两处下标:入口 state 恒写入 question(缺键属编程错误),保留求值序
        if state.get("has_attachments"):
            question = state["question"]  # pyright: ignore[reportTypedDictNotRequiredAccess]
        else:
            resolved = state.get("resolved_question")
            question = resolved or state["question"]  # pyright: ignore[reportTypedDictNotRequiredAccess]
        # 注入段标注=引用键(与净化合法集同源;source_ref 仅作块头说明)
        parts = [
            f"来源 {h.get('citation_key') or h['source_ref']}:\n{h['content'][:1500]}"
            for h in doc_hints[:5]
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
    # 服务故障被表述为内容缺失,属伪造终态
    answer = await deps.get_llm().chat(
        system,
        user,
        temperature=0.1,
        task="DOC_ANSWER",
        trace_id=state.get("trace_id"),
        tenant_key=state.get("tenant_key"),
    )
    summary = answer or "文档中未提及相关内容。"
    sources: list[dict[str, object]] = []
    if doc_hints:
        # 确定性净化(零模型;与 numeric 收口同位置)——合法键集取自
        # 截断前的完整命中集(注入段 top-5/字符上限裁剪不得误杀真引用)
        legal_keys = [str(h.get("citation_key") or "") for h in doc_hints]
        legal_keys = [k for k in legal_keys if k]
        result = sanitize_citations(summary, legal_keys)
        summary = result.text
        # sources 投影(api 侧落库;存活键 → 命中集元数据回填)
        by_key = {str(h.get("citation_key") or ""): h for h in doc_hints}
        for key in result.surviving_keys:
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
        # 被剥离计数随 trace 下发(可观测;净化在输出护栏前完成)
        if result.stripped:
            emit_event(
                "trace",
                {
                    "node": "doc_answer",
                    "fields": {
                        "kind": "citation_sanitize",
                        "stripped": result.stripped,
                        "surviving": list(result.surviving_keys),
                    },
                },
            )
    return {
        "execution": ExecutionResult(verdict="PASS", columns=[], rows=[]),
        "summary": summary,
        "insights": [],
        "stats": {},
        "sources": sources,  # 结构化来源(空=SQL 道/无命中)
    }


# ── 三态意图路由(route_intent)+ 融合问答(fused_answer)──

# 可观测计数(复语直通 snapshot_stats 同款形态;零新采集通道):
# 路由回退(LLM 失败/超时/低置信)与融合兜底(生成失败回退纯数据答)
_intent_lock = threading.Lock()
_intent_stats = {"intent_route_fallback_total": 0, "fused_answer_fallback_total": 0}


def snapshot_intent_route_stats() -> dict[str, int]:
    """回退/兜底计数快照(可观测:prompt 调优与阈值调整依据)。"""
    with _intent_lock:
        return dict(_intent_stats)


def parse_intent_route(raw: str) -> tuple[str, float, str] | None:
    """路由 LLM 输出解析(纯函数):JSON {intent, confidence, reason}。
    intent 越域 / 置信缺失或不可解析 / 置信越界 [0,1] → None(调用方回退词表)。"""
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
    return intent, confidence, str(parsed.get("reason", "")).strip()


async def route_intent(state: AgentState) -> dict[str, object]:
    """检索前判定问句意图；失败或置信度不足时回退到规则词表。

    回退时不写 intent，沿用原有分流规则；意图误判只影响答法，不触发数据操作。
    """
    question = state.get("resolved_question")
    if not question:
        question = state["question"]  # pyright: ignore[reportTypedDictNotRequiredAccess]
    settings = Settings()
    parsed: tuple[str, float, str] | None = None
    try:
        raw = await asyncio.wait_for(
            deps.get_llm().chat(
                INTENT_ROUTE_SYSTEM,
                f"用户问题:{question}\n\n输出 JSON:",
                temperature=0.1,
                max_tokens=120,
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
    intent, confidence, reason = parsed
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
    data_preview = (
        f"列:{execution.columns}\n总行数:{len(execution.rows)}\n"
        f"本地统计:{stats}\n前5行:{execution.rows[:5]}"
    )
    # 注入段标注=引用键(与净化合法集同源;缺失回退 source_ref 作块头说明)
    parts = [
        f"来源 {h.get('citation_key') or h['source_ref']}:\n{h['content'][:1500]}"
        for h in doc_hints[:5]
    ]
    user = (
        f"问题:{question}\n\n数据结果:\n{data_preview}\n\n文档内容:\n"
        + "\n\n".join(parts)
        + "\n\n回答:"
    )
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
    # 确定性净化(零模型):合法键集=本节点下发父块集
    legal_keys = [str(h.get("citation_key") or "") for h in doc_hints]
    legal_keys = [k for k in legal_keys if k]
    sanitized = sanitize_citations(summary, legal_keys)
    summary = sanitized.text
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
    # 数值纪律收口:融合正文同过确定性审计,白名单=结果面∪stats∪本轮
    # 计算值∪下发文档内容数字(双依据语义:正文数字须可溯源到数据或文档,
    # 两者皆非才剥——文档口径数字如「7 天」是合法证据,不得按数据口径误杀)
    doc_numbers = [t.value for part in parts for t in numeric.extract_numbers(part)]
    audited = numeric.audit_summarize_output(
        summary,
        [],
        columns=execution.columns,
        rows=execution.rows,
        stats=stats,
        compute_values=[*(state.get("compute_values") or []), *doc_numbers],
        structural=[float(len(execution.rows)), float(numeric.PREVIEW_ROWS)],
    )
    final = audited.summary or fallback_summary(stats)
    # sources 投影必须以「过审终稿」为准:审计会剥句(含被引句),若按
    # 净化期存活键投影,会出现「列表有条目而正文无标注」的幽灵不一致
    # (chat/knowledge-qa spec 明文 SHALL NOT)——终稿存活标记重投影,
    # 全剥回退纯数据答时 sources 自然为空
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
    return {
        "summary": final,
        "insights": [],
        "sources": sources,
    }


# 标识列名信号与期间词:与 api 侧 图表列角色画像同规则(跨进程各自落地的
# 纯值面小规则,注释互引防漂移;num 后缀刻意不入——order_num 类计数量是度量)
_STAT_ID_NAME = re.compile(r"(?:^|_)(?:id|no|code)$|^id_")
_STAT_PERIOD_NAMES = {"year", "quarter", "month", "week", "day"}


def _is_stat_worthy(col: str, values: list[int | float]) -> bool:
    """列是否具备统计度量资格:标识/期间名称信号,或整数稠密序列
    (起点 0/1、连续、1<去重数≤16、上界≤64)的列无统计语义,不产出
    max/min/avg——内部 id 不进统计、不喂 LLM、不上前端徽标。"""
    lowered = col.lower()
    if _STAT_ID_NAME.search(lowered) or lowered in _STAT_PERIOD_NAMES:
        return False
    ints = [v for v in values if isinstance(v, int)]
    if ints and len(ints) == len(values):
        distinct = set(ints)
        lo, hi = min(ints), max(ints)
        if 1 < len(distinct) <= 16 and lo in (0, 1) and hi <= 64 and hi - lo + 1 == len(distinct):
            return False
    return True


def compute_stats(columns: list[str], rows: list[list[object]]) -> dict[str, object]:
    """本地确定性统计:行数 + 具备度量资格的数值列 max/min/avg(显式排除 bool;
    标识/期间列经 _is_stat_worthy 排除)。
    末两行环比/末值占比等派生量在时间列启发式成功时并入;失败不产出。"""
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
        if values and _is_stat_worthy(col, values):
            stats[f"{col}_max"] = max(values)
            stats[f"{col}_min"] = min(values)
            stats[f"{col}_avg"] = round(sum(values) / len(values), 2)
    derived = numeric.precompute_derived_stats(columns, rows)
    for key, val in derived.items():
        # 不覆盖已有 max/min/avg 语义;派生键独立命名(delta_pct/share_pct)
        stats.setdefault(key, val)
    return stats


# summarize 上下文的表说明段抽取——generate 与 summarize 共享召回
# schema_hint 的表说明段(rag spec「不再单开别名段」):只取召回形态表头行
# `schema.table(别名)(列…) -- 表说明`,剔除「示例:」行省 token;内省回退
# 形态(「表 x.y：…」表头+「  - 」列行,无别名且全限定名与称谓规则相悖)
# 整体不注入。头行同时含 alias 与表说明中文——别名缺失时 LLM 可依
# 「回退表说明中的中文名称」规则取中文名。不做任何「表名(…)」正则解析:
# 该形态无法区分 alias 与列清单,无 alias 表会被解析出「别名=列清单」垃圾。
_HINT_HEADER_RE = re.compile(r"^[A-Za-z_]\w*\.[A-Za-z_]\w*\(")


def _table_hint_lines(schema_hint: str) -> str:
    """取 schema_hint 的召回形态表头行(含业务称谓别名与表说明)。"""
    return "\n".join(ln for ln in schema_hint.splitlines() if _HINT_HEADER_RE.match(ln))


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
            out.append({"type": str(typed.get("type", "")), "text": cast("str", typed["text"])})
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
    # 上下文接地:行数+本地确定性统计+前 5 行——数值结论以 stats 为准,
    # 消除「只喂前 5 行以偏概全」的信息根源(冒烟 olist 状态分布说错最大值)
    data_preview = (
        f"列：{execution.columns}\n总行数：{len(execution.rows)}\n"
        f"本地统计：{stats}\n前5行：{execution.rows[:5]}"
    )
    # 表说明段直拼(含业务称谓别名;用户用语已在 question 中,LLM 按
    # SUMMARIZE_SYSTEM 称谓规则对齐:用户用语>alias>表说明中文名)
    schema_hint = state.get("schema_hint") or ""
    context = f"问题：{question}\n结果：\n{data_preview}"
    table_hint = _table_hint_lines(schema_hint)
    if table_hint:
        context += f"\n所涉表(表名(业务称谓别名) 列 -- 表说明)：\n{table_hint}"
    # 人设段注入 summarize(只影响表述风格与禁令,不改安全纪律)
    skill_persona = (state.get("skill_persona") or "").strip()
    if skill_persona:
        context += f"\n回答风格与禁令(仅风格,不得臆测编表或放宽只读约束)：\n{skill_persona}"
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
    # 数值纪律收口:确定性审计剥离非白名单数值句(不重试 LLM、不动 stats)
    compute_values = state.get("compute_values") or []
    audited = numeric.audit_summarize_output(
        summary,
        insights,
        columns=execution.columns,
        rows=execution.rows,
        stats=stats,
        compute_values=compute_values,
        structural=[float(len(execution.rows)), float(numeric.PREVIEW_ROWS)],
    )
    return {
        "summary": audited.summary or fallback_summary(stats),
        "insights": audited.insights,
        "stats": stats,
    }
