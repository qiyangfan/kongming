"""rag 领域模型与纯函数(009):零框架依赖(宪法 Article II)。

覆盖优先级(knowledge spec):INTROSPECTION < AI_GENERATED < MANUAL_UPLOAD <
MANUAL_EDIT < FEEDBACK_REFINED——自动来源(扫描/润色)不覆盖更高优先级既有条目。
召回参数与上游同构:topK=5、RRF k=60(query_history 独立强 few-shot 通道不进融合)。
"""

import re
from dataclasses import dataclass, field

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
# query_history 每 (tenant, datasource_key) 维度总量上限(016 治理):
# 防膨胀闸门而非常态工作集(上游无上限实测沉淀 <百行/租户源),超出淘汰最旧
QUERY_HISTORY_CAP = 500


@dataclass(frozen=True)
class TableBlock:
    """parser 切块产物:一张表的知识条目内容(schema_name 为请求表单维度)。"""

    table_name: str
    table_comment: str = ""
    # default_factory 走参数化泛型别名(bare dict/list 推断出 Unknown 元素)
    columns_comment: dict[str, str] = field(default_factory=dict[str, str])
    sample_queries: list[str] = field(default_factory=list[str])
    alias: str = ""  # 037:业务称谓别名


@dataclass(frozen=True)
class KnowledgeEntry:
    """知识条目(管理面/召回视图;永不含向量原始值)。037:新增 alias 业务称谓。"""

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
    alias: str = ""  # 037:业务称谓别名


@dataclass(frozen=True)
class RecallHit:
    """单路召回命中(rank 从 1 起;RRF 输入形态)。"""

    table_name: str
    schema_name: str = ""


@dataclass(frozen=True)
class IntrospectedTable:
    """全量内省产物(fetch-metadata 输入;与 meta 通道同口径:BASE TABLE+VIEW+注释)。"""

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


# 037 alias 结构防御(R1-F1 纵深):括号/换行/竖线会破坏 schema_hint 行结构
# 「schema.table(alias)(cols) -- comment」并可借换行伪造「fake.table(」形态
# 注入 023 表名提取;含违禁字符的别名整体置空(截半句更误导)。
_ALIAS_FORBIDDEN = re.compile(r"[\r\n()（）|]")


def clean_alias(alias: str) -> str:
    """别名清洗:strip+压缩内部空白;含结构性违禁字符(括号/换行/竖线)置空。"""
    folded = " ".join(alias.split())
    return "" if _ALIAS_FORBIDDEN.search(folded) else folded


def build_embedding_text(
    table_name: str, table_comment: str, columns_comment: dict[str, str], *, alias: str = ""
) -> str:
    """embedding 输入文本:表名+alias+表说明+字段说明(037 alias 增强输入侧匹配)。

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
    """通用 RRF(任意 id 键;024 文档子块融合)——与 rrf_fuse 同算法参数,
    同分按首现顺序稳定排序(不动既有函数)。"""
    scores: dict[str, float] = {}
    for hits in ranked_lists:
        for rank, hit_id in enumerate(hits, start=1):
            scores[hit_id] = scores.get(hit_id, 0.0) + 1.0 / (k + rank)
    ordered = sorted(scores.items(), key=lambda kv: -kv[1])
    return [key for key, _ in ordered[:top]]


def build_schema_hint(entries: list[KnowledgeEntry]) -> str:
    """召回条目 → generate 的 schema 提示文本(纯函数)。

    形态(037 顺延):`表名(alias):列... -- 表说明`;空 alias 不显括号;
    空说明只留列名;表内嵌示例 SQL 逐条追加。alias 经 clean_alias 纵深清洗。
    """
    lines: list[str] = []
    for e in entries:
        cols = ", ".join(
            f"{name}({desc})" if desc else name for name, desc in e.columns_comment.items()
        )
        # 037:alias 优先显示在表名后括号内
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


# ---- 领域错误(router 统一映射状态码;message 即用户可见文案) ----


class RagError(Exception):
    """rag 域错误基类。"""


# 400(同形,不区分不存在/禁用/越属——防 key 探测)


class DatasourceInvalid(RagError):
    """数据源不存在/禁用/不归属当前租户(与执行路由同形语义)。"""


class InvalidSchemaName(RagError):
    """schema_name 非合法 PG 标识符。"""


class SchemaNotAllowed(RagError):
    """schema_name 语法合法但不在该数据源可扫描集合(040 写边界闸)。"""


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
    """知识条目不存在或不属于当前租户(同形,防探测)。"""
