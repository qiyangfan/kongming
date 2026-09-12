"""Kongming Agent 面入口(仅内网,内部令牌认证,ADR-002)。

业务端点:POST /query(NL2SQL 问答图,同步一次性返回)+
POST /query/stream(同图流式 SSE,017)+ /internal/embedding(批量向量化)+
/internal/describe-table(表元数据润色,009)——后两者供控制面回调
(api 持 DB、agent 持 LLM key 的分工)。
019 新增:POST /query_react + /query_react/stream(ReAct 工具调用内核,
工具 schema 由控制面按授权注入——agent 零工具真理源,零凭证回调执行)。
"""

import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import cast

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from langgraph.errors import GraphRecursionError
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse, Response

from kongming_agent import deps
from kongming_agent.describer import (
    ColumnSpec,
    DescribeError,
    TableSpec,
    describe_table,
)
from kongming_agent.graph.build import get_graph
from kongming_agent.graph.extract_prompt import EXTRACT_MEMORY_SYSTEM
from kongming_agent.graph.react import get_react_graph, react_recursion_limit
from kongming_agent.graph.react.state import ReactState
from kongming_agent.graph.state import AgentState, ExecutionResult, ValidationResult
from kongming_agent.llm import LlmError
from kongming_agent.settings import Settings

log = logging.getLogger(__name__)

_settings = Settings()
_settings.fail_closed_checks()

# 053:SQL 安全引擎启动自检(validate 预检解析底座;失败告警降级 builtin,不阻断)
from kongming_agent import sqlguard  # noqa: E402

sqlguard.startup_self_check(_settings.sqlguard_mode)

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
    """批量向量化请求(009;api 回调,问句/知识文本 → 向量)。"""

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
    """表结构润色请求(009;api 回调——内省产物,非业务行数据)。"""

    table_name: str
    schema_name: str
    table_comment: str = ""
    columns: list[DescribeColumnIn] = Field(min_length=1)


class DescribeTableOut(BaseModel):
    enriched: bool  # False=本次失败(不可润色),api 侧自行降级纯结构
    table_comment: str = ""
    columns_comment: dict[str, str] = {}
    sample_queries: list[str] = []
    alias: str = ""  # 037:业务称谓别名(enriched=True 时有效)


@app.post("/internal/describe-table", response_model=DescribeTableOut, tags=["internal"])
async def describe_table_endpoint(req: DescribeTableIn) -> DescribeTableOut:
    """LLM 润色表元数据;失败不抛 5xx(enriched=False),由 api 侧降级不阻断整批。"""
    spec = TableSpec(
        table_name=req.table_name,
        schema_name=req.schema_name,
        table_comment=req.table_comment,
        columns=[
            ColumnSpec(name=c.name, type=c.type, comment=c.comment) for c in req.columns
        ],
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
        alias=described.alias,  # 037
    )


class TitleIn(BaseModel):
    """会话标题改写请求(010;api 回调,首轮成功后异步触发)。"""

    question: str = Field(min_length=1, max_length=2000)


class TitleOut(BaseModel):
    rewritten: bool  # False=本次失败(降级),api 侧保持截断标题
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


class SuggestQuestionsIn(BaseModel):
    """建议问题生成请求(039;api 回调——结构摘要,非业务行数据)。"""

    digest: str = Field(min_length=1, max_length=20_000)


class SuggestQuestionsOut(BaseModel):
    questions: list[str] = []
    generated: bool  # False=本次失败(降级),api 侧收敛空清单不落缓存


@app.post("/internal/suggest-questions", response_model=SuggestQuestionsOut, tags=["internal"])
async def suggest_questions_endpoint(req: SuggestQuestionsIn) -> SuggestQuestionsOut:
    """LLM 建议问题生成(039);失败不抛 5xx(generated=False),api 侧降级。"""
    from kongming_agent.suggester import SuggestError, suggest_questions

    try:
        questions = await suggest_questions(req.digest, deps.get_llm())
    except SuggestError:
        return SuggestQuestionsOut(generated=False)
    return SuggestQuestionsOut(generated=True, questions=questions)


class ExtractMemoryIn(BaseModel):
    """记忆提取请求(022;api 回调,成功问答轮后台任务)。"""

    question: str = Field(min_length=1, max_length=2000)
    answer: str = Field(default="", max_length=2000)  # 摘要(截断后)


class ExtractMemoryOut(BaseModel):
    memories: list[dict[str, str]] = []  # [{content, category}] ≤3;空=无值得记忆




@app.get("/internal/models", tags=["internal"])
async def list_models() -> dict[str, list[str]]:
    """021 注册表模型名(025 Bot model 校验用;agent 唯一真理源)。"""
    return {"models": sorted(Settings().parse_registry().keys())}


