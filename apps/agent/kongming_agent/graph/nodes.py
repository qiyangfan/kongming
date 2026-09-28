"""节点实现:LLM 接线(understand/generate/summarize)+ 控制面回调(retrieve/execute)。

prompt 资产见 prompts.py(逐字保值);user 模板的全角标点同样与上游逐字一致。
行为契约见 nl2sql/nl2sql-agent spec。
关键一跳:selfheal 把 validate/execute 的失败原因写入 state.error,
generate 读到 error 即拼「上次生成的 SQL 有误」喂回 LLM(自愈闭环)。
"""

import json
import logging
import re
import threading
from collections.abc import Sequence
from typing import cast

from kongming_agent import deps, numeric, sqlguard
from kongming_agent.apiclient import MetricHit, SchemaMeta
from kongming_agent.graph.budget import Segment, converge
from kongming_agent.graph.citations import sanitize_citations
from kongming_agent.graph.prompts import (
    SQL_GEN_SYSTEM,
    SUMMARIZE_SYSTEM,
    UNDERSTAND_MULTI_SYSTEM,
    UNDERSTAND_SINGLE_SYSTEM,
)
from kongming_agent.graph.state import AgentState, ExecutionResult, ValidationResult
from kongming_agent.graph.streams import emit_event
from kongming_agent.graph.table_prune import prune_schema
from kongming_agent.llm import LlmError
from kongming_agent.settings import Settings

