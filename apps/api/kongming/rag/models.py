"""rag 领域模型与纯函数:不依赖 Web 框架或数据库。

覆盖优先级(knowledge spec):INTROSPECTION < AI_GENERATED < MANUAL_UPLOAD <
MANUAL_EDIT < FEEDBACK_REFINED——自动来源(扫描/润色)不覆盖更高优先级既有条目。
召回参数与上游同构:topK=5、RRF k=60(query_history 独立强 few-shot 通道不进融合)。
"""

import hashlib
import re
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, NamedTuple

# source_type 覆盖优先级(baseline CHECK 枚举,0006 演进后的五态)
SOURCE_TYPE_PRIORITY: dict[str, int] = {
    "INTROSPECTION": 1,
    "AI_GENERATED": 2,
    "MANUAL_UPLOAD": 3,
    "MANUAL_EDIT": 4,
    "FEEDBACK_REFINED": 5,
}

AUTOMATED_SOURCE_TYPES = frozenset({"INTROSPECTION", "AI_GENERATED"})

RECALL_TOP_K = 5
RRF_K = 60.0
HISTORY_SQL_LIMIT = 3  # 历史成功 SQL 拼接条数上限(防 token 膨胀)
# query_history 每 (tenant, datasource_key) 的总量上限(防膨胀闸门),超出淘汰最旧记录。
QUERY_HISTORY_CAP = 500


@dataclass(frozen=True)
class TableBlock:
    """parser 切块产物:一张表的知识条目内容(schema_name 为请求表单维度)。"""

    table_name: str
    table_comment: str = ""
    # default_factory 走参数化泛型别名(bare dict/list 推断出 Unknown 元素)
    columns_comment: dict[str, str] = field(default_factory=dict[str, str])
    sample_queries: list[str] = field(default_factory=list[str])
    alias: str = ""  # 业务称谓别名


@dataclass(frozen=True)
class KnowledgeEntry:
    """知识条目(管理面/召回视图;永不含向量原始值)。新增 alias 业务称谓。
    team_id=团队归属(NULL=租户公共),team_name=展示用团队名(列表面)。"""

    id: str
    tenant_id: str
    datasource_key: str
    schema_name: str
    table_name: str
    table_comment: str
    columns_comment: dict[str, str]
    sample_queries: list[str]
    source_type: str
    updated_at: object
    alias: str = ""  # 业务称谓别名
    team_id: str | None = None  # 团队归属(NULL=公共)
    team_name: str | None = None  # 归属团队名(管理面展示;召回面不取)


@dataclass(frozen=True)
class RecallHit:
    """单路召回命中(rank 从 1 起;RRF 输入形态)。

    score=该路原生分(向量/历史路=余弦相似度 `1-(embedding<=>v)`,
    全文路=ts_rank 检索得分)——RRF 融合分只用于排序与观测,相关度判据
    落在各路原生分上;source=来源路标识(vector/fulltext/history)。
    """

    table_name: str
    schema_name: str = ""
    score: float = 0.0
    source: str = ""


@dataclass(frozen=True)
class IntrospectedTable:
    """全量内省产物(与 meta 通道同口径:BASE TABLE+VIEW+注释)。"""

    schema_name: str
    table_name: str
    table_comment: str
    # name -> type(纯结构形态);default_factory 走参数化别名(bare dict 推断 Unknown 元素)
    columns_comment: dict[str, str] = field(default_factory=dict[str, str])


@dataclass(frozen=True)
class RagContext:
    """召回编排产物(内部端点响应体)。schema_hint 空=表说明零命中。"""

    schema_hint: str
    history_sql: str


def can_automated_overwrite(existing: str, incoming: str) -> bool:
    """覆盖优先级判定:自动来源不得覆盖更高优先级既有条目(D5)。

    同级可覆盖(重复扫描幂等更新);人工来源不经过本判定(显式操作即显式覆盖)。
    """
    return SOURCE_TYPE_PRIORITY[incoming] >= SOURCE_TYPE_PRIORITY[existing]


# 知识条目团队归属(可见集合/写权限判定/缓存指纹:领域纯函数单点) ----

WRITABLE_TEAM_ROLES = frozenset({"OWNER", "MEMBER"})


