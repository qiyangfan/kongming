"""结构化 Schema 图:表/列/主键/外键/结构角色与桥接路径(零框架纯函数)。

角色只由结构与元数据注释推导,不在代码里写业务表名;证据不足一律 unknown,
调用方不得把 unknown 当成错误。图不含连接信息,只由控制面组装后下发。
"""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

ROLE_EVENT = "event"
ROLE_AMOUNT = "amount"
ROLE_DIMENSION = "dimension"
ROLE_UNKNOWN = "unknown"

Role = str
TableKey = tuple[str, str]

_AMOUNT_TYPES = frozenset({"numeric", "decimal", "real", "double precision", "float", "money"})
_TEMPORAL_TYPES = ("date", "time")
# last_update 一类审计时间不是业务事件时间;created_at 仍可能是业务发生时刻
_AUDIT_TIME_NAMES = ("update", "modified")


@dataclass(frozen=True)
class GraphColumn:
    name: str
    data_type: str
    comment: str = ""


@dataclass(frozen=True)
class GraphTable:
    schema_name: str
    table_name: str
    comment: str = ""
    columns: tuple[GraphColumn, ...] = ()
    primary_key: tuple[str, ...] = ()
    role: Role = ROLE_UNKNOWN
    source: str = "introspection"
    note: str = ""  # 知识条目别名/说明,仅用于问句匹配,不改变结构事实

    @property
    def key(self) -> TableKey:
        return self.schema_name, self.table_name

    def column_names(self) -> set[str]:
        return {c.name for c in self.columns}


@dataclass(frozen=True)
class GraphForeignKey:
    source_schema: str
    source_table: str
    source_columns: tuple[str, ...]
    target_schema: str
    target_table: str
    target_columns: tuple[str, ...]

    @property
    def source_key(self) -> TableKey:
        return self.source_schema, self.source_table

    @property
    def target_key(self) -> TableKey:
        return self.target_schema, self.target_table


@dataclass(frozen=True)
class BridgePath:
    key: TableKey
    edges: tuple[GraphForeignKey, ...]


