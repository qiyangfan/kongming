"""执行前确定性 SQL 语义校验:只读 AST、结构化图与问句,不调用模型。

规则只处理结构上可证明的违规;图不完整或角色/路径不确定时不做猜测性拒绝。
输出违规类别与定向修正指令,交由 selfheal 喂回生成,耗尽后 fail-closed。
"""

from __future__ import annotations

import re
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
    ROLE_UNKNOWN,
    GraphForeignKey,
    GraphTable,
    SchemaGraph,
    TableKey,
    canonical_path,
    path_tables,
)
from kongming_agent.sqlbridge import bridge_join_only_links_base

CATEGORY_TABLE = "TABLE_NOT_IN_GRAPH"
CATEGORY_COLUMN = "COLUMN_NOT_IN_GRAPH"
CATEGORY_AMOUNT_COUNT = "AMOUNT_TABLE_COUNT"
CATEGORY_FANOUT = "EVENT_COUNT_FANOUT"
CATEGORY_AMOUNT_TIME = "AMOUNT_TIME_FOR_EVENT"
CATEGORY_ATTRIBUTION = "ATTRIBUTION_BYPASS"
CATEGORY_BRIDGE_JOIN = "BRIDGE_JOIN_LOSS"

# 时间口径识别:只处理明确的「按/每 + 时间单位」形态,避免把「上个月」一类
# 过滤条件误当分组口径。标签全部来自表的注释/名称,不写业务词。
_TIME_UNITS = (
    "monthly",
    "quarterly",
    "weekly",
    "daily",
    "yearly",
    "month",
    "quarter",
    "week",
    "day",
    "year",
    "月份",
    "季度",
    "月",
    "年",
    "周",
    "日",
    "天",
)
_GROUPING_MARKS = ("按", "每", "by", "per", "each")
_CJK = re.compile(r"[\u4e00-\u9fff]")
_TIME_WINDOW_CHARS = 10