def entry_visible(*, team_id: str | None, caller_team_ids: Collection[str]) -> bool:
    """知识条目可见集合判定:租户公共条目(归属空)恒可见;团队条目仅
    调用者所在团队可见。孤儿归属(团队已删→成员行连带清空→不在任何调用者
    集合)与非成员同形不可见,不泄露存在性。召回过滤/缓存键/管理列表三处
    同一判定,禁另起口径(design Goals)。"""
    if team_id is None:
        return True
    return team_id in caller_team_ids


def entry_write_access(*, team_id: str | None, member_role: str | None) -> str:
    """知识条目写权限三态判定:"allowed"=可写;"not_member"=非成员
    (404 同形,不暴露存在性);"viewer"=VIEWER 只读成员(403——条目在其
    可见集合内,存在性已知,角色不足如实拒绝)。公共条目=既有权限口径
    (system:rag:manage 已闸),零额外判定(存量)。"""
    if team_id is None:
        return "allowed"
    if member_role is None:
        return "not_member"
    return "allowed" if member_role in WRITABLE_TEAM_ROLES else "viewer"


def team_set_fingerprint(team_ids: Collection[str]) -> str:
    """调用者团队集合确定性指纹(缓存键维度):排序后拼接取 sha256 前
    16 hex;空集=固定串 "public"(纯公共形态,同形用户共享缓存空间)。
    同集合异序同指纹(排序),异集合异指纹——团队关系变化即指纹变,缓存
    天然隔离(TTL 吸收旧条目,无需主动清)。"""
    ids = sorted(team_ids)
    if not ids:
        return "public"
    return hashlib.sha256("\x1f".join(ids).encode("utf-8")).hexdigest()[:16]


# alias 结构防御(纵深):括号/换行/竖线会破坏 schema_hint 行结构
# 「schema.table(alias)(cols) -- comment」并可借换行伪造「fake.table(」形态
# 注入 表名提取;含违禁字符的别名整体置空(截半句更误导)。
_ALIAS_FORBIDDEN = re.compile(r"[\r\n()（）|]")


def clean_alias(alias: str) -> str:
    """别名清洗:strip+压缩内部空白;含结构性违禁字符(括号/换行/竖线)置空。"""
    folded = " ".join(alias.split())
    return "" if _ALIAS_FORBIDDEN.search(folded) else folded


def build_embedding_text(
    table_name: str, table_comment: str, columns_comment: dict[str, str], *, alias: str = ""
) -> str:
    """embedding 输入文本:表名+alias+表说明+字段说明(alias 增强输入侧匹配)。

    alias 经 clean_alias 纵深清洗(结构性违禁字符置空)。
    """
    cols = " ".join(f"{k} {v}" for k, v in columns_comment.items())
    parts = [x for x in (table_name, clean_alias(alias), table_comment, cols) if x]
    return " ".join(parts).strip()


def rrf_fuse(
    ranked_lists: list[list[RecallHit]], *, k: float = RRF_K, top: int = RECALL_TOP_K
) -> list[RecallHit]:
    """RRF 融合:score(d)=Σ 1/(k+rank_i(d)),k=60(上游同构参数)。

    仅表说明两路(向量/全文)进融合;并列分数按首现顺序稳定排序。
    """
    scores: dict[str, float] = {}
    first_hit: dict[str, RecallHit] = {}
    for hits in ranked_lists:
        for rank, hit in enumerate(hits, start=1):
            key = f"{hit.schema_name}.{hit.table_name}"
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
            first_hit.setdefault(key, hit)
    ordered = sorted(scores.items(), key=lambda kv: -kv[1])
    return [first_hit[key] for key, _ in ordered[:top]]


def rrf_fuse_ids(ranked_lists: list[list[str]], *, k: float = RRF_K, top: int = 5) -> list[str]:
    """通用 RRF(任意 id 键;文档子块融合)——与 rrf_fuse 同算法参数,
    同分按首现顺序稳定排序(不动既有函数)。"""
    scores: dict[str, float] = {}
    for hits in ranked_lists:
        for rank, hit_id in enumerate(hits, start=1):
            scores[hit_id] = scores.get(hit_id, 0.0) + 1.0 / (k + rank)
    ordered = sorted(scores.items(), key=lambda kv: -kv[1])
    return [key for key, _ in ordered[:top]]


