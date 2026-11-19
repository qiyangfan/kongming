"""chat 请求/响应模型与领域结果转换(ADR-005 契约的类型单源)。

模型类名冻结:OpenAPI components 按类名生成,改名即契约漂移。
"""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal, cast

from pydantic import BaseModel, Field

from kongming.chat.results import ChatOutcome, MessageResult, SessionMessage


class ChatQueryIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    session_id: uuid.UUID | None = None
    persist: bool = True  # 显式 false=临时对话不落库(chat spec)
    # 条数与单条双限:防超大 history 撑爆 LLM prompt(token 成本攻击面)
    history: list[Annotated[str, Field(max_length=2000)]] = Field(
        default_factory=list, max_length=10
    )
    datasource_key: str = Field(default="default", pattern=r"^[a-z][a-z0-9_]{1,63}$")
    # Agent 内核选择(缺省 recipe,8 节点内核);react=ReAct 工具调用内核
    agent_mode: Literal["recipe", "react"] = "recipe"
    # 图片附件引用(≤3;归属校验在 service)
    attachment_ids: list[uuid.UUID] = Field(default_factory=list[uuid.UUID], max_length=3)


class ChartOut(BaseModel):
    type: str
    x: str | None = None
    y: str | None = None


class ChartCandidateOut(BaseModel):
    """图表候选:候选自带完整绑定,y 为值列(多度量多系列),
    series 为系列维(分组柱/时序多系列)。既有 chart 字段保留=首候选展平。"""

    type: str
    x: str | None = None
    y: list[str] = []
    series: str | None = None


class ArtifactOut(BaseModel):
    """类型化产物:type ∈ sql/chart/document。"""

    type: str
    content: str
    metadata: dict[str, object] = {}


class PendingToolCallOut(BaseModel):
    """待确认写工具调用:确认卡片渲染要素。"""

    confirm_id: str
    tool_name: str
    args_digest: str = ""


class MetricHitOut(BaseModel):
    """语义层口径命中(标识/名称/口径表达式/修订时间)。"""

    metric_id: str
    metric_name: str
    sql_template: str
    updated_at: str | None = None


class SourceOut(BaseModel):
    """结构化来源条目(引用键/来源标识/文档名快照)。
    语义:纯数据道恒为空;知识道与融合道由净化后正文单向投影,可非空。"""

    citation_key: str
    source_ref: str = ""
    doc_name: str = ""


class ChatQueryOut(BaseModel):
    session_id: str | None
    success: bool
    verdict: str | None
    summary: str
    insights: list[dict[str, str]]
    stats: dict[str, object]
    columns: list[str]
    rows: list[list[object]]
    sql: str
    chart: ChartOut
    # 有序图表候选(空=查询失败/不适配;首候选即上方 chart)
    chart_candidates: list[ChartCandidateOut] = []
    retry_count: int
    error: str | None
    # 原始技术错误(FAILED 携带;前端折叠「技术详情」,缺省隐藏)
    error_detail: str | None = None
    # messageId 打通:assistant 落库消息 id(反馈提交引用)+执行数据源快照
    message_id: str | None = None
    datasource_key: str = "default"
    # 待确认写工具调用(空列表=无)
    pending_tool_calls: list[PendingToolCallOut] = []
    # 类型化产物(空数组)
    artifacts: list["ArtifactOut"] = []
    # 附件引用摘要(空数组)
    attachments: list[dict[str, object]] = []
    # 呈现层列标签,与 columns 等长对齐(空=未接线/历史存量,回退 columns)
    column_labels: list[str] = []
    # 指标命中为空表示无命中；将命中列表传给 Agent。
    metric_hits: list[MetricHitOut] = []
    # 结构化来源——纯数据道恒为空;知识道与融合道由净化后正文
    # 单向兼容投影允许为空，以兼容旧版 Agent。
    sources: Annotated[
        list[SourceOut],
        Field(description="纯数据问答不返回来源;知识问答和融合问答会从净化后的回答正文提取来源。"),
    ] = Field(default_factory=list[SourceOut])