_GUIDANCE = {
    CATEGORY_TABLE: "只使用结构化图中存在的表",
    CATEGORY_COLUMN: "只使用对应表中存在的列,核对拼写",
    CATEGORY_AMOUNT_COUNT: "笔数/次数回到事件角色表计数,金额只在金额表聚合",
    CATEGORY_FANOUT: "按事件主键 COUNT(DISTINCT 主键) 或先子查询预聚合后再计数",
    CATEGORY_AMOUNT_TIME: "按问句要求使用事件表的发生时间列分组",
    CATEGORY_ATTRIBUTION: "沿事件表自身的归属外键路径关联员工/门店等维度",
    CATEGORY_BRIDGE_JOIN: (
        "基表到目标维度有直接外键路径时不要经另一事件/金额表内连接桥接;"
        "直接关联基表外键;确需保留无匹配行时用 LEFT JOIN 并披露空维度"
    ),
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

    def add(self, category: str, detail: str, guidance: str | None = None) -> None:
        item = Violation(
            category=category,
            detail=detail,
            guidance=guidance if guidance else _GUIDANCE[category],
        )
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
    _check_amount_time(facts, question)
    _check_attribution(facts, question, counts=counts)
    return SemanticGuardResult(passed=not facts.violations, violations=tuple(facts.violations))


def validate_bridge_joins(sql: str, graph: SchemaGraph, question: str = "") -> SemanticGuardResult:
    """独立桥接丢行预检:需要完整外键图,不与会把金额表当非事件的规则混用。"""
    if not graph.complete or not graph.tables:
        return SemanticGuardResult(passed=True)
    try:
        expression = sqlglot.parse_one(sql, dialect="postgres")
    except SqlglotError:
        return SemanticGuardResult(passed=True)
    facts = _query_facts(expression, graph)
    _check_bridge_joins(facts, question)
    return SemanticGuardResult(passed=not facts.violations, violations=tuple(facts.violations))


def repair_event_count_fanout(sql: str, graph: SchemaGraph) -> str | None:
    """把可确定的非去重事件主键计数改为 COUNT(DISTINCT 主键)。

    只在事件表主键单列、计数参数恰为该主键、且同一 SELECT 关联了可能一对多
    的表时改写;COUNT(*) 或多事件表等无法唯一确定主键的形态仍交给自愈。
    返回改写后的 SQL;没有可安全改写处返回 None。
    """
    if not graph.complete or not graph.tables:
        return None
    try:
        expression = sqlglot.parse_one(sql, dialect="postgres")
    except SqlglotError:
        return None
    facts = _query_facts(expression, graph)
    changed = False
    counts = list(expression.find_all(exp.Count))
    for count in counts:
        select = count.find_ancestor(exp.Select)
        if select is None:
            continue
        local = _local_tables(select, facts.aliases)
        if not local:
            continue
        target, distinct = _count_target(facts, count, local)
        if target is None or distinct:
            continue
        table = facts.graph.table(target)
        if (
            table is None
            or table.role != ROLE_EVENT
            or len(table.primary_key) != 1
            or not _has_incoming_fanout(facts, target, local)
        ):
            continue
        arg, _distinct = _count_arg(count)
        if not isinstance(arg, exp.Column) or arg.name != table.primary_key[0]:
            continue
        count.set("this", exp.Distinct(expressions=[arg.copy()]))
        changed = True
    if not changed:
        return None
    return expression.sql(dialect="postgres")


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
            # 下发的是召回子图:子图外但全图已知的表属「未召回」,不是图外
            known = facts.graph.table((schema, table.name)) is not None or facts.graph.knows(
                (schema, table.name)
            )
        else:
            # 同名表跨 schema 时未限定名由 search_path 解析;图里存
            # 在该名字但无法唯一落表,属于歧义而非图外,不能硬拒。
            known = any(t.table_name == table.name for t in facts.graph.tables) or any(
                name == table.name for _, name in facts.graph.known_tables
            )
        if not known:
            facts.add(CATEGORY_TABLE, f"表 {table.sql()} 不在数据源的已知表中")


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


def _has_incoming_fanout(facts: _QueryFacts, target: TableKey, local: set[TableKey]) -> bool:
    """当前 SELECT 是否有其他表经外键指向 target(可能一对多)。"""
    return any(key != target and _has_fk_to(facts, key, target) for key in local)


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
    events = [
        key
        for key in sorted(local)
        if (t := facts.graph.table(key)) is not None and t.role == ROLE_EVENT
    ]
    if not events:
        return
    event_table = facts.graph.table(events[0])
    event_label = f"{events[0][0]}.{events[0][1]}"
    pk = event_table.primary_key[0] if event_table and event_table.primary_key else "主键"
    facts.add(
        CATEGORY_AMOUNT_COUNT,
        f"按金额表 {table.schema_name}.{table.table_name} 计数冒充事件笔数(事件表 {event_label})",
        guidance=f"笔数按事件表 {event_label} 计数(COUNT(DISTINCT {pk}))；金额只在金额表聚合",
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


def _check_amount_time(facts: _QueryFacts, question: str) -> None:
    # 问句明确按金额表时间分组时,金额表时间列就是正确口径;不能按事件表存在就硬拒
    if _time_basis_hint(question, facts.graph) == "amount":
        return
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
                    f"按金额表 {table.schema_name}.{table.table_name} 的时间列 "
                    f"{column.name} 分组冒充事件时间",
                    guidance=_amount_time_guidance(facts, local),
                )
                return


def _amount_time_guidance(facts: _QueryFacts, local: set[TableKey]) -> str:
    """列出事件表可用的发生时间列,让自愈指令可直接执行而非泛泛改写。"""
    columns: list[str] = []
    for key in sorted(local):
        table = facts.graph.table(key)
        if table is None or table.role != ROLE_EVENT:
            continue
        columns.extend(
            f"{table.schema_name}.{table.table_name}.{column.name}"
            for column in table.columns
            if _temporal_column(table, column.name)
        )
    if not columns:
        return _GUIDANCE[CATEGORY_AMOUNT_TIME]
    return f"按问句要求改用事件表的发生时间列分组: {'、'.join(columns)}"


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
            # 角色未知的关联表可能是桥也可能是绕行,证据不足不做猜测性拒绝
            if other_table.role == ROLE_UNKNOWN:
                continue
            violation = _bypass_violation(facts, event, other)
            if violation is not None:
                facts.add(CATEGORY_ATTRIBUTION, violation)


def _bypass_violation(facts: _QueryFacts, event: TableKey, other: TableKey) -> str | None:
    """存在唯一可判定的事件归属路径时,不得改走路径外的表取维度。"""
    # 关系桥(如影片-分类关联)会同时用两条维度外键把维度接起来;这不是
    # 绕开事件归属路径取维度,而是经维度继续取下一层维度。多于一条维度
    # 外键参与查询时证据不唯一,按既有纪律不做猜测性拒绝。
    if _dimension_links_used(facts, other) > 1:
        return None
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


def _dimension_links_used(facts: _QueryFacts, key: TableKey) -> int:
    """该表已参与查询、指向维度表的外键列数量。"""
    links = 0
    for fk in facts.graph.outgoing(key):
        target = facts.graph.table(fk.target_key)
        if target is None or target.role != ROLE_DIMENSION:
            continue
        if _column_used(facts, key, fk.source_columns):
            links += 1
    return links


@dataclass(frozen=True)
class _BridgeJoin:
    """可确定会丢基表行的内连接桥:基表→桥→维度,但基表已有直连维度。"""

    select: exp.Select
    base_key: TableKey
    base_alias: str
    bridge_join: exp.Join
    bridge_key: TableKey
    target_key: TableKey
    direct_fk: GraphForeignKey


def _join_is_inner(join: exp.Join) -> bool:
    """LEFT/FULL/RIGHT 不因缺失匹配丢基表行;CROSS 也不是这里的桥接形态。"""
    side = str(join.args.get("side") or "").upper()
    kind = str(join.args.get("kind") or "").upper()
    return side not in ("LEFT", "FULL", "RIGHT") and kind != "CROSS"


def _used_outside_joins(facts: _QueryFacts, key: TableKey) -> bool:
    """桥表是否被投影/过滤/分组/聚合使用;只出现在 JOIN 条件才是纯桥。"""
    for column in facts.expression.find_all(exp.Column):
        if facts.aliases.get(column.table) != key:
            continue
        if column.find_ancestor(exp.Join) is None:
            return True
    return False


def _used_in_join(facts: _QueryFacts, key: TableKey, names: Sequence[str]) -> bool:
    wanted = set(names)
    for column in facts.expression.find_all(exp.Column):
        if facts.aliases.get(column.table) != key or column.name not in wanted:
            continue
        if column.find_ancestor(exp.Join) is not None:
            return True
    return False


def _direct_fk(graph: SchemaGraph, source: TableKey, target: TableKey) -> GraphForeignKey | None:
    matches = [
        fk
        for fk in graph.foreign_keys
        if fk.source_key == source
        and fk.target_key == target
        and len(fk.source_columns) == len(fk.target_columns)
    ]
    return matches[0] if len(matches) == 1 else None


def _bridge_join_candidates(facts: _QueryFacts, question: str = "") -> list[_BridgeJoin]:
    """找出「内连接另一事件/金额表只为取维度」且基表已有直连路径的形态。

    只在桥表列不被投影/过滤/聚合使用、问句未点名桥表、基表对目标维度有
    唯一直接外键时成立;LEFT JOIN、桥表参与度量、多路径等证据不足场景跳过。
    """
    out: list[_BridgeJoin] = []
    for select in facts.expression.find_all(exp.Select):
        base_node = _source_table(select.args.get("from_"))
        if base_node is None:
            continue
        base_key = _table_key(base_node, facts.aliases)
        if base_key is None:
            continue
        base_table = facts.graph.table(base_key)
        if base_table is None or base_table.role not in (ROLE_EVENT, ROLE_AMOUNT):
            continue
        for join in cast("list[exp.Join]", select.args.get("joins") or []):
            candidate = _bridge_candidate_for_join(
                facts,
                select=select,
                base_key=base_key,
                base_alias=base_node.alias_or_name,
                join=join,
                question=question,
            )
            if candidate is not None:
                out.append(candidate)
    return out


def _bridge_candidate_for_join(
    facts: _QueryFacts,
    *,
    select: exp.Select,
    base_key: TableKey,
    base_alias: str,
    join: exp.Join,
    question: str,
) -> _BridgeJoin | None:
    """单个 JOIN 的桥接判定;不满足结构证据返回 None。"""
    bridge_node = _source_table(join) if _join_is_inner(join) else None
    if bridge_node is None:
        return None
    bridge_key = _table_key(bridge_node, facts.aliases)
    if bridge_key is None or bridge_key == base_key:
        return None
    bridge_table = facts.graph.table(bridge_key)
    if bridge_table is None or bridge_table.role not in (ROLE_EVENT, ROLE_AMOUNT):
        return None
    if _mentioned(question, bridge_table) or _used_outside_joins(facts, bridge_key):
        return None
    for fk in facts.graph.outgoing(bridge_key):
        target = facts.graph.table(fk.target_key)
        if target is None or target.role != ROLE_DIMENSION:
            continue
        if not _used_in_join(facts, bridge_key, fk.source_columns):
            continue
        direct = _direct_fk(facts.graph, base_key, fk.target_key)
        if direct is None:
            continue
        return _BridgeJoin(
            select=select,
            base_key=base_key,
            base_alias=base_alias,
            bridge_join=join,
            bridge_key=bridge_key,
            target_key=fk.target_key,
            direct_fk=direct,
        )
    return None


def _check_bridge_joins(facts: _QueryFacts, question: str = "") -> None:
    for candidate in _bridge_join_candidates(facts, question):
        facts.add(
            CATEGORY_BRIDGE_JOIN,
            f"基表 {candidate.base_key[0]}.{candidate.base_key[1]} 经 "
            f"{candidate.bridge_key[0]}.{candidate.bridge_key[1]} 内连接取维度 "
            f"{candidate.target_key[0]}.{candidate.target_key[1]},但基表已有直连路径;"
            "INNER JOIN 会丢掉桥表无匹配的基表行",
        )


def repair_bridge_join(sql: str, graph: SchemaGraph, question: str = "") -> str | None:
    """把「基表→事件/金额桥→维度」的内连接改成基表直连维度。

    仅处理单列直连外键、目标维度 join 条件引用了桥列、桥列不被投影/过滤的
    简单形态;其余返回 None 交给 selfheal,不猜改写。
    """
    if not graph.complete or not graph.tables:
        return None
    try:
        expression = sqlglot.parse_one(sql, dialect="postgres")
    except SqlglotError:
        return None
    facts = _query_facts(expression, graph)
    for candidate in _bridge_join_candidates(facts, question):
        if not bridge_join_only_links_base(
            graph=facts.graph,
            aliases=facts.aliases,
            base_key=candidate.base_key,
            bridge_key=candidate.bridge_key,
            bridge_join=candidate.bridge_join,
        ):
            continue
        select = candidate.select
        joins = cast("list[exp.Join]", select.args.get("joins") or [])
        target_join = _find_join_to(facts, joins, candidate.target_key)
        if target_join is None or not _rewire_join_on(facts, candidate, target_join):
            continue
        if _residual_bridge_refs(facts, candidate):
            continue
        select.set("joins", [join for join in joins if join is not candidate.bridge_join])
        return expression.sql(dialect="postgres")
    return None


def _find_join_to(
    facts: _QueryFacts, joins: Sequence[exp.Join], target: TableKey
) -> exp.Join | None:
    for join in joins:
        node = _source_table(join)
        if node is not None and _table_key(node, facts.aliases) == target:
            return join
    return None


def _rewire_join_on(facts: _QueryFacts, candidate: _BridgeJoin, target_join: exp.Join) -> bool:
    """把目标维度 join 条件里的桥列替换为基表直连外键列。"""
    on = target_join.args.get("on")
    if not isinstance(on, exp.Expr):
        return False
    mapping = dict(
        zip(
            candidate.direct_fk.target_columns,
            candidate.direct_fk.source_columns,
            strict=False,
        )
    )
    replaced = 0
    for eq in on.find_all(exp.EQ):
        for key, other in (("this", "expression"), ("expression", "this")):
            side = eq.args.get(key)
            peer = eq.args.get(other)
            if (
                isinstance(side, exp.Column)
                and facts.aliases.get(side.table) == candidate.bridge_key
                and isinstance(peer, exp.Column)
                and facts.aliases.get(peer.table) == candidate.target_key
                and side.name in mapping
            ):
                eq.set(key, exp.column(mapping[side.name], table=candidate.base_alias))
                replaced += 1
    return replaced > 0


def _residual_bridge_refs(facts: _QueryFacts, candidate: _BridgeJoin) -> bool:
    """桥列若在待删 JOIN 之外还有引用,删除桥 JOIN 会破坏 SQL。"""
    for column in facts.expression.find_all(exp.Column):
        if facts.aliases.get(column.table) != candidate.bridge_key:
            continue
        ancestor = column.find_ancestor(exp.Join)
        if ancestor is not candidate.bridge_join:
            return True
    return False


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


def _basis_labels(graph: SchemaGraph) -> tuple[set[str], set[str]]:
    """事件表/金额表的时间口径标签,全部取自表名、注释与时间列元数据。

    取注释主段的前两个汉字作为口语标签,英文取表/列名中的词;两侧共有的
    标签(如 date)不参与判定,避免把通用词当口径。
    """
    event: set[str] = set()
    amount: set[str] = set()
    for table in graph.tables:
        if table.role not in (ROLE_EVENT, ROLE_AMOUNT):
            continue
        temporal = [c for c in table.columns if _temporal_column(table, c.name)]
        if not temporal:
            continue
        labels: set[str] = set()
        labels.update(_text_labels(table.table_name))
        labels.update(_text_labels(table.comment))
        for column in temporal:
            labels.update(_text_labels(column.name))
            labels.update(_text_labels(column.comment))
        if table.role == ROLE_EVENT:
            event.update(labels)
        else:
            amount.update(labels)
    shared = event & amount
    return event - shared, amount - shared


def _text_labels(text: str) -> set[str]:
    """从一段名称/注释提取可用于问句匹配的标签。"""
    raw = text.strip()
    if not raw:
        return set()
    head = re.split(r"[（(\-—:：,，;；/、\s]", raw, maxsplit=1)[0].strip()
    if not head:
        return set()
    labels = {head.lower()} if len(head) >= 2 else set[str]()
    labels.update(cast("list[str]", re.findall(r"[a-z_]{3,}", head.lower())))
    if len(head) >= 2 and _CJK.match(head[:2]):
        labels.add(head[:2])
    return {label for label in labels if len(label) >= 2}


def _time_grouping_index(question: str) -> int | None:
    """问句中最早出现的「按/每 + 时间单位」分组词位置;过滤型「上个月」不算。"""
    lower = question.lower()
    hits: list[int] = []
    for unit in _TIME_UNITS:
        start = 0
        while True:
            index = lower.find(unit, start)
            if index < 0:
                break
            before = lower[max(0, index - 4) : index]
            if unit in ("monthly", "quarterly", "weekly", "daily", "yearly") or any(
                mark in before for mark in _GROUPING_MARKS
            ):
                hits.append(index)
            start = index + len(unit)
    return min(hits) if hits else None


def _time_basis_hint(question: str, graph: SchemaGraph) -> str:
    """分组词前的时间口径线索:event / amount / 空(未明确或两可)。"""
    if not graph.complete:
        return ""
    event, amount = _basis_labels(graph)
    if not event or not amount:
        return ""
    index = _time_grouping_index(question)
    if index is None:
        return ""
    window = question.lower()[max(0, index - _TIME_WINDOW_CHARS) : index]
    event_hit = any(label in window for label in event)
    amount_hit = any(label in window for label in amount)
    if event_hit != amount_hit:
        return "event" if event_hit else "amount"
    return ""


def time_basis_clarification(question: str, graph: SchemaGraph) -> str | None:
    """事件与金额时间口径两可时生成一句澄清反问;可判定或单一指标返回 None。"""
    if not graph.complete:
        return None
    event_tables = [
        t
        for t in graph.tables
        if t.role == ROLE_EVENT and any(_temporal_column(t, c.name) for c in t.columns)
    ]
    amount_tables = [
        t
        for t in graph.tables
        if t.role == ROLE_AMOUNT and any(_temporal_column(t, c.name) for c in t.columns)
    ]
    if not event_tables or not amount_tables:
        return None
    event_labels, amount_labels = _basis_labels(graph)
    lower = question.lower()
    if not any(label in lower for label in event_labels):
        return None
    if not any(label in lower for label in amount_labels):
        return None
    if _time_grouping_index(question) is None or _time_basis_hint(question, graph):
        return None
    event_name = _table_label(event_tables[0])
    amount_name = _table_label(amount_tables[0])
    event_time = _temporal_labels(event_tables)
    amount_time = _temporal_labels(amount_tables)
    return (
        f"请确认时间口径：按{_option_label(event_name)}（{event_time}）统计，"
        f"还是按{_option_label(amount_name)}（{amount_time}）统计？"
    )


def _option_label(label: str) -> str:
    """英文表名与中文之间留空格,避免名称粘连。"""
    return f" {label}" if label.isascii() else label


def _table_label(table: GraphTable) -> str:
    head = re.split(r"[（(\-—:：,，;；/、\s]", table.comment.strip(), maxsplit=1)[0].strip()
    return head or table.table_name


def _temporal_labels(tables: Sequence[GraphTable]) -> str:
    labels: list[str] = []
    for table in tables:
        for column in table.columns:
            if not _temporal_column(table, column.name):
                continue
            head = re.split(r"[（(\-—:：,，;；/、\s]", column.comment.strip(), maxsplit=1)[
                0
            ].strip()
            label = head or column.name
            if label not in labels:
                labels.append(label)
            if len(labels) >= 3:
                return "或".join(labels)
    return "或".join(labels) or "时间"
