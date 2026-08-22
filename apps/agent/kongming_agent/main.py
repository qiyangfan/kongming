"""Kongming Agent 面入口(仅内网,内部令牌认证,ADR-002)。

业务端点:POST /query(NL2SQL 问答图,同步一次性返回)+
POST /query/stream(同图流式 SSE,017)+ /internal/embedding(批量向量化)+
/internal/describe-table(表元数据润色,009)——后两者供控制面回调
(api 持 DB、agent 持 LLM key 的分工)。
"""

import json
import logging
from collections.abc import AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse

from kongming_agent import deps
from kongming_agent.describer import (
    ColumnSpec,
    DescribeError,
    TableSpec,
    describe_table,
)
from kongming_agent.graph.build import get_graph
from kongming_agent.graph.state import AgentState, ExecutionResult, ValidationResult
from kongming_agent.llm import LlmError
from kongming_agent.settings import Settings

log = logging.getLogger(__name__)

_settings = Settings()
_settings.fail_closed_checks()

app = FastAPI(
    title="Kongming Agent",
    version="0.1.0",
    description="Agent 面:零凭证、纯脑力。不直面用户流量。",
)


@app.middleware("http")
async def require_internal_token(request: Request, call_next):  # type: ignore[no-untyped-def]
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


class QueryIn(BaseModel):
    """控制面 → agent 的问答请求(nl2sql spec「控制面回调 Agent 面」)。"""

    trace_id: str = Field(min_length=1, max_length=64)  # 与审计同宽,全程贯穿
    tenant_key: str = Field(min_length=1, max_length=64)
    question: str = Field(min_length=1)
    user_id: str | None = None  # 缺省=系统调用(控制面豁免语义见 sql-execution spec)
    history: list[str] = []  # user 问句列表,旧→新
    datasource_key: str = "default"


class QueryOut(BaseModel):
    success: bool
    verdict: str | None = None  # PASS / BLOCKED / ERROR;LLM 故障未生成 SQL 时为 None
    summary: str = ""
    insights: list[dict[str, str]] = []
    stats: dict[str, object] = {}
    columns: list[str] = []
    rows: list[list[object]] = []
    sql: str = ""
    retry_count: int = 0
    error: str | None = None


@app.post("/query", response_model=QueryOut, tags=["agent"])
async def query(req: QueryIn) -> QueryOut:
    final = await _run_graph(req)
    return _build_query_out(final)


def _initial_state(req: QueryIn) -> AgentState:
    return {
        "trace_id": req.trace_id,
        "tenant_key": req.tenant_key,
        "question": req.question,
        "user_id": req.user_id,
        "history": req.history,
        "datasource_key": req.datasource_key,
        "retry_count": 0,
    }


async def _run_graph(req: QueryIn) -> dict[str, object]:
    """图执行 + LLM 故障收敛(同步/流式同口径:key 缺失/超时等友好返回)。"""
    try:
        final: dict[str, object] = await get_graph().ainvoke(_initial_state(req))
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
    if isinstance(execution, ExecutionResult):
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
    return QueryOut(
        success=isinstance(execution, ExecutionResult) and execution.verdict == "PASS",
        verdict=verdict,
        summary=summary if isinstance(summary, str) else "",
        insights=insights if isinstance(insights, list) else [],
        stats=stats if isinstance(stats, dict) else {},
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
    state = _initial_state(req)

    async def gen() -> AsyncIterator[str]:
        final: dict[str, object] = {}
        try:
            async for mode, chunk in get_graph().astream(
                state, stream_mode=["updates", "custom"]
            ):
                if mode == "custom":
                    ev = chunk if isinstance(chunk, dict) else {}
                    event = str(ev.get("event") or "")
                    if event:
                        yield _sse_frame(
                            event, json.dumps(ev.get("data"), ensure_ascii=False)
                        )
                elif isinstance(chunk, dict):
                    for delta in chunk.values():
                        if isinstance(delta, dict):
                            final.update(delta)  # updates 累积=终态 state
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