@dataclass(frozen=True)
class SchemaGraph:
    tables: tuple[GraphTable, ...]
    foreign_keys: tuple[GraphForeignKey, ...]
    complete: bool = True
    # 全图已知表键(子图下发时携带):用于区分「未召回的已知表」与「库中不存在」
    known_tables: tuple[TableKey, ...] = ()

    def table(self, key: TableKey) -> GraphTable | None:
        for t in self.tables:
            if t.key == key:
                return t
        return None

    def outgoing(self, key: TableKey) -> list[GraphForeignKey]:
        return [fk for fk in self.foreign_keys if fk.source_key == key]

    def project(self, keys: Sequence[TableKey], notes: Mapping[TableKey, str]) -> SchemaGraph:
        """按需下发子图;缺失任一键即 complete=False(调用方跳过硬拒绝)。"""
        wanted = set(keys)
        tables: list[GraphTable] = []
        present: set[TableKey] = set()
        for t in self.tables:
            if t.key in wanted:
                tables.append(
                    GraphTable(
                        schema_name=t.schema_name,
                        table_name=t.table_name,
                        comment=t.comment,
                        columns=t.columns,
                        primary_key=t.primary_key,
                        role=t.role,
                        source=t.source,
                        note=notes.get(t.key, ""),
                    )
                )
                present.add(t.key)
        fks = [
            fk for fk in self.foreign_keys if fk.source_key in present and fk.target_key in present
        ]
        return SchemaGraph(
            tables=tuple(sorted(tables, key=lambda x: x.key)),
            foreign_keys=tuple(fks),
            complete=self.complete and present == wanted,
            known_tables=tuple(sorted(set(self.known_tables) | {t.key for t in self.tables})),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "tables": [
                {
                    "schema_name": t.schema_name,
                    "table_name": t.table_name,
                    "comment": t.comment,
                    "columns": [
                        {"name": c.name, "data_type": c.data_type, "comment": c.comment}
                        for c in t.columns
                    ],
                    "primary_key": list(t.primary_key),
                    "role": t.role,
                    "source": t.source,
                    "note": t.note,
                }
                for t in self.tables
            ],
            "foreign_keys": [
                {
                    "source_schema": fk.source_schema,
                    "source_table": fk.source_table,
                    "source_columns": list(fk.source_columns),
                    "target_schema": fk.target_schema,
                    "target_table": fk.target_table,
                    "target_columns": list(fk.target_columns),
                }
                for fk in self.foreign_keys
            ],
            "complete": self.complete,
            "known_tables": [f"{s}.{t}" for s, t in self.known_tables],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> SchemaGraph:
        tables_raw = _list_of_maps(data.get("tables"))
        fks_raw = _list_of_maps(data.get("foreign_keys"))
        tables = tuple(
            GraphTable(
                schema_name=str(t.get("schema_name") or ""),
                table_name=str(t.get("table_name") or ""),
                comment=str(t.get("comment") or ""),
                columns=tuple(
                    GraphColumn(
                        name=str(c.get("name") or ""),
                        data_type=str(c.get("data_type") or ""),
                        comment=str(c.get("comment") or ""),
                    )
                    for c in _list_of_maps(t.get("columns"))
                ),
                primary_key=tuple(_str_list(t.get("primary_key"))),
                role=_role_of(str(t.get("role") or "")),
                source=str(t.get("source") or "introspection"),
                note=str(t.get("note") or ""),
            )
            for t in tables_raw
        )
        fks = tuple(
            GraphForeignKey(
                source_schema=str(f.get("source_schema") or ""),
                source_table=str(f.get("source_table") or ""),
                source_columns=tuple(_str_list(f.get("source_columns"))),
                target_schema=str(f.get("target_schema") or ""),
                target_table=str(f.get("target_table") or ""),
                target_columns=tuple(_str_list(f.get("target_columns"))),
            )
            for f in fks_raw
        )
        return cls(
            tables=tables,
            foreign_keys=fks,
            complete=bool(data.get("complete", True)),
            known_tables=_table_keys(_str_list(data.get("known_tables"))),
        )


def _table_keys(values: Sequence[str]) -> tuple[TableKey, ...]:
    """解析「schema.table」形态的表键;畸形项丢弃。"""
    out: list[TableKey] = []
    for value in values:
        schema, sep, table = value.partition(".")
        if sep and schema and table:
            out.append((schema, table))
    return tuple(out)


def _list_of_maps(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [
        cast("dict[str, object]", x) for x in cast("list[object]", value) if isinstance(x, dict)
    ]


def _str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(x) for x in cast("list[object]", value) if str(x)]


def _role_of(value: str) -> Role:
    return value if value in (ROLE_EVENT, ROLE_AMOUNT, ROLE_DIMENSION) else ROLE_UNKNOWN


def _is_temporal(column: GraphColumn) -> bool:
    name = column.name.lower()
    if any(x in name for x in _AUDIT_TIME_NAMES):
        return False
    dtype = column.data_type.lower()
    return any(x in dtype for x in _TEMPORAL_TYPES) or name.endswith("_at")


def _is_amount_column(column: GraphColumn, *, key_columns: set[str]) -> bool:
    dtype = column.data_type.lower().split("(")[0].strip()
    return dtype in _AMOUNT_TYPES and column.name not in key_columns


def _event_basis(
    table: GraphTable, outgoing: Sequence[GraphForeignKey], keys: set[TableKey]
) -> bool:
    """事件的结构底像:主键 + 业务时间 + 至少一条去往实体表的外键。"""
    if not table.primary_key or not outgoing:
        return False
    if not any(_is_temporal(c) for c in table.columns):
        return False
    return any(fk.target_key in keys for fk in outgoing)


def _edge_indexes(
    tables: Sequence[GraphTable], fks: Sequence[GraphForeignKey]
) -> tuple[
    set[TableKey],
    dict[TableKey, GraphTable],
    dict[TableKey, list[GraphForeignKey]],
    dict[TableKey, list[GraphForeignKey]],
]:
    keys = {t.key for t in tables if t.table_name}
    by_key = {t.key: t for t in tables}
    outgoing: dict[TableKey, list[GraphForeignKey]] = {k: [] for k in keys}
    incoming: dict[TableKey, list[GraphForeignKey]] = {k: [] for k in keys}
    for fk in fks:
        if fk.source_key in keys and fk.target_key in keys:
            outgoing[fk.source_key].append(fk)
            incoming[fk.target_key].append(fk)
    return keys, by_key, outgoing, incoming


def _amount_keys(
    by_key: dict[TableKey, GraphTable],
    outgoing: dict[TableKey, list[GraphForeignKey]],
    event_basis: set[TableKey],
) -> set[TableKey]:
    out: set[TableKey] = set()
    for key, table in by_key.items():
        fk_cols = {c for fk in outgoing[key] for c in fk.source_columns}
        key_cols = set(table.primary_key) | fk_cols
        has_amount = any(_is_amount_column(c, key_columns=key_cols) for c in table.columns)
        targets_event = any(fk.target_key in event_basis for fk in outgoing[key])
        if table.primary_key and has_amount and targets_event:
            out.add(key)
    return out


def infer_roles(tables: Sequence[GraphTable], fks: Sequence[GraphForeignKey]) -> SchemaGraph:
    """按结构证据一次性标注角色;证据冲突/不足保留 unknown。"""
    keys, by_key, outgoing, incoming = _edge_indexes(tables, fks)
    event_basis = {k for k, t in by_key.items() if _event_basis(t, outgoing[k], keys)}
    amount_keys = _amount_keys(by_key, outgoing, event_basis)

    roles: dict[TableKey, Role] = {}
    for key, table in by_key.items():
        if key in amount_keys:
            roles[key] = ROLE_AMOUNT
        elif key in event_basis:
            roles[key] = ROLE_EVENT
        elif table.primary_key and incoming[key]:
            roles[key] = ROLE_DIMENSION
        else:
            roles[key] = ROLE_UNKNOWN

    return SchemaGraph(
        tables=tuple(
            GraphTable(
                schema_name=t.schema_name,
                table_name=t.table_name,
                comment=t.comment,
                columns=t.columns,
                primary_key=t.primary_key,
                role=roles[t.key],
                source=t.source,
                note=t.note,
            )
            for t in sorted(tables, key=lambda x: x.key)
        ),
        foreign_keys=tuple(sorted(fks, key=lambda f: (f.source_key, f.target_key))),
        complete=True,
    )


def has_reciprocal_edge(graph: SchemaGraph, path: Sequence[GraphForeignKey]) -> bool:
    """路径中任一边存在反向外键(组织/管理环)即视为更强的归属证据。"""
    edges = {(fk.source_key, fk.target_key) for fk in graph.foreign_keys}
    return any((fk.target_key, fk.source_key) in edges for fk in path)


def select_bridge_tables(
    graph: SchemaGraph,
    recalled: Sequence[TableKey],
    question: str,
    *,
    max_bridges: int,
) -> list[BridgePath]:
    """从已召回事件/金额表沿外键补维表;尾部追加、独立预算、无路径零扩展。

    金额表也常作为度量基表(如支付按门店/月份聚合);只补维度/未知角色,
    事件与金额角色仍不作为桥接目标。直连外键优先,其次按路径长度与问句命中排序;
    不按表名猜关系。
    """
    if max_bridges <= 0:
        return []
    recalled_set = set(recalled)
    candidates: dict[TableKey, tuple[int, int, int, tuple[GraphForeignKey, ...]]] = {}
    for key in recalled:
        table = graph.table(key)
        if table is not None and table.role in (ROLE_EVENT, ROLE_AMOUNT):
            _collect_bridge_candidates(graph, key, recalled_set, question, candidates)
    ordered = sorted(candidates.items(), key=lambda kv: (*kv[1][:3], kv[0]))
    return [BridgePath(key=key, edges=item[3]) for key, item in ordered[:max_bridges]]


def _collect_bridge_candidates(
    graph: SchemaGraph,
    start: TableKey,
    recalled: set[TableKey],
    question: str,
    candidates: dict[TableKey, tuple[int, int, int, tuple[GraphForeignKey, ...]]],
) -> None:
    """两跳内 BFS,按 (距离, 问句命中) 保留每个目标的最优路径。"""
    queue: deque[tuple[TableKey, tuple[GraphForeignKey, ...]]] = deque([(start, ())])
    while queue:
        node, path = queue.popleft()
        if len(path) >= 2:
            continue
        visited = {f.source_key for f in path} | {f.target_key for f in path}
        for fk in graph.outgoing(node):
            nxt = fk.target_key
            if nxt in visited:
                continue
            next_path = (*path, fk)
            target = graph.table(nxt)
            # 只补维度/未知角色;金额与事件角色不是维表。
            if target is None or target.role in (ROLE_AMOUNT, ROLE_EVENT) or nxt in recalled:
                continue
            _remember_bridge(graph, candidates, nxt, next_path, question, target)
            queue.append((nxt, next_path))


def _remember_bridge(
    graph: SchemaGraph,
    candidates: dict[TableKey, tuple[int, int, int, tuple[GraphForeignKey, ...]]],
    key: TableKey,
    path: tuple[GraphForeignKey, ...],
    question: str,
    target: GraphTable,
) -> None:
    rank = (
        len(path),
        0 if _mentioned(question, target) else 1,
        0 if has_reciprocal_edge(graph, path) else 1,
    )
    previous = candidates.get(key)
    if previous is None or rank < previous[:3]:
        candidates[key] = (*rank, path)


def _mentioned(question: str, table: GraphTable) -> bool:
    text = question.lower()
    for raw in (table.table_name, table.comment, table.note):
        token = raw.strip().lower()
        if len(token) >= 2 and token in text:
            return True
    return False


def format_table_hint(table: GraphTable) -> str:
    """桥接表补入 schema_hint 的行形态(与知识条目提示同构)。"""
    cols = ", ".join(f"{c.name}({c.data_type})" if c.data_type else c.name for c in table.columns)
    line = f"{table.schema_name}.{table.table_name}({cols})"
    if table.comment:
        line += f" -- {table.comment}"
    return line


def format_join(fk: GraphForeignKey) -> str:
    conds = " AND ".join(
        f"{fk.source_schema}.{fk.source_table}.{left} = "
        f"{fk.target_schema}.{fk.target_table}.{right}"
        for left, right in zip(fk.source_columns, fk.target_columns, strict=False)
    )
    return (
        f"{fk.source_schema}.{fk.source_table} JOIN {fk.target_schema}.{fk.target_table} ON {conds}"
    )


def schema_hint_tables(schema_hint: str) -> list[tuple[TableKey, str]]:
    """从 schema_hint 表头行取 (schema.table, 说明) 序;说明含别名与表注释。"""
    out: list[tuple[TableKey, str]] = []
    seen: set[TableKey] = set()
    for line in schema_hint.splitlines():
        m = re.match(r"^([A-Za-z_][\w]*)\.([A-Za-z_][\w]*)\(", line)
        if m is None:
            continue
        key = (m.group(1), m.group(2))
        if key in seen:
            continue
        seen.add(key)
        note = line.split("--", 1)[1].strip() if "--" in line else ""
        out.append((key, note))
    return out


def drop_schema_hint_tables(schema_hint: str, drop: set[TableKey]) -> str:
    """剔除 hint 中指定表的块(表头行及其后的示例/续行),其余块原样保留。

    用于让提示词与结构化图对齐:知识条目里残留的已删除表/继承子表不在图内,
    留在提示词里会诱导生成,并把整张图误标为不完整(语义硬规则整体跳过)。
    """
    kept: list[str] = []
    dropping = False
    for line in schema_hint.splitlines():
        m = re.match(r"^([A-Za-z_][\w]*)\.([A-Za-z_][\w]*)\(", line)
        if m is not None:
            dropping = (m.group(1), m.group(2)) in drop
        if not dropping:
            kept.append(line)
    return "\n".join(kept)


# 仅用于测试/诊断的公开别名
__all__ = [
    "ROLE_AMOUNT",
    "ROLE_DIMENSION",
    "ROLE_EVENT",
    "ROLE_UNKNOWN",
    "BridgePath",
    "GraphColumn",
    "GraphForeignKey",
    "GraphTable",
    "SchemaGraph",
    "format_join",
    "format_table_hint",
    "has_reciprocal_edge",
    "infer_roles",
    "schema_hint_tables",
    "select_bridge_tables",
]