# 080:装配预算分段优先级(0=不可切;小者先裁)。schema/history 殿后(无表结构
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
    """generate 的 user 提示八段装配 + 080 预算收敛(纯函数,可单测)。

    预算内:输出与既有装配逻辑逐字一致(零回归锚);超限:按优先级从尾部收敛
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
    # 装配格式与 080 前逐字同构(空段零痕迹;收敛后空文本走同一条件分支)
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
    """080 引入前的装配逻辑原样(测试零回归锚:预算充足时两侧逐字一致)。"""
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

# 043 生成纪律:空结构占位文案与哨兵机制联动(如实说明,严禁教唆编表)
_EMPTY_HINT = (
    "（未能检索到与问题相关的表结构；若问题所需数据不在可用范围内，"
    "只输出 NO_RELEVANT_TABLE——严禁臆测表名或列名。）"
)

# 043:知识未覆盖哨兵(生成侧如实说明的唯一出口);短路零执行零重试
# 046:语义补全两级——无支撑本问题的表(表级)/所需关键信息项无列且无法推导(列级)
_NO_DATA_SENTINEL = "NO_RELEVANT_TABLE"
_NO_DATA_ERROR = "当前数据源未找到回答该问题所需的数据,可切换数据源或调整问法后重试"


# ── 071 对话复语直通(闭合词表 + 全等判定,零依赖纯数据/纯函数)──
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
    # 071:复语直通——零模型调用、零检索、零执行,按既有第四态收敛;
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


def _metric_hit_dicts(hits: Sequence[MetricHit]) -> list[dict[str, str | None]]:
    """076:MetricHit 域对象 → state 字典投影(纯函数;空列表=零命中)。"""
    return [
        {
            "metric_id": h.metric_id,
            "metric_name": h.metric_name,
            "sql_template": h.sql_template,
            "updated_at": h.updated_at,
        }
        for h in hits
    ]


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
        # 047 多轮继承:最近 3 条历史透传(api 侧取末条走继承路补上轮依赖表);
        # 单轮为空列表,回调体不携带该键,链路与单轮逐字节同形
        history=list(state.get("history") or [])[-3:],
    )
    # 历史 SQL 独立于表说明命中(R2-S1):「知识未抓取但 query_history 已有成功
    # 记录」的真实形态下,schema 空回退内省、history 照常作 few-shot,不整体丢弃。
    history_sql = ctx.history_sql if ctx is not None else ""
    doc_hints = list(ctx.doc_hints) if ctx is not None else []
    # 076:结构化口径命中(空=无命中;与 metric_hints 文字段同源)
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
            out0["metric_hits"] = _metric_hit_dicts(ctx.metric_hits)
            out0["doc_hints"] = list(ctx.doc_hints)
            out0["skill_terms"] = ctx.skill_terms
            out0["skill_examples"] = ctx.skill_examples
            out0["skill_persona"] = ctx.skill_persona
        return out0
    # 063:表级预筛(问题+术语/示例/指标共现加权打分,top-N)。零命中=空表
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
    # R1-M2:回退内省形态仍透传语义段(metric_hints 按问句命中,不受 schema
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
    """自愈喂回分类(047):按 PG 错误形态生成针对性修正指令。

    043 旧行为对一切 does-not-exist 附表哨兵出口——列不存在会被引向弃答
    (表存在,出口是改列);泛化「请修正」使机械错同因连败(017 实证:
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
    # 022:记忆段(用户已知信息)位于表结构之前;空则形态与引入前逐字一致
    memory_hint = (state.get("memory_hint") or "").strip()
    # 023 语义段:表关系/指标口径位于表结构之后、历史 SQL 之前;空则零痕迹
    relations_hint = (state.get("relations_hint") or "").strip()
    metric_hints = (state.get("metric_hints") or "").strip()
    # 历史成功 SQL 段(009):召回命中时拼在表结构之后——人类验证过的 SQL 置信度
    # 高于表说明内嵌示例(强 few-shot);系统模板资产零改动,只动 user 拼接。
    history_sql = (state.get("history_sql") or "").strip()
    # 056 skill-pack 两段(可空;零段零痕迹零回归)
    skill_terms = (state.get("skill_terms") or "").strip()
    skill_examples = (state.get("skill_examples") or "").strip()
    extra = ""
    error = state.get("error") or ""
    if error:
        # 自愈:把上次错误喂回 LLM;047 按错误形态附加针对性修正指令
        # (别名/列/表分类;列错不引向表哨兵出口,机械错不再同因连败)
        extra = f"\n\n上次生成的 SQL 有误：{error}\n请修正。"
        guidance = _selfheal_guidance(error)
        if guidance:
            extra += guidance
    # 080:装配预算(窗口 − 预留输出 − 安全边际,按 SQL_GEN 生效模型解析)。
    # 预算内逐字零回归;超限按段优先级收敛(证据尾端首位豁免/纪律与问句不截);
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
    # 043:哨兵=模型如实说明知识未覆盖——短路产物直达失败终态,
    # 不进 validate/execute、不进自愈(零执行零重试;路由见 build._after_generate)
    if _is_no_data_sentinel(sql):
        return {"generated_sql": "", "error": _NO_DATA_ERROR, "end_reason": "no_data"}
    # 生成成功即清掉上轮错误(错误已消费)
    return {"generated_sql": clean_sql(sql), "error": ""}


# ── 节点4:本地预检(轻量四规则;权威校验在 api 防火墙,不复刻)──
# 053:解析走 liannu 引擎适配(模式化:影子对拍/切主/降级),判定语义不变
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


# ── 024 知识分流:意图词表 + doc_answer 节点 ──

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

# 047:强文档意图词表——「是什么」是泛系系词,数据追问(「这些影片各自的
# 分类是什么」)与文档问句同形;多轮会话在场时仅系词命中不分流(结构性
# 条件替代指代词表,036 弱信号词表两犯教训)
DOC_STRONG_INTENT_WORDS = tuple(w for w in KNOWLEDGE_INTENT_WORDS if w != "是什么")

# 036④:SQL 强意图词表(纯图片问题分流否决信号)——只收**几乎只在
# 问库出现**的强信号;通用量词(多少/数量/平均/最高/哪个)不收
# (真链路冒烟抓回:「这张图里哪个分类最高?数量是多少?」问图也带
# 「数量」,列入即误否决分流=答非所问复发)
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
    """047:强文档意图判定(纯函数)——泛系系词之外的显式文档词。"""
    return any(w in question for w in DOC_STRONG_INTENT_WORDS)


def is_sql_intent(question: str) -> bool:
    """036④:SQL 强意图判定(纯函数)——附件在场时命中即否决直答分流
    (「带图默认问图,带库词走 SQL」)。"""
    return any(w in question for w in SQL_INTENT_WORDS)


def route_after_retrieve(state: AgentState) -> str:
    """分流判定(spec「知识分流」):知识意图 + 文档命中即分流;
    schema 是否命中不作条件(024 冒烟实证:知识问题常有 schema 模糊命中,
    双条件过严——「退货政策是什么」召回 rental 表说明致不分流)。
    036④:附件在场且无 SQL 强意图→直答分流(纯图片问题不硬走 SQL)。
    047:多轮会话在场时,仅泛系系词「是什么」命中意图词表不分流——会话
    本身即数据语境,泛系系词不足以证明文档意图(浏览器实测:数据追问
    「这些影片各自的分类是什么」被文档通道劫持);强文档词照旧分流。"""
    doc_hints = state.get("doc_hints") or []
    # 下标:入口 state 恒写入 question(缺键属编程错误),保留下标求值序
    question = state.get("resolved_question")
    if not question:
        question = state["question"]  # pyright: ignore[reportTypedDictNotRequiredAccess]
    if doc_hints and is_knowledge_intent(question):
        if state.get("history") and not is_strong_doc_intent(question):
            return "generate"
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
        # 068:注入段标注=引用键(与净化合法集同源;source_ref 仅作块头说明)
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
    # 服务故障被表述为内容缺失,属伪造终态(审查循环 F4)
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
        # 068:确定性净化(零模型;与 057 numeric 收口同位置)——合法键集取自
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
        "sources": sources,  # 068:结构化来源(空=SQL 道/无命中)
    }


# 标识列名信号与期间词:与 api 侧 049 图表列角色画像同规则(跨进程各自落地的
# 纯值面小规则,注释互引防漂移;num 后缀刻意不入——order_num 类计数量是度量)
_STAT_ID_NAME = re.compile(r"(?:^|_)(?:id|no|code)$|^id_")
_STAT_PERIOD_NAMES = {"year", "quarter", "month", "week", "day"}


def _is_stat_worthy(col: str, values: list[int | float]) -> bool:
    """列是否具备统计度量资格(050):标识/期间名称信号,或整数稠密序列
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
    标识/期间列经 _is_stat_worthy 排除,050)。
    057:末两行环比/末值占比等派生量在时间列启发式成功时并入;失败不产出。"""
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
    # 056:人设段注入 summarize(只影响表述风格与禁令,不改安全纪律)
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
    # 057 数值纪律收口:确定性审计剥离非白名单数值句(不重试 LLM、不动 stats)
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