def to_chat_query_out(o: ChatOutcome) -> ChatQueryOut:
    """问答终态 → 同步/流式 result 响应模型。"""
    return ChatQueryOut(
        session_id=o.session_id,
        attachments=[dict(a) for a in o.attachments],
        success=o.success,
        verdict=o.verdict,
        summary=o.summary,
        insights=o.insights,
        stats=o.stats,
        columns=o.columns,
        rows=o.rows,
        sql=o.sql,
        chart=ChartOut(type=o.chart.type, x=o.chart.x, y=o.chart.y),
        chart_candidates=[
            ChartCandidateOut(type=c.type, x=c.x, y=list(c.y), series=c.series)
            for c in o.chart_candidates
        ],
        retry_count=o.retry_count,
        error=o.error,
        error_detail=o.error_detail,
        message_id=o.message_id,
        datasource_key=o.datasource_key,
        pending_tool_calls=[
            PendingToolCallOut(
                confirm_id=str(p.get("confirm_id") or ""),
                tool_name=str(p.get("tool_name") or ""),
                args_digest=str(p.get("args_digest") or ""),
            )
            for p in o.pending_tool_calls
        ],
        artifacts=[
            ArtifactOut(
                type=str(a.get("type") or ""),
                content=str(a.get("content") or ""),
                metadata=(
                    # isinstance 守卫后 cast 收窄键型(mcp client 同款;运行时不变)
                    dict(cast("dict[str, object]", _meta))
                    if isinstance(_meta := a.get("metadata"), dict)
                    else {}
                ),
            )
            for a in o.artifacts
        ],
        column_labels=o.column_labels,
        metric_hits=[
            MetricHitOut(
                metric_id=str(m.get("metric_id") or ""),
                metric_name=str(m.get("metric_name") or ""),
                sql_template=str(m.get("sql_template") or ""),
                updated_at=(str(m["updated_at"]) if m.get("updated_at") is not None else None),
            )
            for m in o.metric_hits
        ],
        sources=[
            SourceOut(
                citation_key=str(s.get("citation_key") or ""),
                source_ref=str(s.get("source_ref") or ""),
                doc_name=str(s.get("doc_name") or ""),
            )
            for s in o.sources
        ],
    )


class SuggestionsOut(BaseModel):
    """GET /chat/suggestions 响应:建议问句清单(空=无建议,静默隐藏)。"""

    items: list[str]


class StreamStageOut(BaseModel):
    """stage 事件 data:节点粒度进度(status ∈ running/done)。"""

    node: str
    status: Literal["running", "done"]
    retry_count: int = 0  # selfheal 表示重试序号；其他节点表示已完成的重试数。


class StreamSqlOut(BaseModel):
    """sql 事件 data:validate 通过后提前下发,内容与终态落库 SQL 一致。"""

    sql: str


class StreamSummaryOut(BaseModel):
    """summary 事件 data:总结增量(当前为单条完整文案,逐事件追加=打字机语义)。"""

    delta: str


class StreamErrorOut(BaseModel):
    """error 终态事件 data:仅传输层/意外失败(业务三态走 result 事件)。"""

    error: str
    reason_code: str | None = None


class StreamToolCallOut(BaseModel):
    """tool_call 事件 data(tool-calling):工具调用进度。

    status ∈ running/done/rejected(confirm_required 以 done 下发,result_digest
    含「待确认」);args_digest/result_digest 为截断摘要(审计同源口径)。
    """

    tool_name: str
    status: Literal["running", "done", "rejected"]
    args_digest: str = ""
    result_digest: str = ""


class StreamTraceOut(BaseModel):
    """trace 事件 data:agent 侧诊断信号(内部运维口径,不渲染进用户答案)。

    表预筛/召回闸门/引用净化/装配预算共用本通道;
    fields 为自由映射(键集随能力演进,前向兼容);node=发起节点名。
    """

    node: str
    fields: dict[str, Any]


# model 键使 FastAPI 把全部事件模型注册进 components(ADR-005 类型生成可达),
# content 键描述 SSE 事件流的真实形态(6 类事件 data 的 oneOf)
STREAM_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "model": (
            StreamStageOut
            | StreamSqlOut
            | StreamSummaryOut
            | StreamToolCallOut
            | StreamErrorOut
            | StreamTraceOut
            | ChatQueryOut
        ),
        "description": (
            "SSE 发送 stage、sql、summary、tool_call 和诊断事件;"
            "result 与 /chat/query 返回相同数据。"
            "错误通过 error 终态发送;以冒号开头的注释帧用于心跳。"
        ),
        "content": {
            "text/event-stream": {
                "schema": {
                    "oneOf": [
                        {"$ref": "#/components/schemas/StreamStageOut"},
                        {"$ref": "#/components/schemas/StreamSqlOut"},
                        {"$ref": "#/components/schemas/StreamSummaryOut"},
                        {"$ref": "#/components/schemas/StreamToolCallOut"},
                        {"$ref": "#/components/schemas/StreamErrorOut"},
                        {"$ref": "#/components/schemas/StreamTraceOut"},
                        {"$ref": "#/components/schemas/ChatQueryOut"},
                    ]
                }
            }
        },
    }
}


class SessionItemOut(BaseModel):
    id: str
    title: str
    updated_at: datetime


class ChartInfoOut(BaseModel):
    type: str
    x: str | None = None
    y: str | None = None


