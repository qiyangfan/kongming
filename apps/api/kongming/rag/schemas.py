"""rag 请求/响应模型与条目投影转换(模型类名冻结:OpenAPI components 单源)。

召回试跑响应与 `service.recall_probe` 的字段保持一致并供 OpenAPI 生成前端类型。
`gate_skipped=None` 表示相关度判定已参与,`"no_knowledge"` 表示零知识旁路;
旁路时各来源 verdict 保持 `None`。
"""

import uuid as uuidlib
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, field_validator

from kongming.rag.models import KnowledgeEntry, SchemaTablePair


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
    """关系扩展明细:enabled=False 或零关系行时 tables 为空表。"""

    enabled: bool
    tables: list[ProbeExpansionTableOut]
    fused_after: list[ProbeSchemaTableOut]


class RecallProbeOut(BaseModel):
    """召回试跑响应(只读验证面;键集=service.recall_probe 现值锁定)。"""

    question: str
    gate_skipped: str | None = None
    expansion: ProbeExpansionOut
    vector: ProbeRouteOut
    fulltext: ProbeRouteOut
    fused: list[ProbeFusedHitOut]


# ============ 知识管理面 ============


class UploadOut(BaseModel):
    tables: list[str]


class SchemasOut(BaseModel):
    datasource_key: str
    schemas: list[str]


class KnowledgeItemOut(BaseModel):
    id: str
    datasource_key: str
    schema_name: str
    table_name: str
    table_comment: str
    columns_comment: dict[str, str]
    sample_queries: list[str]
    source_type: str
    updated_at: datetime
    alias: str = ""  # 业务称谓别名
    # 归属(空=租户公共;team_name 供列表归属列展示)
    team_id: str | None = None
    team_name: str | None = None


class KnowledgePageOut(BaseModel):
    items: list[KnowledgeItemOut]
    total: int
    page: int
    size: int


class EditTableIn(BaseModel):
    """表说明人工精修请求(table_comment 与 alias 同面编辑)。"""

    table_comment: str = Field(max_length=500)
    alias: str = Field(max_length=64)

    @field_validator("table_comment", "alias")
    @classmethod
    def _strip_no_control(cls, v: str) -> str:
        # 换行/回车破坏 schema_hint 行结构;strip 后空串=清除语义
        if "\n" in v or "\r" in v:
            raise ValueError("不可包含换行")
        return v.strip()

    @field_validator("alias")
    @classmethod
    def _alias_no_structure_chars(cls, v: str) -> str:
        # 括号/竖线与 hint 行结构「表名(别名)(列…)」冲突,配合换行可
        # 伪造表名注入提取正则;此处拒收,models.clean_alias 为纵深
        if any(c in v for c in "()（）|"):
            raise ValueError("别名不可包含括号或竖线")
        return v


class OwnershipIn(BaseModel):
    """归属变更请求(team_id 空=转公共;非空=转该团队专属)。"""

    team_id: uuidlib.UUID | None = None


class DatasourceActionIn(BaseModel):
    datasource_key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")


class FetchOut(BaseModel):
    found: int
    enriched: int
    skipped: int
    removed: int  # 对账删除的扫描集外自动来源行数


class ReindexOut(BaseModel):
    deleted: int
    rebuilt: int
    enriched: int


class SchemaTableOut(BaseModel):
    schema_name: str
    table_name: str


class MissingColumnsOut(BaseModel):
    schema_name: str
    table_name: str
    missing: list[str]
    missing_count: int


class FailureClusterOut(BaseModel):
    question: str
    count: int
    last_status: str
    last_seen: datetime
    rating: str
    has_corrected_sql: bool


class HistoryOverviewOut(BaseModel):
    success_count: int
    distinct_questions: int
    cap: int


class CoverageReportOut(BaseModel):
    datasource_key: str
    unregistered_tables: list[SchemaTableOut]
    unregistered_total: int
    structure_only_tables: list[SchemaTableOut]
    structure_only_total: int
    missing_columns: list[MissingColumnsOut]
    missing_columns_total: int
    no_sample_tables: list[SchemaTableOut]
    no_sample_total: int
    history: HistoryOverviewOut
    failure_clusters: list[FailureClusterOut]
    failure_cluster_total: int


def to_knowledge_item_out(e: KnowledgeEntry) -> KnowledgeItemOut:
    """条目 → 响应投影单点(列表/编辑/归属变更共用)。"""
    return KnowledgeItemOut(
        id=e.id,
        datasource_key=e.datasource_key,
        schema_name=e.schema_name,
        table_name=e.table_name,
        table_comment=e.table_comment,
        columns_comment=e.columns_comment,
        sample_queries=e.sample_queries,
        source_type=e.source_type,
        updated_at=e.updated_at,  # type: ignore[arg-type]
        alias=e.alias,
        team_id=e.team_id,
        team_name=e.team_name,
    )


