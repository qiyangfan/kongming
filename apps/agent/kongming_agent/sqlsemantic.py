"""执行前确定性 SQL 语义校验:只读 AST、结构化图与问句,不调用模型。

规则只处理结构上可证明的违规;图不完整或角色/路径不确定时不做猜测性拒绝。
输出违规类别与定向修正指令,交由 selfheal 喂回生成,耗尽后 fail-closed。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, cast

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from kongming_agent.schemagraph import (
    ROLE_AMOUNT,
    ROLE_DIMENSION,
    ROLE_EVENT,
    GraphTable,
    SchemaGraph,
    TableKey,
    canonical_path,
    path_tables,
)

CATEGORY_TABLE = "TABLE_NOT_IN_GRAPH"
CATEGORY_COLUMN = "COLUMN_NOT_IN_GRAPH"
CATEGORY_AMOUNT_COUNT = "AMOUNT_TABLE_COUNT"
CATEGORY_FANOUT = "EVENT_COUNT_FANOUT"
CATEGORY_AMOUNT_TIME = "AMOUNT_TIME_FOR_EVENT"
CATEGORY_ATTRIBUTION = "ATTRIBUTION_BYPASS"

_GUIDANCE = {
    CATEGORY_TABLE: "只使用结构化图中存在的表",
    CATEGORY_COLUMN: "只使用对应表中存在的列,核对拼写",
    CATEGORY_AMOUNT_COUNT: "笔数/次数回到事件角色表计数,金额只在金额表聚合",
    CATEGORY_FANOUT: "按事件主键 COUNT(DISTINCT 主键) 或先子查询预聚合后再计数",
    CATEGORY_AMOUNT_TIME: "按问句要求使用事件表的发生时间列分组",
    CATEGORY_ATTRIBUTION: "沿事件表自身的归属外键路径关联员工/门店等维度",
}


@dataclass(frozen=True)
class Violation:
    category: str
    detail: str
    guidance: str


@dataclass(frozen=True)
class SemanticGuardResult:
    passed: bool
    violations: tuple[Violation, ...] = ()

    @property
    def categories(self) -> list[str]:
        return list(dict.fromkeys(v.category for v in self.violations))

    def render_reason(self) -> str:
        parts = [f"语义预检未过（{', '.join(self.categories)}）"]
        parts.extend(f"- {v.detail}；修正：{v.guidance}" for v in self.violations)
        return "\n".join(parts)


@dataclass
class _QueryFacts:
    expression: Any
    graph: SchemaGraph
    aliases: dict[str, TableKey | None]
    tables: set[TableKey]
    projection_aliases: set[str]
    violations: list[Violation] = field(default_factory=list[Violation])

    def add(self, category: str, detail: str) -> None:
        item = Violation(category=category, detail=detail, guidance=_GUIDANCE[category])
        if item not in self.violations:
            self.violations.append(item)


def validate_semantics(sql: str, graph: SchemaGraph, question: str) -> SemanticGuardResult:
    """执行前语义校验入口;解析失败交给既有 sqlguard,重复解析错误不重复报。"""
    if not graph.complete or not graph.tables:
        return SemanticGuardResult(passed=True)
    try:
        expression = sqlglot.parse_one(sql, dialect="postgres")
    except SqlglotError:
        return SemanticGuardResult(passed=True)
    facts = _query_facts(expression, graph)
    _check_graph_scope(facts)
    counts = _check_counts(facts)
    _check_amount_time(facts)
    _check_attribution(facts, question, counts=counts)
    return SemanticGuardResult(passed=not facts.violations, violations=tuple(facts.violations))


def _query_facts(expression: Any, graph: SchemaGraph) -> _QueryFacts:
    cte_names = {cte.alias_or_name for cte in expression.find_all(exp.CTE)}
    aliases: dict[str, TableKey | None] = {}
    tables: set[TableKey] = set()
    for table in expression.find_all(exp.Table):
        if not table.db and table.name in cte_names:
            continue
        key = _resolve_table(graph, str(table.db or ""), table.name)
        if key is None:
            continue
        tables.add(key)
        alias = table.alias_or_name
        previous = aliases.get(alias, key)
        aliases[alias] = key if previous == key else None
    projection_aliases = {str(a.alias) for a in expression.find_all(exp.Alias) if a.alias}
    return _QueryFacts(
        expression=expression,
        graph=graph,
        aliases=aliases,
        tables=tables,
        projection_aliases=projection_aliases,
    )


def _resolve_table(graph: SchemaGraph, schema: str, name: str) -> TableKey | None:
    if schema:
        key = (schema, name)
        return key if graph.table(key) is not None else None
    matches = [t.key for t in graph.tables if t.table_name == name]
    return matches[0] if len(matches) == 1 else None


def _check_graph_scope(facts: _QueryFacts) -> None:
    if not facts.graph.complete:
        return
    _check_unknown_tables(facts)
    _check_columns(facts)


def _check_unknown_tables(facts: _QueryFacts) -> None:
    cte_names = {cte.alias_or_name for cte in facts.expression.find_all(exp.CTE)}
    for table in facts.expression.find_all(exp.Table):
        if not _is_real_table(table):
            continue
        if not table.db and table.name in cte_names:
            continue
        schema = str(table.db or "")
        if schema:
            known = facts.graph.table((schema, table.name)) is not None
        else:
            # 同名表跨 schema 时未限定名由 search_path 解析;图里存
            # 在该名字但无法唯一落表,属于歧义而非图外,不能硬拒。
            known = any(t.table_name == table.name for t in facts.graph.tables)
        if not known:
            facts.add(CATEGORY_TABLE, f"表 {table.sql()} 不在结构化图中")


def _check_columns(facts: _QueryFacts) -> None:
    for column in facts.expression.find_all(exp.Column):
        if column.table:
            _check_qualified_column(facts, column)
        elif column.name not in facts.projection_aliases:
            _check_unqualified_column(facts, column)


def _check_qualified_column(facts: _QueryFacts, column: exp.Column) -> None:
    key = facts.aliases.get(column.table)
    if key is None:
        return
    table = facts.graph.table(key)
    if table is not None and column.name not in table.column_names():
        facts.add(
            CATEGORY_COLUMN,
            f"列 {column.table}.{column.name} 不在表 {key[0]}.{key[1]} 中",
        )


def _check_unqualified_column(facts: _QueryFacts, column: exp.Column) -> None:
    select = column.find_ancestor(exp.Select)
    if select is not None and _has_opaque_source(select, facts.aliases):
        return
    local = _local_tables(select, facts.aliases) if select is not None else facts.tables
    if local and not any(
        (table := facts.graph.table(key)) is not None and column.name in table.column_names()
        for key in local
    ):
        facts.add(CATEGORY_COLUMN, f"列 {column.name} 不在当前查询的图中表内")


def _local_tables(select: exp.Select, aliases: dict[str, TableKey | None]) -> set[TableKey]:
    """当前 SELECT 的直接 FROM/JOIN 表;子查询源不展开(计数从子查询出时跳过)。"""
    out: set[TableKey] = set()
    from_ = select.args.get("from_")
    table = _source_table(from_)
    if table is not None:
        key = _table_key(table, aliases)
        if key is not None:
            out.add(key)
    for join in cast("list[exp.Join]", select.args.get("joins") or []):
        table = _source_table(join)
        if table is not None:
            key = _table_key(table, aliases)
            if key is not None:
                out.add(key)
    return out


def _source_table(node: object) -> exp.Table | None:
    if not isinstance(node, exp.From) and not isinstance(node, exp.Join):
        return None
    this = node.this
    return this if isinstance(this, exp.Table) else None


def _is_real_table(table: exp.Table) -> bool:
    """表函数(VALUES/generate_series 等)也解析成 Table,但不是图中实体表。"""
    return isinstance(table.this, exp.Identifier)


def _source_is_opaque(node: object, aliases: dict[str, TableKey | None]) -> bool:
    source = _source_table(node)
    if source is None or not _is_real_table(source):
        return True
    return aliases.get(source.alias_or_name) is None


def _has_opaque_source(select: exp.Select, aliases: dict[str, TableKey | None]) -> bool:
    """FROM/JOIN 含子查询、CTE、表函数或未解析表时,未限定列可能来自图外,
    不能据此判定列越界或归属。"""
    from_ = select.args.get("from_")
    if from_ is not None and _source_is_opaque(from_, aliases):
        return True
    joins = cast("list[exp.Join]", select.args.get("joins") or [])
    return any(_source_is_opaque(join, aliases) for join in joins)


def _table_key(table: exp.Table, aliases: dict[str, TableKey | None]) -> TableKey | None:
    return aliases.get(table.alias_or_name)


def _column_key(column: exp.Column, aliases: dict[str, TableKey | None]) -> TableKey | None:
    return aliases.get(column.table) if column.table else None


def _count_arg(count: exp.Count) -> tuple[Any | None, bool]:
    arg = count.this
    if isinstance(arg, exp.Distinct):
        args = list(arg.expressions)
        return (args[0] if args else None), True
    if isinstance(arg, exp.Star):
        return None, False
    return arg, False


def _count_target(
    facts: _QueryFacts, count: exp.Count, local: set[TableKey]
) -> tuple[TableKey | None, bool]:
    select = count.find_ancestor(exp.Select)
    if select is None:
        return None, False
    arg, distinct = _count_arg(count)
    if isinstance(arg, exp.Column):
        if arg.table:
            target = _column_key(arg, facts.aliases)
        elif _has_opaque_source(select, facts.aliases):
            target = None
        else:
            target = _unqualified_key(facts, arg.name, local)
        return (target, distinct) if target in local else (None, distinct)
    source = _source_table(select.args.get("from_"))
    if source is None:
        return None, distinct  # 子查询/CTE 出数:不把外层计数归到某张明细表
    return _table_key(source, facts.aliases), distinct


def _has_fk_to(facts: _QueryFacts, source: TableKey, target: TableKey) -> bool:
    return any(
        fk.source_key == source and fk.target_key == target for fk in facts.graph.foreign_keys
    )


def _check_counts(facts: _QueryFacts) -> dict[TableKey, int]:
    """返回被计数的事件表次数(归属规则据此判定是否在统计事件)。"""
    event_counts: dict[TableKey, int] = {}
    for count in facts.expression.find_all(exp.Count):
        select = count.find_ancestor(exp.Select)
        if select is None:
            continue
        local = _local_tables(select, facts.aliases)
        if not local:
            continue
        target, distinct = _count_target(facts, count, local)
        if target is None:
            continue
        table = facts.graph.table(target)
        if table is None:
            continue
        if table.role == ROLE_AMOUNT and not _select_has_event_count(facts, select):
            _record_amount_count(facts, table, local)
        if table.role != ROLE_EVENT:
            continue
        event_counts[target] = event_counts.get(target, 0) + 1
        if not distinct:
            _record_fanout(facts, table, target, local)
    return event_counts


def _select_has_event_count(facts: _QueryFacts, select: exp.Select) -> bool:
    """同一 SELECT 已显式统计事件表时,并行的金额表计数视为口径内的支付笔数。"""
    for other in facts.expression.find_all(exp.Count):
        if other.find_ancestor(exp.Select) is not select:
            continue
        local = _local_tables(select, facts.aliases)
        target, _distinct = _count_target(facts, other, local)
        table = facts.graph.table(target) if target is not None else None
        if table is not None and table.role == ROLE_EVENT:
            return True
    return False


def _record_amount_count(facts: _QueryFacts, table: GraphTable, local: set[TableKey]) -> None:
    if any((t := facts.graph.table(k)) is not None and t.role == ROLE_EVENT for k in local):
        facts.add(
            CATEGORY_AMOUNT_COUNT,
            f"按金额表 {table.schema_name}.{table.table_name} 计数冒充事件笔数",
        )


def _record_fanout(
    facts: _QueryFacts, table: GraphTable, target: TableKey, local: set[TableKey]
) -> None:
    for key in sorted(local):
        if key != target and _has_fk_to(facts, key, target):
            facts.add(
                CATEGORY_FANOUT,
                f"计数事件表 {table.schema_name}.{table.table_name} 时关联了可能一对多的 "
                f"{key[0]}.{key[1]} 且未去重",
            )
            return


def _check_amount_time(facts: _QueryFacts) -> None:
    for select in facts.expression.find_all(exp.Select):
        local = _local_tables(select, facts.aliases)
        if not local:
            continue
        if not any((t := facts.graph.table(k)) is not None and t.role == ROLE_EVENT for k in local):
            continue
        group = select.args.get("group")
        if group is None:
            continue
        for column in _group_columns(select, group):
            if column.table:
                key = _column_key(column, facts.aliases)
            elif _has_opaque_source(select, facts.aliases):
                continue
            else:
                key = _unqualified_key(facts, column.name, local)
            table = facts.graph.table(key) if key is not None else None
            if (
                table is not None
                and table.role == ROLE_AMOUNT
                and _temporal_column(table, column.name)
            ):
                facts.add(
                    CATEGORY_AMOUNT_TIME,
                    f"按金额表 {table.schema_name}.{table.table_name} 的时间列分组冒充事件时间",
                )
                return


def _temporal_column(table: GraphTable, name: str) -> bool:
    """列名/类型呈业务时间形态;last_update 等审计时间不算事件时间。"""
    column = next((c for c in table.columns if c.name == name), None)
    if column is None:
        return False
    lowered = name.lower()
    if "update" in lowered or "modified" in lowered:
        return False
    return any(x in column.data_type.lower() for x in ("date", "time")) or lowered.endswith("_at")


def _group_columns(select: exp.Select, group: object) -> list[exp.Column]:
    """GROUP BY 的列引用;数字序号先解析到投影表达式(1=首列)。"""
    expressions = cast("list[Any]", getattr(group, "expressions", []) or [])
    projections = list(select.expressions)
    out: list[exp.Column] = []
    for item in expressions:
        if isinstance(item, exp.Literal) and item.is_int:
            index = int(item.name) - 1
            if 0 <= index < len(projections):
                out.extend(projections[index].find_all(exp.Column))
            continue
        out.extend(item.find_all(exp.Column))
    return out


def _unqualified_key(facts: _QueryFacts, name: str, local: Iterable[TableKey]) -> TableKey | None:
    hits = [
        key
        for key in local
        if (table := facts.graph.table(key)) is not None and name in table.column_names()
    ]
    return hits[0] if len(hits) == 1 else None


def _check_attribution(facts: _QueryFacts, question: str, *, counts: dict[TableKey, int]) -> None:
    events = sorted(
        key
        for key in facts.tables
        if (table := facts.graph.table(key)) is not None and table.role == ROLE_EVENT
    )
    if not events:
        return
    has_group = any(select.args.get("group") for select in facts.expression.find_all(exp.Select))
    if not counts and not has_group:
        return
    for event in events:
        for other in sorted(facts.tables):
            other_table = facts.graph.table(other)
            if other == event or other_table is None or _mentioned(question, other_table):
                continue
            violation = _bypass_violation(facts, event, other)
            if violation is not None:
                facts.add(CATEGORY_ATTRIBUTION, violation)


def _bypass_violation(facts: _QueryFacts, event: TableKey, other: TableKey) -> str | None:
    """存在唯一可判定的事件归属路径时,不得改走路径外的表取维度。"""
    for fk in facts.graph.outgoing(other):
        target = fk.target_key
        target_table = facts.graph.table(target)
        if target_table is None or target_table.role != ROLE_DIMENSION:
            continue
        canonical = canonical_path(facts.graph, event, target, max_depth=3)
        if canonical is None:
            continue  # 多路径且无唯一证据:不硬拒
        if other in path_tables(canonical) or other == target:
            continue
        if _column_used(facts, other, fk.source_columns):
            return (
                f"统计事件 {event[0]}.{event[1]} 时经 {other[0]}.{other[1]} "
                f"取维度 {target[0]}.{target[1]},绕过了事件表自身外键路径"
            )
    return None


def _column_used(facts: _QueryFacts, key: TableKey, names: Sequence[str]) -> bool:
    wanted = set(names)
    for column in facts.expression.find_all(exp.Column):
        if column.name not in wanted:
            continue
        if column.table:
            if facts.aliases.get(column.table) == key:
                return True
            continue
        select = column.find_ancestor(exp.Select)
        if select is None or _has_opaque_source(select, facts.aliases):
            continue
        local = _local_tables(select, facts.aliases)
        if key in local and _unqualified_key(facts, column.name, local) == key:
            return True
    return False


def _mentioned(question: str, table: object) -> bool:
    text = question.lower()
    for attr in ("table_name", "comment"):
        value = str(getattr(table, attr, "") or "").strip().lower()
        if len(value) >= 2 and value in text:
            return True
    return False
