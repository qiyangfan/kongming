"""rag 域响应 schema(097):召回试跑端点定型。

键名与 `service.recall_probe` 返回逐一对齐(纯定型,零行为变更:事实契约
锁定,使前端经契约管线类型化消费——ADR-005 单源)。可选键按现状标注:
- `gate_skipped` 为 None = 闸门正常参与;`"no_knowledge"` = 零知识旁路;
- 路 verdict 为 None = 闸门未参与(零知识旁路/轻量装配),观测面如实透出,
  不报裁剪结论误导排障。
嵌套模型一律公开名(pyright reportPrivateUsage 禁跨模块消费下划线名)。
via 各键由 rag.table_relations 列 NOT NULL 保证非空(jsonb join_keys 为
`[{left_col, right_col}]` 数组形态)。
"""

from pydantic import BaseModel


class ProbeHitOut(BaseModel):
    """单路命中项(score=该路原生分:向量路余弦相似度,全文路 ts_rank)。"""

    schema_name: str
    table_name: str
    score: float
    source: str


class ProbeVerdictOut(BaseModel):
    """闸门判定结论(gate.GateVerdict.as_dict 投影:判定归因与分值观测)。"""

    passed: bool
    reason: str  # no_hits / min_count / top1_below / mean_below / passed
    top1: float
    mean_top_n: float
    count: int


class ProbeRouteOut(BaseModel):
    """单路(向量/全文)投影:命中明细 + 判定结论 + 被裁命中数。

    dropped>0 = 该路不过闸整路被裁(恒裁剪形态,命中共进退)。
    """

    hits: list[ProbeHitOut]
    verdict: ProbeVerdictOut | None = None
    dropped: int


class ProbeFusedHitOut(BaseModel):
    """融合序命中(RRF 融合分,只用于排序与观测,非相关度)。"""

    schema_name: str
    table_name: str
    score: float


class ProbeExpansionViaOut(BaseModel):
    """扩展经由的关系行(rag.table_relations 行投影)。"""

    source_table: str
    target_table: str
    join_keys: list[dict[str, str]]
    source: str


class ProbeExpansionTableOut(BaseModel):
    """补入的扩展表 + 经由关系。"""

    schema_name: str
    table_name: str
    via: ProbeExpansionViaOut


class ProbeSchemaTableOut(BaseModel):
    """两段限定名(扩展后融合集合项)。"""

    schema_name: str
    table_name: str


class ProbeExpansionOut(BaseModel):
    """关系扩展明细(095):enabled=False 或零关系行时 tables 为空表。"""

    enabled: bool
    tables: list[ProbeExpansionTableOut]
    fused_after: list[ProbeSchemaTableOut]


class RecallProbeOut(BaseModel):
    """064 召回试跑响应(只读验证面;键集=service.recall_probe 现值锁定)。"""

    question: str
    gate_skipped: str | None = None
    expansion: ProbeExpansionOut
    vector: ProbeRouteOut
    fulltext: ProbeRouteOut
    fused: list[ProbeFusedHitOut]