def to_schema_table_out(pairs: list[SchemaTablePair]) -> list[SchemaTableOut]:
    """两段限定名对 → 覆盖报告表项。"""
    return [SchemaTableOut(schema_name=p.schema_name, table_name=p.table_name) for p in pairs]


# ============ 内部召回面 ============


class RetrieveIn(BaseModel):
    tenant_key: str = Field(min_length=1, max_length=64)
    datasource_key: str = Field(default="default", pattern=r"^[a-z][a-z0-9_]{1,63}$")
    question: str = Field(min_length=1, max_length=2000)
    # 多轮继承:最近历史问句(≤3 条,取末条走继承路;单轮缺省)
    history: list[Annotated[str, Field(max_length=2000)]] = Field(
        default_factory=list, max_length=3
    )
    # 可选:调用者身份(端用户 id)——可见集合=公共∪其所在团队;
    # 缺省(未携带)=纯公共形态,既有回调负载(agent 面无须改动即可
    # 继续工作;接线后团队条目方进入召回)
    user_id: uuidlib.UUID | None = None


class MetricHitOut(BaseModel):
    """语义层口径命中(标识/名称/口径表达式/修订时间)。"""

    metric_id: str
    metric_name: str
    sql_template: str
    updated_at: str | None = None


class SchemaGraphColumnOut(BaseModel):
    """结构化图列(名称/类型/注释;不含任何连接信息)。"""

    name: str
    data_type: str
    comment: str = ""


class SchemaGraphTableOut(BaseModel):
    """结构化图表、列、主键与可确定角色。"""

    schema_name: str
    table_name: str
    comment: str = ""
    columns: list[SchemaGraphColumnOut] = []
    primary_key: list[str] = []
    role: str = "unknown"
    source: str = "introspection"


class SchemaGraphForeignKeyOut(BaseModel):
    """结构化图外键边(多列按序配对)。"""

    source_schema: str
    source_table: str
    source_columns: list[str] = []
    target_schema: str
    target_table: str
    target_columns: list[str] = []


class SchemaGraphOut(BaseModel):
    """召回响应携带的结构化 Schema 图;complete=False 时调用方不得做硬范围拒绝。"""

    tables: list[SchemaGraphTableOut] = []
    foreign_keys: list[SchemaGraphForeignKeyOut] = []
    complete: bool = True
    # 全图已知表键("schema.table",子图下发时携带):调用方据此区分
    # 「未召回的已知表」与「库中不存在的表」,只对后者做范围硬拒
    known_tables: list[str] = []


class RetrieveOut(BaseModel):
    schema_hint: str
    history_sql: str
    # 语义段为空时，不向 Agent 注入内容。
    relations_hint: str = ""
    metric_hints: str = ""
    # 结构化口径命中(与 metric_hints 同源;空=无命中)
    metric_hits: list[MetricHitOut] = []
    # 结构化 Schema 图(纯增;缺失/旧 Agent 不消费时保持既有路径)
    schema_graph: SchemaGraphOut | None = None
    # 文档段(空=无命中,双通道隔离)。
    # 每项 citation_key(稳定引用键;由父块主键派生)
    doc_hints: list[dict[str, str]] = []
    # skill-pack 投影段(空=零包)
    skill_terms: str = ""
    skill_examples: str = ""
    skill_persona: str = ""


# ============ 文档管理面 ============


class DocumentItemOut(BaseModel):
    id: str
    doc_name: str
    block_count: int
    updated_at: str


class DocumentChunkOut(BaseModel):
    id: str
    chunk_level: str
    seq: int
    source_ref: str | None
    has_embedding: bool
    content: str


class DocUploadOut(BaseModel):
    doc_id: str
    blocks: int
    skipped: bool


class DocEnrichOut(BaseModel):
    doc_id: str
    entities: int


class RetrievalProbeHitOut(BaseModel):
    child_id: str
    parent_id: str | None
    score: float


class RetrievalProbeFusedOut(BaseModel):
    child_id: str
    parent_id: str | None
    rank: int


class RetrievalProbeParentOut(BaseModel):
    parent_id: str
    seq: int
    doc_name: str
    source_ref: str
    citation_key: str
    content: str
    rank: int


class RetrievalProbeOut(BaseModel):
    paths: dict[str, list[RetrievalProbeHitOut]]
    fused_children: list[RetrievalProbeFusedOut]
    parents: list[RetrievalProbeParentOut]