def build_schema_hint(entries: list[KnowledgeEntry]) -> str:
    """召回条目 → generate 的 schema 提示文本(纯函数)。

    形态(顺延):`表名(alias):列... -- 表说明`;空 alias 不显括号;
    空说明只留列名;表内嵌示例 SQL 逐条追加。alias 经 clean_alias 纵深清洗。
    """
    lines: list[str] = []
    for e in entries:
        cols = ", ".join(
            f"{name}({desc})" if desc else name for name, desc in e.columns_comment.items()
        )
        # alias 优先显示在表名后括号内
        alias = clean_alias(e.alias)
        alias_part = f"({alias})" if alias else ""
        header = f"{e.schema_name}.{e.table_name}{alias_part}({cols})"
        if e.table_comment:
            header += f" -- {e.table_comment}"
        lines.append(header)
        for sql in e.sample_queries:
            lines.append(f"  示例:{sql}")
    return "\n".join(lines)


def build_history_sql(sql_texts: list[str]) -> str:
    """历史成功 SQL 拼接(强 few-shot 段;最多 HISTORY_SQL_LIMIT 条)。"""
    return "\n".join(sql_texts[:HISTORY_SQL_LIMIT])


# 关系感知召回扩展(纯函数;一跳/去重/上限/尾附加) ----


@dataclass(frozen=True)
class RelationExpansion:
    """单条扩展产物:补入的表键 + 经由的关系行(试跑端点与 trace 透出用)。"""

    key: tuple[str, str]  # (schema_name, table_name)
    via: dict[str, Any]  # 经由关系行 {source_table, target_table, join_keys, source}


def _split_qualified(name: str) -> tuple[str, str] | None:
    """`schema.table` 两段限定名 → (schema, table);非两段形态返回 None(不参与)。"""
    parts = name.split(".")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None
    return parts[0], parts[1]


def expand_by_relations(
    recalled: list[tuple[str, str]],
    relations: list[dict[str, Any]],
    *,
    max_expand: int,
) -> list[RelationExpansion]:
    """按已声明关系一跳扩展(纯函数,无 IO)。

    任一端在召回集内的关系行,其对端不在召回集内则补入(方向对称);
    关系行不存在即零扩展(SHALL NOT 猜测)。已在召回集内的对端不补、
    不占额度;多条关系指向同表只补一次;输出序=关系行输入序(尾附加序,
    预算超限时按 既有尾部丢弃方向先裁扩展);受 max_expand 上限约束。
    """
    recalled_set = set(recalled)
    out: list[RelationExpansion] = []
    seen: set[tuple[str, str]] = set()
    for rel in relations:
        if len(out) >= max_expand:
            break  # 上限先于补入判定(max_expand=0 即零扩展)
        src = _split_qualified(str(rel.get("source_table", "")))
        dst = _split_qualified(str(rel.get("target_table", "")))
        if src is None or dst is None:
            continue  # 非两段限定名的关系行不参与(写入口已保证形态,防御)
        if src in recalled_set:
            far = dst
        elif dst in recalled_set:
            far = src
        else:
            continue  # 两端均在召回集外:不命中
        if far in recalled_set or far in seen:
            continue  # 对端已在召回集(不补不占额度)或已被本轮补入
        seen.add(far)
        out.append(RelationExpansion(key=far, via=rel))
    return out


# ---- 领域错误(router 统一映射状态码;message 即用户可见文案) ----


class RagError(Exception):
    """rag 域错误基类。"""


# 400(同形,不区分不存在/禁用/越属——防 key 探测)


class DatasourceInvalid(RagError):
    """数据源不存在/禁用/不归属当前租户(与执行路由同形语义)。"""


class InvalidSchemaName(RagError):
    """schema_name 非合法 PG 标识符。"""


class SchemaNotAllowed(RagError):
    """schema_name 语法合法但不在该数据源可扫描集合(写边界闸)。"""


class EmptyDocument(RagError):
    """文档不含任何 `## 表名` 形态表块。"""


class DocumentTooLarge(RagError):
    """文档/文件超过大小上限。"""


class TooManyBlocks(RagError):
    """表块数超过上限(防大文档切块风暴)。"""


