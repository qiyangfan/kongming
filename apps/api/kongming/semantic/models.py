"""semantic 领域模型:表关系/指标/注入文本格式化(零框架)。"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TableRelation:
    source_table: str  # schema.table 全名
    target_table: str
    join_keys: list[dict[str, str]]  # [{left_col, right_col}]
    source: str  # introspection / manual


@dataclass(frozen=True)
class MetricDef:
    metric_name: str
    sql_template: str
    business_terms: list[str] = field(default_factory=list[str])
    enabled: bool = True
    # 口径依据透出(仓储 SQL 已 SELECT,被丢)
    metric_id: str = ""
    updated_at: object | None = None


@dataclass(frozen=True)
class CallerScope:
    """调用者 data_scope 解析结果。

    universal=True=有效范围 ALL 档(全部指标可见);False 时仅 dept_ids 集合内
    的 DEPT 档指标 + 全体 ALL 档指标可见(空集合=SELF/无身份/CUSTOM 空集读面
    降级,只余 ALL 档)。"""

    universal: bool
    dept_ids: tuple[str, ...] = ()


def format_relations_hint(relations: list[TableRelation]) -> str:
    """关系集 → JOIN path 文字段(纯函数;仅召回集合内的两两关系由调用方收窄)。"""
    lines: list[str] = []
    for r in relations:
        conds = " AND ".join(
            f"{r.source_table}.{k['left_col']} = {r.target_table}.{k['right_col']}"
            for k in r.join_keys
        )
        lines.append(f"{r.source_table} JOIN {r.target_table} ON {conds}")
    return "\n".join(lines)


def format_metric_hints(metrics: list[MetricDef]) -> str:
    """命中指标 → 口径文字段(纯函数);sql_template 仅为提示文字,永不执行。"""
    lines: list[str] = []
    for m in metrics:
        lines.append(f"{m.metric_name}: {m.sql_template}")
    return "\n".join(lines)


def match_metrics(question: str, metrics: list[MetricDef], top_k: int = 5) -> list[MetricDef]:
    """问句命中启用指标(纯函数):metric_name 或任一 business_term 被子串包含;
    字母序(list_metrics 排序),截 top_k。"""
    hits: list[MetricDef] = []
    for m in metrics:
        if not m.enabled:
            continue
        terms = [m.metric_name, *m.business_terms]
        if any(t and t in question for t in terms):
            hits.append(m)
    return hits[:top_k]


@dataclass(frozen=True)
class MetricHit:
    """语义层命中的结构化投影:标识/名称/口径表达式/修订时间。
    提示词文字段由此列表派生(单一来源,杜绝两处口径漂移)。"""

    metric_id: str
    metric_name: str
    sql_template: str
    updated_at: str | None = None

    def as_dict(self) -> dict[str, object]:
        """契约/落库投影(datetime → ISO 串;None 透传)。"""
        updated = self.updated_at  # 已为 str | None(调用方构造时归一)
        return {
            "metric_id": self.metric_id,
            "metric_name": self.metric_name,
            "sql_template": self.sql_template,
            "updated_at": updated,
        }


def project_metric_hits(hits: list[MetricDef]) -> list[MetricHit]:
    """命中域对象 → 结构化投影(纯函数)。"""
    out: list[MetricHit] = []
    for m in hits:
        updated = None if m.updated_at is None else str(m.updated_at)
        out.append(
            MetricHit(
                metric_id=m.metric_id,
                metric_name=m.metric_name,
                sql_template=m.sql_template,
                updated_at=updated,
            )
        )
    return out
