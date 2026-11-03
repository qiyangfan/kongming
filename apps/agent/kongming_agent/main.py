"""Agent 内网服务入口(ADR-002):NL2SQL、ReAct、向量化、表元数据润色与
报告生成端点;所有端点由内部令牌保护。"""

import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, cast

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from langgraph.errors import GraphRecursionError
from langgraph.types import Command
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse, Response

from kongming_agent import deps
from kongming_agent.collab_wiring import make_collab_runner
from kongming_agent.describer import (
    ColumnSpec,
    DescribeError,
    TableSpec,
    describe_table,
)
from kongming_agent.graph.budget import PromptBudgetExceeded
from kongming_agent.graph.build import get_graph
from kongming_agent.graph.checkpoints import (
    reset_checkpoint_context,
    set_checkpoint_context,
    thread_id_for_task,
)
from kongming_agent.graph.extract_prompt import EXTRACT_MEMORY_SYSTEM
from kongming_agent.graph.react import get_react_graph, react_recursion_limit
from kongming_agent.graph.react.state import ReactState
from kongming_agent.graph.state import AgentState, ExecutionResult, ValidationResult
from kongming_agent.llm import LlmError
from kongming_agent.logging_setup import setup_logging
from kongming_agent.prompt_fingerprint import prompt_fingerprint
from kongming_agent.report_gen import run_report_generation
from kongming_agent.settings import Settings
from kongming_agent.task_gen import run_task as run_task_orchestration

log = logging.getLogger(__name__)

_settings = Settings()
_settings.fail_closed_checks()

# prompt 资产指纹(启动期一次计算,纯函数同源;任一字变化即换指纹,
# 跨重启稳定)——随每轮 QueryOut 携带,api 侧落 chat_message.prompt_version
_PROMPT_VERSION = prompt_fingerprint()

# SQL 安全引擎启动自检(validate 预检解析底座;失败告警降级 builtin,不阻断)
from kongming_agent import sqlguard  # noqa: E402

sqlguard.startup_self_check(_settings.sqlguard_mode)

setup_logging(level=_settings.log_level, json_logs=False)

app = FastAPI(
    title="Kongming Agent",
    version="0.1.0",
    description="Agent 面:零凭证、纯脑力。不直面用户流量。",
)