class TableNameTooLong(RagError):
    """表名超过列宽上限(128 字符,对齐 table_name VARCHAR(128))。"""


class UnsupportedFileType(RagError):
    """不支持的文件扩展名/解析失败。"""


# 404


class KnowledgeNotFound(RagError):
    """知识条目不存在/越租户/团队条目非成员(同形,防探测)。"""


# 403


class TeamWriteForbidden(RagError):
    """团队条目写权限不足(VIEWER 只读成员;条目在其可见集合内,角色不足
    如实 403,与「非成员 404 同形」分立——不向不可见者泄露,不向可见者说谎)。"""


# ============ 知识覆盖报告(确定性纯聚合;零 LLM 零写入) ============

# 清单封顶(契约固化:零覆盖/纯结构/示例缺口 ≤200,缺标签列按表 ≤,
# 失败问题簇 ≤50);封顶只截条目,总数计数如实反映全量
COVERAGE_LIST_CAP = 200
COVERAGE_MISSING_COLS_CAP = 100
COVERAGE_FAILURE_CLUSTER_CAP = 50
COVERAGE_FAILURE_WINDOW_DAYS = 30  # 失败信号回看窗口
COVERAGE_FAILURE_ROW_LIMIT = 500  # 聚合读取行数上限(内存防线,归并在应用层)


class SchemaTablePair(NamedTuple):
    """(schema, table) 二元键视图(同款防跨 schema 同名表互遮;
    NamedTuple:与元组直接判等,测试与序列化两侧零适配)。"""

    schema_name: str
    table_name: str

    def key(self) -> tuple[str, str]:
        return (self.schema_name, self.table_name)


@dataclass(frozen=True)
class FailureSignal:
    """失败信号输入行(chat_message 聚合投影;按 created_at 降序由仓储保证)。"""

    question: str
    status: str
    rating: str  # 'UP'/'DOWN';未评分为空串
    has_corrected_sql: bool
    created_at: datetime


@dataclass(frozen=True)
class FailureCluster:
    """归并后的问题簇:同归一化文本计一次,取最近一次的状态/时间。"""

    question: str
    count: int
    last_status: str
    last_seen: datetime
    rating: str
    has_corrected_sql: bool


@dataclass(frozen=True)
class MissingColumnsEntry:
    """缺标签列按表聚合:内省列名集合 − 知识条目列标签键集合。"""

    schema_name: str
    table_name: str
    missing: list[str]
    missing_count: int


@dataclass(frozen=True)
class HistorySample:
    """query_history 历史路候选(来源透出;原生分透出)。"""

    question: str
    sql_text: str
    source_type: str  # EXECUTION | FEEDBACK
    score: float = 0.0  # 历史路余弦相似度(1-(embedding<=>v))


@dataclass(frozen=True)
class HistoryOverview:
    """few-shot 通道概况(query_history 成功沉淀)。"""

    success_count: int
    distinct_questions: int
    cap: int


@dataclass(frozen=True)
class CoverageReport:
    """覆盖报告领域视图(纯聚合产物,零持久化;五节:四类空白+few-shot 概况)。"""

    datasource_key: str
    unregistered_tables: list[SchemaTablePair]
    unregistered_total: int
    structure_only_tables: list[SchemaTablePair]
    structure_only_total: int
    missing_columns: list[MissingColumnsEntry]
    missing_columns_total: int
    no_sample_tables: list[SchemaTablePair]
    no_sample_total: int
    history: HistoryOverview
    failure_clusters: list[FailureCluster]
    failure_cluster_total: int


def diff_unregistered_tables(
    tables: list[IntrospectedTable], registered_keys: set[tuple[str, str]]
) -> list[SchemaTablePair]:
    """零覆盖表:内省扫描集 − 已登记键(二元键,跨 schema 同名表不遮蔽)。"""
    return [
        SchemaTablePair(t.schema_name, t.table_name)
        for t in tables
        if (t.schema_name, t.table_name) not in registered_keys
    ]


def find_structure_only_entries(entries: list[KnowledgeEntry]) -> list[SchemaTablePair]:
    """纯结构登记表:来源为 INTROSPECTION(润色降级,零语义内容)。"""
    return [
        SchemaTablePair(e.schema_name, e.table_name)
        for e in entries
        if e.source_type == "INTROSPECTION"
    ]


