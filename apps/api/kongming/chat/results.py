"""问答结果值对象(用例产物与回显要素;纯数据,零框架依赖)。"""

from dataclasses import dataclass, field

from kongming.chat.models import ChartCandidate, ChartSuggestion


@dataclass(frozen=True)
class ChatOutcome:
    session_id: str | None
    success: bool
    verdict: str | None = None
    summary: str = ""
    # default_factory 用带参泛型别名:裸 list/dict 工厂在 pyright strict 下产物 Unknown
    insights: list[dict[str, str]] = field(default_factory=list[dict[str, str]])
    stats: dict[str, object] = field(default_factory=dict[str, object])
    columns: list[str] = field(default_factory=list[str])
    rows: list[list[object]] = field(default_factory=list[list[object]])
    sql: str = ""
    chart: ChartSuggestion = field(default_factory=lambda: ChartSuggestion("none"))
    # 有序图表候选(首候选即默认单图 chart)
    chart_candidates: list[ChartCandidate] = field(default_factory=list[ChartCandidate])
    retry_count: int = 0
    error: str | None = None
    # 原始技术错误(FAILED 时携带;error 为用户语言,细节经前端折叠披露)
    error_detail: str | None = None
    # messageId 打通:assistant 落库消息 id(反馈提交引用;不落库路径 None)
    # 记录执行时使用的数据源，与会话快照口径一致。
    message_id: str | None = None
    datasource_key: str = "default"
    # 待确认写工具调用，供前端展示确认卡；缺省为空列表。
    pending_tool_calls: list[dict[str, object]] = field(default_factory=list[dict[str, object]])
    # 供记忆后台任务使用，不序列化进响应模型。
    tenant_id: str = ""
    # 类型化产物(派生自 sql/chart/summary)
    artifacts: list[dict[str, object]] = field(default_factory=list[dict[str, object]])
    # 附件引用摘要(filename/mime/size)
    attachments: list[dict[str, object]] = field(default_factory=list[dict[str, object]])
    # Agent 返回的语义层命中，随 result_json 落库并回显；空列表表示未命中。
    metric_hits: list[dict[str, str | None]] = field(default_factory=list[dict[str, str | None]])
    # 文档道结构化来源(净化后正文存活标记投影;随 result_json 落库+回显)
    sources: list[dict[str, str]] = field(default_factory=list[dict[str, str]])
    # 本轮为显式记忆短路(隐式提取任务据此排除,防同内容重复落 IMPLICIT)
    memory_shortcut: bool = False
    # 问答准备期捕获的偏好人工版本快照(内部携带给隐式提取后台任务,
    # 与 tenant_id/memory_shortcut 同为不序列化字段;None=捕获失败,任务跳过)
    pref_revision: int | None = None
    # 呈现层列标签(与 columns 等长对齐;未命中回退原始列名;
    # 空列表=未接线/查询失败,消费方回退 columns)
    column_labels: list[str] = field(default_factory=list[str])


@dataclass(frozen=True)
class SessionSummary:
    """会话列表项(侧栏)。"""

    id: str
    title: str
    updated_at: object  # datetime;类型宽放避免领域层 import datetime 之外的耦合


@dataclass(frozen=True)
class MessageResult:
    """assistant 消息结构化应答要素(回显形态,与 ChatOutcome 的结果子集对齐)。"""

    columns: list[str] = field(default_factory=list[str])
    rows: list[list[object]] = field(default_factory=list[list[object]])
    insights: list[dict[str, str]] = field(default_factory=list[dict[str, str]])
    stats: dict[str, object] = field(default_factory=dict[str, object])
    chart: ChartSuggestion = field(default_factory=lambda: ChartSuggestion("none"))
    # 有序图表候选(读档优先;存量档缺失→空,消费方回落单图 chart)
    chart_candidates: list[ChartCandidate] = field(default_factory=list[ChartCandidate])
    error: str | None = None
    # 原始技术错误(读档;存量档缺失→None,前端不渲染折叠块)
    error_detail: str | None = None
    # 类型化产物(读档优先,缺档按要素重算)
    artifacts: list[dict[str, object]] = field(default_factory=list[dict[str, object]])
    # 呈现层列标签(读档优先;存量档缺失→空列表,消费方回退 columns)
    column_labels: list[str] = field(default_factory=list[str])
    # 语义层口径命中(读档优先;存量档缺失→空,不反查指标表重算)
    metric_hits: list[dict[str, str | None]] = field(default_factory=list[dict[str, str | None]])
    # 文档道来源(读档优先;存量档缺失→空;名称快照不回查文档表)
    sources: list[dict[str, str]] = field(default_factory=list[dict[str, str]])


@dataclass(frozen=True)
class SessionMessage:
    """会话消息回显项(user 问句 / assistant 要素)。"""

    id: str
    role: str
    question: str | None = None
    summary: str | None = None
    sql: str | None = None
    status: str = "SUCCESS"
    error: str | None = None
    # 原始技术错误(FAILED 回显;web 折叠「技术详情」)
    error_detail: str | None = None
    result: MessageResult | None = None
    # 反馈状态回显(assistant 消息;user 消息恒 None)
    rating: str | None = None
    corrected_sql: str | None = None
    feedback_comment: str | None = None
    # 附件引用摘要(回显徽标;空数组=无附件)
    attachments: list[dict[str, object]] = field(default_factory=list[dict[str, object]])


@dataclass(frozen=True)
class ExplainView:
    """执行计划视图:PASS 含计划树;BLOCKED/ERROR 含友好文案。"""

    success: bool
    plan: object | None = None
    error: str | None = None
