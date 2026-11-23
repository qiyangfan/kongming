"""LangGraph 问答状态与结果模型。"""

from typing import NotRequired, TypedDict

from pydantic import BaseModel

# 意图路由取值域单源(常量放 state 纯数据模块:nodes/streams 共引,
# 避免 streams→nodes 反向依赖成环)。chitchat 为兜底态:完全不携带取数
# 意图的输入由语义判定收口(词表直通漏网的兜底层,spec「闲聊语义兜底判定」)
INTENT_DATA = "data"
INTENT_KNOWLEDGE = "knowledge"
INTENT_FUSED = "fused"
INTENT_CHITCHAT = "chitchat"
VALID_INTENTS: tuple[str, ...] = (INTENT_DATA, INTENT_KNOWLEDGE, INTENT_FUSED, INTENT_CHITCHAT)

# 文档父块合并与提示注入上限:原问 top-5 + 最多 3 条子查询增量
DOC_HINT_LIMIT = 8

# 兜底轮分类(诊断字段,随 QueryOut 回传、api 落库;CHITCHAT=复语直通或
# 语义判定闲聊,CLARIFY=歧义澄清早退)
FALLBACK_CHITCHAT = "CHITCHAT"
FALLBACK_CLARIFY = "CLARIFY"


class ExecutionResult(BaseModel):
    """控制面执行路由三态响应的 state 载体(与 sql-execution spec 对齐)。"""

    verdict: str  # PASS / BLOCKED / ERROR
    reason: str | None = None
    columns: list[str] = []
    rows: list[list[object]] = []
    duration_ms: int = 0


class ValidationResult(BaseModel):
    """validate 节点本地预检结果。permission_block=True 即权限类,直接终态不重试。"""

    passed: bool
    permission_block: bool = False
    reason: str = ""


class AgentState(TypedDict, total=False):
    trace_id: str
    tenant_key: str
    datasource_key: str
    user_id: NotRequired[str | None]
    question: str  # 原始问句(审计/喂回以此为准)
    history: list[str]  # 多轮上下文:user 问句列表,旧→新(指代消解用)
    resolved_question: NotRequired[str]  # understand 产物(指代消解后)
    schema_hint: NotRequired[str]  # retrieve 产物(拼好的 schema 提示文本)
    history_sql: NotRequired[str]  # retrieve 产物(召回的历史成功 SQL 段)
    generated_sql: NotRequired[str]  # generate 产物
    validation: NotRequired[ValidationResult]  # validate 产物(本地预检)
    execution: NotRequired[ExecutionResult]  # execute 产物(回调)
    error: NotRequired[str]  # selfheal 写入,喂回 generate
    retry_count: NotRequired[int]
    summary: NotRequired[str]
    insights: NotRequired[list[dict[str, str]]]
    stats: NotRequired[dict[str, object]]
    compute_values: NotRequired[list[float]]  # 本轮计算工具返回值(入白名单)
    end_reason: NotRequired[str]  # 终态分类(blocked 等)
    memory_hint: NotRequired[str]  # 召回的用户已知信息段(控制面注入)
    relations_hint: NotRequired[str]  # 召回集合内 JOIN path 段
    metric_hints: NotRequired[str]  # 命中指标口径段
    metric_hits: NotRequired[list[dict[str, str | None]]]  # 结构化口径命中(空=无命中)
    # 文档道结构化来源(citation_key/source_ref/doc_name;空=SQL 道/无命中)
    sources: NotRequired[list[dict[str, str]]]
    skill_terms: NotRequired[str]  # Skill Pack 术语段
    skill_examples: NotRequired[str]  # 黄金示例段(禁令前缀)
    fallback_type: NotRequired[str]  # 兜底轮分类(闲聊/澄清;诊断字段)
    # 上一轮兜底分类(控制面随载荷注入;CLARIFY 时本轮不再命中澄清,防循环)
    last_fallback: NotRequired[str]
    skill_persona: NotRequired[str]  # 人设段
    doc_hints: NotRequired[list[dict[str, str]]]  # 文档段(source_ref+content)
    # 意图帧产出的文档子查询(仅知识/融合态);retrieve 用于并行补检合并
    doc_subqueries: NotRequired[list[str]]
    has_attachments: NotRequired[bool]  # 当前请求是否含图片附件，用于问答分流。
    # 三态意图路由(route_intent 产物)。intent 缺席=LLM 失败/超时/低置信
    # 缺少 intent 时，分流会沿用规则词表并检查多轮上下文。
    intent: NotRequired[str]  # data / knowledge / fused(取值域见 VALID_INTENTS)
    intent_source: NotRequired[str]  # 判定来源 llm(诊断;只进 trace 不入响应)
    intent_confidence: NotRequired[float]  # LLM 置信(0~1)
    intent_reason: NotRequired[str]  # 判定依据一句话(入 trace)


def fused_takes_over(state: AgentState) -> bool:
    """融合道接手判定(单源谓词):intent=fused 且 SQL 通道 PASS 且结果
    非空且文档命中,四条件合取——_after_summarize 条件边、流式 summary 抑制
    与 fused_answer 前置复核三处共用(判据漂移即「数据稿被吞」或「双稿拼接」,
    故收敛单点)。"""
    if state.get("intent") != INTENT_FUSED:
        return False
    execution = state.get("execution")
    if execution is None or execution.verdict != "PASS" or not execution.rows:
        return False
    return bool(state.get("doc_hints"))