@app.middleware("http")
async def require_internal_token(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    if request.url.path == "/healthz":  # 存活探针豁免
        return await call_next(request)
    token = request.headers.get("X-Internal-Token", "")
    if not _settings.internal_token or token != _settings.internal_token:
        # middleware 中抛 HTTPException 会变 500,必须返回响应
        return JSONResponse(status_code=403, content={"detail": "internal channel closed"})
    return await call_next(request)


@app.get("/healthz", tags=["ops"])
async def healthz() -> dict[str, str]:
    return {"status": "ok", "app": _settings.app_name}


class EmbeddingIn(BaseModel):
    """批量向量化请求(api 回调,问句/知识文本 → 向量)。"""

    texts: list[str] = Field(min_length=1)


class EmbeddingOut(BaseModel):
    vectors: list[list[float]]


@app.post("/internal/embedding", response_model=EmbeddingOut, tags=["internal"])
async def embedding(req: EmbeddingIn) -> EmbeddingOut:
    """批量向量化:embedding key 仅 agent 面持有(ADR-002);上限由 llm 客户端校验。"""
    if len(req.texts) > _settings.embedding_max_texts:
        raise HTTPException(status_code=422, detail="texts 超单批上限")
    try:
        vectors = await deps.get_llm().embed(req.texts)
    except LlmError as e:
        # LLM 层失败收敛 503:api 侧按「embedding 不可用」降级(纯全文路/回写跳过)
        raise HTTPException(status_code=503, detail=f"embedding unavailable: {e}") from e
    return EmbeddingOut(vectors=vectors)


class DescribeColumnIn(BaseModel):
    name: str
    type: str = ""
    comment: str = ""


class DescribeTableIn(BaseModel):
    """表结构润色请求(api 回调——内省产物,非业务行数据)。"""

    table_name: str
    schema_name: str
    table_comment: str = ""
    columns: list[DescribeColumnIn] = Field(min_length=1)


class DescribeTableOut(BaseModel):
    enriched: bool  # False 时 API 使用原始表结构。
    table_comment: str = ""
    columns_comment: dict[str, str] = {}
    sample_queries: list[str] = []
    alias: str = ""  # 业务称谓别名(enriched=True 时有效)


@app.post("/internal/describe-table", response_model=DescribeTableOut, tags=["internal"])
async def describe_table_endpoint(req: DescribeTableIn) -> DescribeTableOut:
    """LLM 润色表元数据;失败不抛 5xx(enriched=False),由 api 侧降级不阻断整批。"""
    spec = TableSpec(
        table_name=req.table_name,
        schema_name=req.schema_name,
        table_comment=req.table_comment,
        columns=[ColumnSpec(name=c.name, type=c.type, comment=c.comment) for c in req.columns],
    )
    try:
        described = await describe_table(spec, deps.get_llm())
    except DescribeError:
        return DescribeTableOut(enriched=False)
    return DescribeTableOut(
        enriched=True,
        table_comment=described.table_comment,
        columns_comment=described.columns_comment,
        sample_queries=described.sample_queries,
        alias=described.alias,
    )


class TitleIn(BaseModel):
    """会话标题改写请求(api 回调,首轮成功后异步触发)。"""

    question: str = Field(min_length=1, max_length=2000)


class TitleOut(BaseModel):
    rewritten: bool  # False 时 API 保留截断标题。
    title: str = ""


@app.post("/internal/rewrite-title", response_model=TitleOut, tags=["internal"])
async def rewrite_title_endpoint(req: TitleIn) -> TitleOut:
    """LLM 会话标题改写;失败不抛 5xx(rewritten=False),api 侧保持截断标题。"""
    from kongming_agent.titler import TitleError, rewrite_title

    try:
        title = await rewrite_title(req.question, deps.get_llm())
    except TitleError:
        return TitleOut(rewritten=False)
    return TitleOut(rewritten=True, title=title)


class ScoreAnswerIn(BaseModel):
    """答案评审请求(api 评分后台任务回调,材料=问句+回答+证据装配)。"""

    material: str = Field(min_length=1, max_length=24_000)
    trace_id: str = Field(default="", max_length=64)  # 用量归因到本轮
    tenant_key: str = Field(default="", max_length=64)  # 用量租户维度


class ScoreAnswerOut(BaseModel):
    decomposed: bool  # False 时 API 不落评分(降级)。
    decomposition: dict[str, object] = {}
    score_model: str = ""  # 实际生效的评审模型名(审计归因)


@app.post("/internal/score-answer", response_model=ScoreAnswerOut, tags=["internal"])
async def score_answer_endpoint(req: ScoreAnswerIn) -> ScoreAnswerOut:
    """LLM 答案评审(只产出主张分解,分数由控制面重算);
    失败不抛 5xx(decomposed=False),api 侧不落评分仅记日志。"""
    from kongming_agent.scorer import ScoringError, score_answer

    try:
        decomposition = await score_answer(
            req.material,
            deps.get_llm(),
            trace_id=req.trace_id or None,
            tenant_key=req.tenant_key or None,
        )
    except ScoringError:
        return ScoreAnswerOut(decomposed=False)
    return ScoreAnswerOut(
        decomposed=True,
        decomposition=decomposition,
        score_model=deps.get_llm().routed_model("ANSWER_SCORING"),
    )


class SuggestQuestionsIn(BaseModel):
    """建议问题生成请求(api 回调——结构摘要,非业务行数据)。"""

    digest: str = Field(min_length=1, max_length=20_000)


class SuggestQuestionsOut(BaseModel):
    questions: list[str] = []
    generated: bool  # False 时 API 返回空清单且不缓存。


@app.post("/internal/suggest-questions", response_model=SuggestQuestionsOut, tags=["internal"])
async def suggest_questions_endpoint(req: SuggestQuestionsIn) -> SuggestQuestionsOut:
    """LLM 建议问题生成;失败不抛 5xx(generated=False),api 侧降级。"""
    from kongming_agent.suggester import SuggestError, suggest_questions

    try:
        questions = await suggest_questions(req.digest, deps.get_llm())
    except SuggestError:
        return SuggestQuestionsOut(generated=False)
    return SuggestQuestionsOut(generated=True, questions=questions)


class ExtractMemoryIn(BaseModel):
    """记忆提取请求(api 回调,成功问答轮后台任务)。"""

    question: str = Field(min_length=1, max_length=2000)
    answer: str = Field(default="", max_length=2000)  # 摘要(截断后)


class ExtractMemoryOut(BaseModel):
    # 槽位 patch 形态 [{preferred_datasource:"…"}];metric_aliases 可为数组
    memories: list[dict[str, object]] = []  # ≤3;空=无值得写入槽位


@app.get("/internal/models", tags=["internal"])
async def list_models() -> dict[str, list[str]]:
    """注册表模型名(Bot model 校验用;agent 唯一真理源)。"""
    return {"models": sorted(Settings().parse_registry().keys())}


class PromptFingerprintOut(BaseModel):
    """prompt 资产内容指纹(评估跨版本可比性判定)。"""

    prompt_fingerprint: str


@app.get("/internal/prompt-fingerprint", response_model=PromptFingerprintOut, tags=["internal"])
async def prompt_fingerprint_endpoint() -> PromptFingerprintOut:
    """prompt 资产全集内容指纹(控制面/评估器可取;共用同一纯函数)。

    受既有 X-Internal-Token 中间件守门(ADR-002 不变);纯函数直算,零状态。
    """
    return PromptFingerprintOut(prompt_fingerprint=prompt_fingerprint())


class GenerateReportIn(BaseModel):
    """报告生成编排请求(api 报告域后台任务回调,字段=编排素材)。"""

    report_id: str
    trace_id: str = ""
    tenant_key: str = "default"
    datasource_key: str = "default"
    user_id: str | None = None
    instruction: str = ""
    title: str = ""
    context_digest: str = ""


class TaskRunIn(BaseModel):
    """任务编排请求(api 任务域后台派发回调)。
    epoch=派发代际(0042 双执行者闸):编排全程携带,控制面失配即拦。"""

    task_id: str
    epoch: int = 0
    trace_root: str = ""
    tenant_key: str = "default"
    datasource_key: str = "default"
    user_id: str | None = None
    goal: str = ""
    budget: dict[str, object] = {}
    tools: list[dict[str, object]] = []


@app.post("/internal/task-run", tags=["internal"])
async def task_run(req: TaskRunIn) -> dict[str, object]:
    """任务编排:规划→逐步执行(单步有界 ReAct,证据驱动;
    声明协作拓扑的步骤走协作编排)→回写。agent 面零任务持久状态;
    审批/预算/暂停由控制面回调决策驱动。"""

    async def _run_step(question: str, extra: dict[str, object]) -> dict[str, object]:
        tools_raw = extra.get("tools")
        tools = cast("list[dict[str, object]]", tools_raw if isinstance(tools_raw, list) else [])
        req_inner = QueryReactIn(
            trace_id=str(req.trace_root or "task"),
            tenant_key=req.tenant_key,
            datasource_key=req.datasource_key,
            user_id=req.user_id,
            question=question,
            history=[],
            tools=tools,
            channel="task",  # 工具执行审计归因到任务编排(区别于交互问答)
        )
        # 任务步骤受限循环带线程上下文与恢复提示
        # (快照续跑/图内挂起/决策恢复;协作步骤不落此路)
        task_id = str(extra.get("task_id") or "")
        step_id = str(extra.get("step_id") or "")
        hint = extra.get("resume_confirm")
        if task_id and step_id:
            return await _run_react_task(
                req_inner,
                task_id=task_id,
                step_id=step_id,
                resume_confirm=hint if isinstance(hint, dict) else None,  # pyright: ignore[reportUnknownArgumentType]
            )
        return await _run_react(req_inner)

    # 组合根:协作步骤执行器(开账 fail-closed、worker=受限 ReAct、
    # 账本/用量/trace 归集;装配细节单源在 collab_wiring)。
    # 适配壳:collab_wiring 以 dict 载荷解耦 QueryReactIn(避免反向依赖本模块)
    async def _react_adapter(req: object) -> dict[str, object]:
        if isinstance(req, QueryReactIn):
            return await _run_react(req)
        payload = cast("dict[str, object]", req)
        return await _run_react(QueryReactIn(**payload))  # type: ignore[arg-type]

    collab_runner = make_collab_runner(
        api=deps.get_api(),
        llm=deps.get_llm(),
        settings=_settings,
        react_runner=_react_adapter,
        task_id=req.task_id,
        tenant_key=req.tenant_key,
        datasource_key=req.datasource_key,
        user_id=req.user_id,
        trace_root=str(req.trace_root or ""),
        tools=req.tools or [],
    )

    return await run_task_orchestration(
        payload=req.model_dump(),
        llm=deps.get_llm(),
        api=deps.get_api(),
        settings=_settings,
        step_runner=_run_step,
        collab_runner=collab_runner,
    )


@app.post("/internal/generate-report", tags=["internal"])
async def generate_report(req: GenerateReportIn) -> dict[str, object]:
    """报告生成编排:规划→逐叶取数(既有工具回调)→逐章撰写→组装。

    长任务:多轮 LLM+多次取数,httpx 侧由 api 以独立长超时等待;编排内部
    预算三约束(LLM 调用/token/墙钟)耗尽即停,PARTIAL 诚实收口。响应即
    编排终态载荷(sections 绑定含快照),持久化/版本化/终态落库在 api 侧。"""
    return await run_report_generation(
        payload=req.model_dump(),
        llm=deps.get_llm(),
        api=deps.get_api(),
        settings=_settings,
    )


@app.post("/internal/extract-memory", response_model=ExtractMemoryOut, tags=["internal"])
async def extract_memory_endpoint(req: ExtractMemoryIn) -> ExtractMemoryOut:
    """LLM 记忆提取(隐式路径;全新撰写 prompt,非上游逐字资产);
    失败/畸形收敛空数组(api 侧静默跳过)。"""
    from kongming_agent.graph.nodes import parse_summary_json

    try:
        raw = await deps.get_llm().chat(
            EXTRACT_MEMORY_SYSTEM,
            f"用户问题:{req.question}\n助手答复:{req.answer[:1000]}\n\n输出 JSON:",
            temperature=0.1,
            max_tokens=300,
            task="EXTRACT",  # 任务路由(缺省主模型)
        )
    except LlmError:
        return ExtractMemoryOut()
    parsed = parse_summary_json(raw)
    out: list[dict[str, object]] = []
    memories_raw = parsed.get("memories")
    # isinstance 收窄产物元素型仍 Unknown,守卫后 cast 再用(判定序不变)
    for item in cast("list[object]", memories_raw if isinstance(memories_raw, list) else []):
        typed = cast("dict[str, object]", item) if isinstance(item, dict) else None
        if typed is None:
            continue
        kept = {str(k): v for k, v in typed.items() if v is not None and v != "" and v != []}
        if kept:
            out.append(kept)
    return ExtractMemoryOut(memories=out[:3])


class QueryIn(BaseModel):
    """控制面 → agent 的问答请求(nl2sql spec「控制面回调 Agent 面」)。"""

    trace_id: str = Field(min_length=1, max_length=64)  # 与审计同宽,全程贯穿
    tenant_key: str = Field(min_length=1, max_length=64)
    question: str = Field(min_length=1)
    user_id: str | None = None  # 缺省=系统调用(控制面豁免语义见 sql-execution spec)
    history: list[str] = []  # user 问句列表,旧→新
    datasource_key: str = "default"
    # 控制面注入的用户记忆提示；为空时不注入。
    memory_hint: str = ""
    # 图片附件引用(一次性消费;空=纯文本)
    attachment_ids: list[str] = []
    # 上一轮兜底分类(澄清防循环判据;诊断字段,缺省不携带)
    last_fallback: str | None = None


class QueryOut(BaseModel):
    success: bool
    verdict: str | None = None  # PASS / BLOCKED / ERROR / NO_DATA(如实说明)
    # None=未生成 SQL(LLM 故障——起 no_data 短路判别为 NO_DATA,不再落 None)
    summary: str = ""
    insights: list[dict[str, str]] = []
    stats: dict[str, object] = {}
    columns: list[str] = []
    rows: list[list[object]] = []
    sql: str = ""
    retry_count: int = 0
    error: str | None = None
    # 管理面诊断信息由 API 落库；单轮问答未改写时为空。
    resolved_question: str | None = None
    # prompt 资产指纹(启动期计算;纯函数同源,前 8 位)
    prompt_version: str | None = None
    # 语义层口径命中(空=无命中;api 侧随 result_json 落库+回显)
    metric_hits: list[dict[str, str | None]] = []
    # 文档道结构化来源(空=SQL 道/无命中;落库+回显)
    sources: list[dict[str, str]] = []
    # 兜底轮分类(CHITCHAT/CLARIFY;管理面诊断,api 落库不进用户响应)
    fallback_type: str | None = None
    # 仅挂载用量收集器的 /query_react 请求会返回明细。
    usage_records: list[dict[str, object]] = []


@app.post("/query", response_model=QueryOut, tags=["agent"])
async def query(req: QueryIn) -> QueryOut:
    from kongming_agent.multimodal import AttachmentError

    try:
        req.question = await _combine_with_attachments(req)  # (空附件零改动)
    except AttachmentError as e:
        return QueryOut(success=False, verdict="ERROR", summary="", error=str(e))
    final = await _run_graph(req)
    return _build_query_out(final)


async def _combine_with_attachments(req: QueryIn) -> str:
    """图片附件预处理(进图前,图结构零改动);fail-closed 任一件失败
    即 AttachmentError;纯文本零调用。"""
    from kongming_agent.multimodal import combine_question, preprocess_attachments

    prefix = await preprocess_attachments(
        req.attachment_ids,
        tenant_key=req.tenant_key,
        user_id=req.user_id,
        api=deps.get_api(),
        llm=deps.get_llm(),
        trace_id=req.trace_id,
    )
    return combine_question(prefix, req.question)


def _initial_state(req: QueryIn) -> AgentState:
    state: AgentState = {
        "trace_id": req.trace_id,
        "tenant_key": req.tenant_key,
        "question": req.question,
        "user_id": req.user_id,
        "history": req.history,
        "datasource_key": req.datasource_key,
        "retry_count": 0,
        "memory_hint": req.memory_hint,
        "has_attachments": bool(req.attachment_ids),  # 图片附件会影响问答分流。
    }
    if req.last_fallback:
        state["last_fallback"] = req.last_fallback  # 澄清防循环判据(缺省不带)
    return state


async def _run_graph(req: QueryIn) -> dict[str, object]:
    """图执行 + LLM 故障收敛(同步/流式同口径:key 缺失/超时等友好返回)。"""
    try:
        graph = get_graph()
        # langgraph 成员签名自带库侧 Unknown,访问点豁免;运行时即 dict(边界 cast)
        final = cast(
            "dict[str, object]",
            await graph.ainvoke(  # pyright: ignore[reportUnknownMemberType]
                _initial_state(req)
            ),
        )
    except LlmError:
        # LLM 配置、网络或协议异常时返回可读错误，不抛出 500。
        return {"llm_unavailable": True}
    except PromptBudgetExceeded:
        # 提示词装配超预算(不可切段本身超窗)——可归因失败,禁假托
        # 「服务暂不可用」(真实原因是我方装配,运维按服务故障查不到东西)
        return {"prompt_budget_exceeded": True}
    return final


def _build_query_out(final: dict[str, object]) -> QueryOut:
    """终态构造(同步/流式共用,单一定义防字段漂移;nl2sql-agent spec)。"""
    if final.get("llm_unavailable"):
        return QueryOut(success=False, error="LLM 服务暂不可用,请稍后重试")
    if final.get("prompt_budget_exceeded"):
        # 如实归因——不可切段(纪律/问句/自愈喂回)本身已超模型窗口,
        # 自动收敛救不回来的配置型失败
        return QueryOut(
            success=False,
            verdict="ERROR",
            error=(
                "本轮问题的上下文已超出模型可处理范围(自动收敛后仍无法容纳),"
                "请缩小问题范围,或联系管理员调整模型上下文窗口配置"
            ),
        )
    execution = final.get("execution")
    validation = final.get("validation")
    verdict: str | None
    if final.get("end_reason") == "no_data":
        # 知识未覆盖如实说明(短路,未执行)——判定最前:自愈轮短路时
        # state 残留上轮 execution(ERROR),不得遮蔽最终形态
        verdict = "NO_DATA"
    elif isinstance(execution, ExecutionResult):
        verdict = execution.verdict
    elif isinstance(validation, ValidationResult) and validation.permission_block:
        verdict = "BLOCKED"  # 本地预检拦下的写操作,未达执行路由
    else:
        verdict = None
    summary = final.get("summary", "")
    insights = final.get("insights", [])
    stats = final.get("stats", {})
    sql = final.get("generated_sql", "")
    retry = final.get("retry_count", 0)
    error = final.get("error")
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
    # error 非空 ⇒ 恒非成功终态(轮次触顶携带部分结果的形态:execution
    # 可能 PASS 但 error 优先;其余既有形态 error 与非 PASS 并存,零影响)
    has_error = isinstance(error, str) and bool(error)
    # 兜底轮分类(仅兜底早退轮非空;诊断字段,api 落库不进用户响应)
    fallback_raw = final.get("fallback_type")
    fallback = fallback_raw if isinstance(fallback_raw, str) and fallback_raw else None
    return QueryOut(
        success=(
            isinstance(execution, ExecutionResult) and execution.verdict == "PASS" and not has_error
        ),
        verdict=verdict,
        resolved_question=resolved,
        prompt_version=_PROMPT_VERSION,
        metric_hits=metric_hits,
        sources=sources,
        fallback_type=fallback,
        summary=summary if isinstance(summary, str) else "",
        # isinstance 守卫后 cast 收窄(守卫产物元素/键型仍 Unknown;cast 运行时恒等)
        insights=cast("list[dict[str, str]]", insights) if isinstance(insights, list) else [],
        stats=cast("dict[str, object]", stats) if isinstance(stats, dict) else {},
        columns=execution.columns if isinstance(execution, ExecutionResult) else [],
        rows=execution.rows if isinstance(execution, ExecutionResult) else [],
        sql=sql if isinstance(sql, str) else "",
        retry_count=retry if isinstance(retry, int) else 0,
        error=str(error) if isinstance(error, str) and error else None,
    )


def _sse_frame(event: str, data_json: str) -> str:
    """标准 SSE 帧:event 行 + 单行 data + 空行分帧(事件契约)。"""
    return f"event: {event}\ndata: {data_json}\n\n"


@app.post("/query/stream", tags=["agent"])
async def query_stream(req: QueryIn) -> StreamingResponse:
    """问答图流式端点:custom 通道=节点过程事件,updates 通道累积终态;
    result 终态与同步 /query 同构(共用 _build_query_out);LLM 故障同同步
    口径走 result(success=false);意外异常收敛 error 终态不裸 500。"""
    from kongming_agent.multimodal import AttachmentError

    try:
        req.question = await _combine_with_attachments(req)
    except AttachmentError as e:
        # 预处理失败:单 result 终态帧收敛(与 LLM 故障同口径,不裸 500)
        fail = _build_query_out({"error": str(e), "success": False})

        async def _fail_gen() -> AsyncIterator[str]:
            yield _sse_frame("result", fail.model_dump_json())

        return StreamingResponse(_fail_gen(), media_type="text/event-stream")
    state = _initial_state(req)

    async def gen() -> AsyncIterator[str]:
        final: dict[str, object] = {}
        try:
            graph = get_graph()
            # langgraph 成员签名自带库侧 Unknown,访问点豁免;chunk 守卫后收窄
            async for mode, chunk in graph.astream(  # pyright: ignore[reportUnknownMemberType]
                state, stream_mode=["updates", "custom"]
            ):
                if mode == "custom":
                    ev = cast("dict[str, object]", chunk) if isinstance(chunk, dict) else {}
                    event = str(ev.get("event") or "")
                    if event:
                        yield _sse_frame(event, json.dumps(ev.get("data"), ensure_ascii=False))
                elif isinstance(chunk, dict):
                    typed = cast("dict[str, object]", chunk)
                    for delta in typed.values():
                        if isinstance(delta, dict):
                            # updates 累积=终态 state
                            final.update(cast("dict[str, object]", delta))
        except LlmError:
            yield _sse_frame(
                "result",
                _build_query_out({"llm_unavailable": True}).model_dump_json(),
            )
            return
        except PromptBudgetExceeded:
            yield _sse_frame(
                "result",
                _build_query_out({"prompt_budget_exceeded": True}).model_dump_json(),
            )
            return
        except Exception:
            log.exception("流式问答图执行异常 trace_id=%s", req.trace_id)
            yield _sse_frame(
                "error",
                json.dumps(
                    {"error": "问答服务内部错误,请稍后重试", "reason_code": None},
                    ensure_ascii=False,
                ),
            )
            return
        yield _sse_frame("result", _build_query_out(final).model_dump_json())

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── ReAct 工具调用内核(与 8 节点图并存,终态同构) ──


class QueryReactIn(QueryIn):
    """ReAct 端点请求:QueryIn 字段 + api 注入的授权工具 schema(唯一真理源=api)。

    tools 为空列表时 LLM 无工具可用,退化为纯文本问答(成功终态,不报错)。
    """

    tools: list[dict[str, object]] = []
    # Bot 系统提示(身份/场景段;内核纪律后缀由 prompts 强制拼接)
    bot_system_prompt: str = ""
    # Bot 模型覆盖(注册表名;空=任务路由/主模型)
    model: str = ""
    # 渠道标记(审计平铺给 tool/execute)
    channel: str = "chat"
    # 仅 Bot 请求启用同步采集；普通 chat 和 workflow 保持异步上报。
    collect_usage: bool = False


def _initial_react_state(req: QueryReactIn) -> ReactState:
    return {
        "trace_id": req.trace_id,
        "tenant_key": req.tenant_key,
        "question": req.question,
        "user_id": req.user_id,
        "history": req.history,
        "datasource_key": req.datasource_key,
        "tools": req.tools,
        "rounds": 0,
        "memory_hint": req.memory_hint,
        "bot_system_prompt": req.bot_system_prompt,
        "model_override": req.model,
        "channel": req.channel,
    }


def _interrupt_confirm_id(tup: object) -> str:
    """从检查点元组提取在途确认标识(恢复提示复合校验的安全闸):挂起写入行
    __interrupt__ 携带 interrupt 载荷。读不出/解析失败=空串——宁可重挂等待
    该确认自身的处置,不可把旧决策套到新在途确认上(未决动作被错兑=越权)。"""
    try:
        pending: Any = cast("Any", getattr(tup, "pending_writes", None)) or []
        for w in pending:
            if str(w[1]) != "__interrupt__":
                continue
            value: Any = w[2]
            # 反序列化恢复的挂起值为 Interrupt 元组(兼容裸 dict/单对象形态)
            for it in cast("list[Any]", value if isinstance(value, (list, tuple)) else [value]):
                payload = getattr(it, "value", it)
                d = cast("dict[str, Any]", payload) if isinstance(payload, dict) else None
                if d is not None:
                    cid = str(d.get("confirm_id") or "")
                    if cid:
                        return cid
    except Exception:  # noqa: BLE001 —— 判读失败按无在途处理(保守侧)
        log.warning("在途确认判读失败,恢复提示按失配处理", exc_info=True)
    return ""


async def _run_react_task(
    req: QueryReactIn,
    *,
    task_id: str,
    step_id: str,
    resume_confirm: dict[str, object] | None,
) -> dict[str, object]:
    """任务通道受限循环(检查点形态):线程确定性派生,
    推进点快照落控制面;已决确认经 Command(resume) 从挂起点精确恢复;
    线程既有快照(无确认挂起)时从中断处续跑(快照缺席回退整步重跑)。
    恢复方式随终态携带(fresh/snapshot),编排如实入步骤账,不谎称首次执行。"""
    graph = cast("Any", get_react_graph(checkpointed=True))
    thread_id = thread_id_for_task(task_id, step_id)
    set_checkpoint_context(
        tenant_key=req.tenant_key,
        task_id=task_id,
        step_id=step_id,
        trace_id=req.trace_id,
    )
    config = {
        "recursion_limit": react_recursion_limit(),
        "configurable": {"thread_id": thread_id},
    }
    origin = "fresh"
    try:
        has_thread = False
        existing: object = None
        if graph.checkpointer is not None:
            existing = await graph.checkpointer.aget_tuple(config)
            has_thread = existing is not None
        # 恢复提示复合校验(安全红线):提示 confirm_id 必须与快照在途确认一致
        # 才兑现——线程内先后挂起过多个确认时,旧决策不得套到新在途确认上
        # (未决动作被他人决策兑现=越权执行)。失配/无法判读一律视为无决策,
        # 走快照续跑:confirm_gate 原地重挂,同一确认行幂等复用,等待该确认
        # 自身的处置
        effective_resume: dict[str, object] | None = None
        if resume_confirm is not None:
            inflight_id = _interrupt_confirm_id(existing) if existing is not None else ""
            if inflight_id and str(resume_confirm.get("confirm_id") or "") == inflight_id:
                effective_resume = {
                    "decision": str(resume_confirm.get("decision") or ""),
                    "confirm_id": inflight_id,
                }
        try:
            if effective_resume is not None:
                origin = "snapshot"
                final = await graph.ainvoke(Command(resume=effective_resume), config=config)
            elif has_thread:
                # 快照续跑(步骤内中断恢复);若快照停在确认点且无决策/决策失配,
                # confirm_gate 会再次挂起(同一确认行幂等复用)
                origin = "snapshot"
                final = await graph.ainvoke(None, config=config)
            else:
                final = await graph.ainvoke(_initial_react_state(req), config=config)
        except Exception:
            if effective_resume is not None or has_thread:
                # 恢复路径异常(线程态漂移/无挂起点可续):如实回退整步重跑,
                # 不向账本谎称恢复成功
                log.warning("检查点恢复失败,回退整步重跑: thread=%s", thread_id, exc_info=True)
                origin = "fresh"
                final = await graph.ainvoke(_initial_react_state(req), config=config)
            else:
                raise
    except GraphRecursionError:
        return {"error": "工具调用步数超限,请缩小问题范围后重试"}
    except LlmError:
        return {"llm_unavailable": True}
    finally:
        reset_checkpoint_context()
    final_d = cast("dict[str, object]", final)
    interrupts = final_d.get("__interrupt__")
    if interrupts:
        # 图内挂起(ainvoke 返回挂起态而非抛出):提取确认载荷交编排上账
        payload: dict[str, object] = {}
        first = cast("Any", interrupts[0]) if isinstance(interrupts, list) else None
        value = getattr(first, "value", None)
        if isinstance(value, dict):
            payload = cast("dict[str, object]", value)
        return {
            "suspended": {
                "confirm_id": str(payload.get("confirm_id") or ""),
                "tool_name": str(payload.get("tool_name") or ""),
                "summary": str(payload.get("summary") or ""),
            }
        }
    out = dict(final_d)
    out["resume_origin"] = origin
    return out


async def _run_react(req: QueryReactIn) -> dict[str, object]:
    """ReAct 图执行 + 故障收敛(与 _run_graph 同口径)。"""
    try:
        graph = get_react_graph()
        # langgraph 成员签名自带库侧 Unknown,访问点豁免;运行时即 dict(边界 cast)
        final = cast(
            "dict[str, object]",
            await graph.ainvoke(  # pyright: ignore[reportUnknownMemberType]
                _initial_react_state(req),
                config={"recursion_limit": react_recursion_limit()},
            ),
        )
    except GraphRecursionError:
        # 图步数兜底(不应触发:轮次上限先于 recursion_limit 收束)
        return {"error": "工具调用步数超限,请缩小问题范围后重试"}
    except LlmError:
        return {"llm_unavailable": True}
    return final


@app.post("/query_react", response_model=QueryOut, tags=["agent"])
async def query_react(req: QueryReactIn) -> QueryOut:
    """执行 ReAct 问答并返回与 /query 同形的结果；启用用量采集时随响应返回明细。"""
    from kongming_agent.llm import reset_usage_collector, set_usage_collector
    from kongming_agent.multimodal import AttachmentError

    collector: list[dict[str, object]] = []
    token = set_usage_collector(collector) if req.collect_usage else None
    try:
        req.question = await _combine_with_attachments(req)  # 与普通问答共用附件处理。
        final = await _run_react(req)
    except AttachmentError as e:
        # 部分图片处理成功后仍保留已收集的用量记录。
        out = QueryOut(success=False, verdict="ERROR", summary="", error=str(e))
        out.usage_records = collector
        return out
    finally:
        if token is not None:
            reset_usage_collector(token)
    out = _build_query_out(final)
    out.usage_records = collector
    return out


@app.post("/query_react/stream", tags=["agent"])
async def query_react_stream(req: QueryReactIn) -> StreamingResponse:
    """ReAct 流式端点:stage/tool_call/summary 过程事件 + result 终态(同构)。

    collect_usage=True 时挂 request 级收集器,与同步 /query_react 保持一致。
    流式接口也必须启用收集器,否则 bot 用量不会写入 usage_records。
    fire-and-forget);result 终态帧携带 usage_records。"""
    from kongming_agent.llm import reset_usage_collector, set_usage_collector
    from kongming_agent.multimodal import AttachmentError

    collector: list[dict[str, object]] = []

    async def gen() -> AsyncIterator[str]:
        # 收集器在生成器上下文内挂载/卸载:流式响应的迭代任务与端点 handler
        # 分属不同上下文,端点级 set 不传播到 gen(同步端点同款语义)。
        # 附件预处理同在 gen 内执行:vision 用量发生在 combine 段,
        # 挂载若晚于 combine 则该段收集缺位、用量绕过「响应携带」契约。
        token2 = set_usage_collector(collector) if req.collect_usage else None
        try:
            try:
                req.question = await _combine_with_attachments(req)
            except AttachmentError as e:
                fail = _build_query_out({"error": str(e), "success": False})
                fail.verdict = "ERROR"  # 与同步端点早返同构(verdict 契约)
                fail.usage_records = collector  # 保留早返前已收集的用量。
                yield _sse_frame("result", fail.model_dump_json())
                return
            state = _initial_react_state(req)
            final: dict[str, object] = {}
            try:
                graph = get_react_graph()
                # langgraph 成员签名自带库侧 Unknown,访问点豁免;chunk 守卫后收窄
                async for mode, chunk in graph.astream(  # pyright: ignore[reportUnknownMemberType]
                    state,
                    stream_mode=["updates", "custom"],
                    config={"recursion_limit": react_recursion_limit()},
                ):
                    if mode == "custom":
                        ev = cast("dict[str, object]", chunk) if isinstance(chunk, dict) else {}
                        event = str(ev.get("event") or "")
                        if event:
                            yield _sse_frame(event, json.dumps(ev.get("data"), ensure_ascii=False))
                    elif isinstance(chunk, dict):
                        typed = cast("dict[str, object]", chunk)
                        for delta in typed.values():
                            if isinstance(delta, dict):
                                final.update(cast("dict[str, object]", delta))
            except LlmError:
                unavailable = _build_query_out({"llm_unavailable": True})
                unavailable.usage_records = collector  # 异常终态也返回已收集的用量。
                yield _sse_frame("result", unavailable.model_dump_json())
                return
            except GraphRecursionError:
                # 图步数兜底(与同步同口径的结构化错误,不走泛异常)
                capped = _build_query_out({"error": "工具调用步数超限,请缩小问题范围后重试"})
                capped.usage_records = collector  # 异常终态同携带
                yield _sse_frame("result", capped.model_dump_json())
                return
            except Exception:
                log.exception("ReAct 流式执行异常 trace_id=%s", req.trace_id)
                yield _sse_frame(
                    "error",
                    json.dumps(
                        {"error": "问答服务内部错误,请稍后重试", "reason_code": None},
                        ensure_ascii=False,
                    ),
                )
                return
            out = _build_query_out(final)
            out.usage_records = collector  # bot 渠道流式化的同步入账口径
            yield _sse_frame("result", out.model_dump_json())
        finally:
            if token2 is not None:
                reset_usage_collector(token2)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