@app.post("/internal/extract-memory", response_model=ExtractMemoryOut, tags=["internal"])
async def extract_memory_endpoint(req: ExtractMemoryIn) -> ExtractMemoryOut:
    """LLM 记忆提取(022 隐式路径;全新撰写 prompt,非上游逐字资产);
    失败/畸形收敛空数组(api 侧静默跳过)。"""
    from kongming_agent.graph.nodes import parse_summary_json

    try:
        raw = await deps.get_llm().chat(
            EXTRACT_MEMORY_SYSTEM,
            f"用户问题:{req.question}\n助手答复:{req.answer[:1000]}\n\n输出 JSON:",
            temperature=0.1,
            max_tokens=300,
            task="EXTRACT",  # 021:任务路由(缺省主模型)
        )
    except LlmError:
        return ExtractMemoryOut()
    parsed = parse_summary_json(raw)
    out: list[dict[str, str]] = []
    memories_raw = parsed.get("memories")
    # isinstance 收窄产物元素型仍 Unknown,守卫后 cast 再用(判定序不变)
    for item in cast("list[object]", memories_raw if isinstance(memories_raw, list) else []):
        typed = cast("dict[str, object]", item) if isinstance(item, dict) else None
        if (
            typed is not None
            and isinstance(typed.get("content"), str)
            and cast("str", typed["content"]).strip()
        ):
            out.append(
                {
                    "content": cast("str", typed["content"]).strip()[:500],
                    "category": str(typed.get("category") or "general")[:32],
                }
            )
    return ExtractMemoryOut(memories=out[:3])


class QueryIn(BaseModel):
    """控制面 → agent 的问答请求(nl2sql spec「控制面回调 Agent 面」)。"""

    trace_id: str = Field(min_length=1, max_length=64)  # 与审计同宽,全程贯穿
    tenant_key: str = Field(min_length=1, max_length=64)
    question: str = Field(min_length=1)
    user_id: str | None = None  # 缺省=系统调用(控制面豁免语义见 sql-execution spec)
    history: list[str] = []  # user 问句列表,旧→新
    datasource_key: str = "default"
    # 022:召回的用户已知信息段(控制面注入;缺省/空=零回归)
    memory_hint: str = ""
    # 030:图片附件引用(一次性消费;空=纯文本零回归)
    attachment_ids: list[str] = []


class QueryOut(BaseModel):
    success: bool
    verdict: str | None = None  # PASS / BLOCKED / ERROR / NO_DATA(044 如实说明)
    # None=未生成 SQL(LLM 故障——044 起 no_data 短路判别为 NO_DATA,不再落 None)
    summary: str = ""
    insights: list[dict[str, str]] = []
    stats: dict[str, object] = {}
    columns: list[str] = []
    rows: list[list[object]] = []
    sql: str = ""
    retry_count: int = 0
    error: str | None = None
    # 035:bot 链路请求内同步采集(纯增;仅 /query_react 挂收集器时非空)
    usage_records: list[dict[str, object]] = []


@app.post("/query", response_model=QueryOut, tags=["agent"])
async def query(req: QueryIn) -> QueryOut:
    from kongming_agent.multimodal import AttachmentError

    try:
        req.question = await _combine_with_attachments(req)  # 030(空附件零改动)
    except AttachmentError as e:
        return QueryOut(success=False, verdict="ERROR", summary="", error=str(e))
    final = await _run_graph(req)
    return _build_query_out(final)


async def _combine_with_attachments(req: QueryIn) -> str:
    """030:图片附件预处理(进图前,图结构零改动);fail-closed 任一件失败
    即 AttachmentError;纯文本零调用零回归。"""
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
    return {
        "trace_id": req.trace_id,
        "tenant_key": req.tenant_key,
        "question": req.question,
        "user_id": req.user_id,
        "history": req.history,
        "datasource_key": req.datasource_key,
        "retry_count": 0,
        "memory_hint": req.memory_hint,  # 022
        "has_attachments": bool(req.attachment_ids),  # 036④:直答分流信号
    }


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
        # LLM 故障(key 缺失/超时/协议异常):链路无法推进,友好返回不裸 500
        return {"llm_unavailable": True}
    return final