class MessageResultOut(BaseModel):
    columns: list[str] = Field(default_factory=list[str])
    rows: list[list[object]] = Field(default_factory=list[list[object]])
    insights: list[dict[str, str]] = Field(default_factory=list[dict[str, str]])
    stats: dict[str, object] = Field(default_factory=dict[str, object])
    chart: ChartInfoOut
    # 有序图表候选(存量档缺失→空,前端回落单图 chart)
    chart_candidates: list[ChartCandidateOut] = []
    error: str | None = None
    # 原始技术错误(存量档缺失→None,前端不渲染折叠块)
    error_detail: str | None = None
    # 类型化产物
    artifacts: list[ArtifactOut] = []
    # 呈现层列标签(空=历史存量档,回退 columns)
    column_labels: list[str] = Field(default_factory=list[str])
    # 口径依据(空=无命中/存量档;由正文消息单向派生,回显不重算)
    metric_hits: list[MetricHitOut] = Field(default_factory=list[MetricHitOut])
    # 结构化来源(空=纯数据道/存量档;知识道与融合道可非空;
    # 名称快照不回查文档表)
    sources: Annotated[
        list[SourceOut],
        Field(description="纯数据问答不返回来源;知识问答和融合问答会从净化后的回答正文提取来源。"),
    ] = Field(default_factory=list[SourceOut])


class SessionMessageOut(BaseModel):
    id: str
    role: str
    question: str | None = None
    summary: str | None = None
    sql: str | None = None
    status: str
    error: str | None = None
    # 原始技术错误(FAILED 回显;web 折叠「技术详情」)
    error_detail: str | None = None
    result: MessageResultOut | None = None
    # messageId 打通:assistant 消息反馈状态回显(user 消息恒 None)
    rating: str | None = None
    corrected_sql: str | None = None
    feedback_comment: str | None = None
    # 附件引用摘要
    attachments: list[dict[str, object]] = []


def to_session_message_out(m: SessionMessage) -> SessionMessageOut:
    """会话历史消息 → 回显响应模型(结果档缺失时 result=None)。"""
    result: MessageResultOut | None = None
    if m.result is not None:
        r: MessageResult = m.result
        result = MessageResultOut(
            columns=r.columns,
            rows=r.rows,
            insights=r.insights,
            stats=r.stats,
            chart=ChartInfoOut(type=r.chart.type, x=r.chart.x, y=r.chart.y),
            chart_candidates=[
                ChartCandidateOut(type=c.type, x=c.x, y=list(c.y), series=c.series)
                for c in r.chart_candidates
            ],
            error=r.error,
            error_detail=r.error_detail,
            column_labels=r.column_labels,
            metric_hits=[
                MetricHitOut(
                    metric_id=str(m.get("metric_id") or ""),
                    metric_name=str(m.get("metric_name") or ""),
                    sql_template=str(m.get("sql_template") or ""),
                    updated_at=(str(m["updated_at"]) if m.get("updated_at") is not None else None),
                )
                for m in r.metric_hits
            ],
            sources=[
                SourceOut(
                    citation_key=str(s.get("citation_key") or ""),
                    source_ref=str(s.get("source_ref") or ""),
                    doc_name=str(s.get("doc_name") or ""),
                )
                for s in r.sources
            ],
            artifacts=[
                ArtifactOut(
                    type=str(a.get("type") or ""),
                    content=str(a.get("content") or ""),
                    metadata=(
                        # isinstance 守卫后 cast 收窄键型(与 to_chat_query_out 同款;运行时不变)
                        dict(cast("dict[str, object]", _meta))
                        if isinstance(_meta := a.get("metadata"), dict)
                        else {}
                    ),
                )
                for a in r.artifacts
            ],
        )
    return SessionMessageOut(
        id=m.id,
        role=m.role,
        question=m.question,
        summary=m.summary,
        sql=m.sql,
        status=m.status,
        error=m.error,
        error_detail=m.error_detail,
        result=result,
        rating=m.rating,
        corrected_sql=m.corrected_sql,
        feedback_comment=m.feedback_comment,
        attachments=[dict(a) for a in m.attachments],
    )


class RerunIn(BaseModel):
    """SQL 卡再执行:message_id 引用 + 编辑后 SQL;归属与六层防御在
    service/实现侧(与 EXPLAIN 同范式)。"""

    message_id: uuid.UUID
    sql: str = Field(min_length=1, max_length=4000)


class RenameIn(BaseModel):
    """标题去除首尾空白后不能为空且最多 200 字;max_length=2000 仅限制过大的请求体。"""

    title: str = Field(min_length=1, max_length=2000)


class ExportIn(BaseModel):
    """所见即所导:前端传已展示的 columns+rows,不重跑 SQL。

    rows 传输层上限=修复:截断发生在 JSON 解析之后,挡不住请求体本身
    的内存放大;max_length 在解析期即拒(422),service 层截断保留为双闸。
    """

    format: Literal["csv", "xlsx"]
    columns: list[str] = Field(min_length=1, max_length=200)
    rows: list[list[object]] = Field(default_factory=list[list[object]], max_length=10000)


class ExplainIn(BaseModel):
    """message_id 引用形态:客户端不可提交裸 SQL。"""

    message_id: uuid.UUID


class ExplainOut(BaseModel):
    success: bool
    plan: object | None = None  # EXPLAIN (FORMAT JSON) 解析产物(顶层数组)
    error: str | None = None
