"""semantic 领域模型(023):表关系/指标/注入文本格式化(零框架)。"""

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
    business_terms: list[str] = field(default_factory=list)
    enabled: bool = True


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
    lines = []
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