def find_missing_column_labels(
    tables: list[IntrospectedTable], entries: list[KnowledgeEntry]
) -> list[MissingColumnsEntry]:
    """缺标签列:按登记表对齐内省列名集与列标签键集,差集即缺失清单。"""
    by_key = {(e.schema_name, e.table_name): e for e in entries}
    out: list[MissingColumnsEntry] = []
    for t in tables:
        entry = by_key.get((t.schema_name, t.table_name))
        if entry is None:
            continue
        missing = [c for c in t.columns_comment if c not in entry.columns_comment]
        if missing:
            out.append(
                MissingColumnsEntry(
                    schema_name=t.schema_name,
                    table_name=t.table_name,
                    missing=missing,
                    missing_count=len(missing),
                )
            )
    return out


def find_no_sample_tables(entries: list[KnowledgeEntry]) -> list[SchemaTablePair]:
    """示例缺口:示例 SQL 为空的登记表(含纯结构登记)。"""
    return [SchemaTablePair(e.schema_name, e.table_name) for e in entries if not e.sample_queries]


def normalize_question(question: str) -> str:
    """失败问题归一化:去全部空白+忽略大小写(中文问句空格多为误植;
    宁并可并不可漏,语义聚类明确不做)。"""
    return re.sub(r"\s+", "", question).lower()


def cluster_failure_signals(
    signals: list[FailureSignal],
) -> tuple[list[FailureCluster], int]:
    """失败信号按归一化文本归并;返回 (封顶簇清单, 全量簇数)。

    最近状态/时间取该组 created_at 最晚的信号(严格大于才覆盖:仓储输入为
    created_at DESC+id DESC,首见行即最新,并列 created_at 时保留先见行——
    id tiebreak 下恰为最新插入);簇序:次数降序 → 最近时间降序 → 文本升序
    (确定性)。NO_DATA(如实弃答)作为知识未接住的弱信号计入,状态分列。
    """
    latest: dict[str, FailureSignal] = {}
    counts: dict[str, int] = {}
    for s in signals:
        norm = normalize_question(s.question)
        if not norm:
            continue
        counts[norm] = counts.get(norm, 0) + 1
        prev = latest.get(norm)
        if prev is None or s.created_at > prev.created_at:
            latest[norm] = s
    clusters = [
        FailureCluster(
            question=latest[norm].question,
            count=counts[norm],
            last_status=latest[norm].status,
            last_seen=latest[norm].created_at,
            rating=latest[norm].rating,
            has_corrected_sql=latest[norm].has_corrected_sql,
        )
        for norm in latest
    ]
    clusters.sort(key=lambda c: (-c.count, -c.last_seen.timestamp(), c.question))
    total = len(clusters)
    return clusters[:COVERAGE_FAILURE_CLUSTER_CAP], total


def build_coverage_report(
    *,
    datasource_key: str,
    tables: list[IntrospectedTable],
    entries: list[KnowledgeEntry],
    signals: list[FailureSignal],
    history: HistoryOverview,
) -> CoverageReport:
    """报告组装唯一入口:四类空白差集/归并 + 封顶与全量计数(纯函数)。"""
    registered_keys = {(e.schema_name, e.table_name) for e in entries}
    unregistered = diff_unregistered_tables(tables, registered_keys)
    structure_only = find_structure_only_entries(entries)
    missing_cols = find_missing_column_labels(tables, entries)
    no_sample = find_no_sample_tables(entries)
    clusters, cluster_total = cluster_failure_signals(signals)
    return CoverageReport(
        datasource_key=datasource_key,
        unregistered_tables=unregistered[:COVERAGE_LIST_CAP],
        unregistered_total=len(unregistered),
        structure_only_tables=structure_only[:COVERAGE_LIST_CAP],
        structure_only_total=len(structure_only),
        missing_columns=missing_cols[:COVERAGE_MISSING_COLS_CAP],
        missing_columns_total=len(missing_cols),
        no_sample_tables=no_sample[:COVERAGE_LIST_CAP],
        no_sample_total=len(no_sample),
        history=history,
        failure_clusters=clusters,
        failure_cluster_total=cluster_total,
    )
