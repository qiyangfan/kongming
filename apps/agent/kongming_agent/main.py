"""Kongming Agent 面入口(仅内网,内部令牌认证,ADR-002)。

业务端点:POST /query(NL2SQL 问答图,同步一次性返回)+
/internal/embedding(批量向量化)+ /internal/describe-table(表元数据润色,
009)——后两者供控制面回调(api 持 DB、agent 持 LLM key 的分工)。
"""

from fastapi import FastAPI, HTTPException, Request
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
from kongming_agent.graph.state import AgentState
from kongming_agent.llm import LlmError
from kongming_agent.settings import Settings

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
    initial: AgentState = {
        "trace_id": req.trace_id,
        "tenant_key": req.tenant_key,
        "question": req.question,
        "user_id": req.user_id,
        "history": req.history,
        "datasource_key": req.datasource_key,
        "retry_count": 0,
    }
    try:
        final = await get_graph().ainvoke(initial)
    except LlmError:
        # LLM 故障(key 缺失/超时/协议异常):链路无法推进,友好返回不裸 500
        return QueryOut(success=False, error="LLM 服务暂不可用,请稍后重试")
    execution = final.get("execution")
    validation = final.get("validation")
    verdict: str | None
    if execution is not None:
        verdict = execution.verdict
    elif validation is not None and validation.permission_block:
        verdict = "BLOCKED"  # 本地预检拦下的写操作,未达执行路由
    else:
        verdict = None
    return QueryOut(
        success=execution is not None and execution.verdict == "PASS",
        verdict=verdict,
        summary=final.get("summary", ""),
        insights=final.get("insights", []),
        stats=final.get("stats", {}),
        columns=execution.columns if execution is not None else [],
        rows=execution.rows if execution is not None else [],
        sql=final.get("generated_sql", ""),
        retry_count=final.get("retry_count", 0),
        error=final.get("error") or None,
    )