def _build_query_out(final: dict[str, object]) -> QueryOut:
    """终态构造(同步/流式共用,单一定义防字段漂移;nl2sql-agent spec)。"""
    if final.get("llm_unavailable"):
        return QueryOut(success=False, error="LLM 服务暂不可用,请稍后重试")
    execution = final.get("execution")
    validation = final.get("validation")
    verdict: str | None
    if final.get("end_reason") == "no_data":
        # 044:知识未覆盖如实说明(短路,未执行)——判定最前:自愈轮短路时
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
    # error 非空 ⇒ 恒非成功终态(轮次触顶携带部分结果的形态:execution
    # 可能 PASS 但 error 优先——A-B6;其余既有形态 error 与非 PASS 并存,零影响)
    has_error = isinstance(error, str) and bool(error)
    return QueryOut(
        success=(
            isinstance(execution, ExecutionResult)
            and execution.verdict == "PASS"
            and not has_error
        ),
        verdict=verdict,
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
    """标准 SSE 帧:event 行 + 单行 data + 空行分帧(017 事件契约)。"""
    return f"event: {event}\ndata: {data_json}\n\n"


@app.post("/query/stream", tags=["agent"])
async def query_stream(req: QueryIn) -> StreamingResponse:
    """问答图流式端点(017):custom 通道=节点过程事件,updates 通道累积终态;
    result 终态与同步 /query 同构(共用 _build_query_out);LLM 故障同同步
    口径走 result(success=false);意外异常收敛 error 终态不裸 500。"""
    from kongming_agent.multimodal import AttachmentError

    try:
        req.question = await _combine_with_attachments(req)  # 030
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
                        yield _sse_frame(
                            event, json.dumps(ev.get("data"), ensure_ascii=False)
                        )
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


# ── 019:ReAct 工具调用内核(与 8 节点图并存,终态同构) ──


class QueryReactIn(QueryIn):
    """ReAct 端点请求:QueryIn 字段 + api 注入的授权工具 schema(唯一真理源=api)。

    tools 为空列表时 LLM 无工具可用,退化为纯文本问答(成功终态,不报错)。
    """

    tools: list[dict[str, object]] = []
    # 025:Bot 系统提示(身份/场景段;内核纪律后缀由 prompts 强制拼接)
    bot_system_prompt: str = ""
    # 025:Bot 模型覆盖(021 注册表名;空=任务路由/主模型)
    model: str = ""
    # 025:渠道标记(审计平铺给 tool/execute)
    channel: str = "chat"
    # 035:挂 request 级 usage 收集器(仅 bot 链路置真——chat 同步 react/
    # workflow 缺省 False 走 fire-and-forget,R1-S2 防用量静默整丢)
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
        "memory_hint": req.memory_hint,  # 022
        "bot_system_prompt": req.bot_system_prompt,  # 025
        "model_override": req.model,  # 025
        "channel": req.channel,  # 025
    }


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
    """ReAct 内核同步端点;终态与 /query 逐字段同构(_build_query_out 单一定义)。
    035:collect_usage=True(bot 链路)才挂 request 级收集器——响应携带明细
    (bot 渠道唯一上报口,api 请求内落库);缺省 False(chat 同步 react/
    workflow)不挂,fire-and-forget 旧路零回归(R1-S2)。"""
    from kongming_agent.llm import reset_usage_collector, set_usage_collector
    from kongming_agent.multimodal import AttachmentError

    collector: list[dict[str, object]] = []
    token = set_usage_collector(collector) if req.collect_usage else None
    try:
        req.question = await _combine_with_attachments(req)  # 030(与 /query 同前段)
        final = await _run_react(req)
    except AttachmentError as e:
        # 035⑧ 清偿:早返也携带已收集记录(多附件部分成功后失败场景,
        # 第 1 张图 vision 用量已入收集器,不随早返丢弃)
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

    035:collect_usage=True 时挂 request 级收集器(与同步 /query_react 同口径
    ——审查循环 B7:曾静默忽略该字段,bot 渠道一旦流式化用量将静默回退
    fire-and-forget);result 终态帧携带 usage_records。"""
    from kongming_agent.llm import reset_usage_collector, set_usage_collector
    from kongming_agent.multimodal import AttachmentError

    collector: list[dict[str, object]] = []

    async def gen() -> AsyncIterator[str]:
        # 收集器在生成器上下文内挂载/卸载:流式响应的迭代任务与端点 handler
        # 分属不同上下文,端点级 set 不传播到 gen(同步端点同款语义)。
        # 附件预处理同在 gen 内执行(R1-F2):vision 用量发生在 combine 段,
        # 挂载若晚于 combine 则该段收集缺位、用量绕过「响应携带」契约。
        token2 = set_usage_collector(collector) if req.collect_usage else None
        try:
            try:
                req.question = await _combine_with_attachments(req)  # 030
            except AttachmentError as e:
                fail = _build_query_out({"error": str(e), "success": False})
                fail.verdict = "ERROR"  # R2-B2:与同步端点早返同构(verdict 契约)
                fail.usage_records = collector  # 035⑧:早返携带已收集记录
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
                            yield _sse_frame(
                                event, json.dumps(ev.get("data"), ensure_ascii=False)
                            )
                    elif isinstance(chunk, dict):
                        typed = cast("dict[str, object]", chunk)
                        for delta in typed.values():
                            if isinstance(delta, dict):
                                final.update(cast("dict[str, object]", delta))
            except LlmError:
                unavailable = _build_query_out({"llm_unavailable": True})
                unavailable.usage_records = collector  # 038⑨:异常终态携带已收集记录
                yield _sse_frame("result", unavailable.model_dump_json())
                return
            except GraphRecursionError:
                # 图步数兜底(R1-S4:与同步同口径的结构化错误,不走泛异常)
                capped = _build_query_out(
                    {"error": "工具调用步数超限,请缩小问题范围后重试"}
                )
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
            out.usage_records = collector  # 035:bot 渠道流式化的同步入账口径
            yield _sse_frame("result", out.model_dump_json())
        finally:
            if token2 is not None:
                reset_usage_collector(token2)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
