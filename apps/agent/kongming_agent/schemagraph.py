"""Agent 侧结构化 Schema 图:只消费控制面下发的结构与角色,不持有任何凭证。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

ROLE_EVENT = "event"
ROLE_AMOUNT = "amount"
ROLE_DIMENSION = "dimension"
ROLE_UNKNOWN = "unknown"
Role = str
TableKey = tuple[str, str]


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
class SchemaGraph:
    tables: tuple[GraphTable, ...]
    foreign_keys: tuple[GraphForeignKey, ...]
    complete: bool = True

    def table(self, key: TableKey) -> GraphTable | None:
        for table in self.tables:
            if table.key == key:
                return table
        return None

    def outgoing(self, key: TableKey) -> list[GraphForeignKey]:
        return [fk for fk in self.foreign_keys if fk.source_key == key]

    def to_dict(self) -> dict[str, object]:
        """回写 LangGraph state 的可序列化形态(检查点/事件边界不传自定义对象)。"""
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
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> SchemaGraph:
        tables_raw = _list_of_maps(data.get("tables"))
        fks_raw = _list_of_maps(data.get("foreign_keys"))
        return cls(
            tables=tuple(
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
                )
                for t in tables_raw
            ),
            foreign_keys=tuple(
                GraphForeignKey(
                    source_schema=str(f.get("source_schema") or ""),
                    source_table=str(f.get("source_table") or ""),
                    source_columns=tuple(_str_list(f.get("source_columns"))),
                    target_schema=str(f.get("target_schema") or ""),
                    target_table=str(f.get("target_table") or ""),
                    target_columns=tuple(_str_list(f.get("target_columns"))),
                )
                for f in fks_raw
            ),
            complete=bool(data.get("complete", True)),
        )


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


def state_graph(raw: object) -> SchemaGraph | None:
    """从 state 的原始字典还原图;畸形按缺失处理。"""
    if not isinstance(raw, dict):
        return None
    try:
        return SchemaGraph.from_dict(cast("dict[str, object]", raw))
    except (TypeError, ValueError):
        return None


def format_prompt_hint(graph: SchemaGraph) -> str:
    """生成提示的结构事实段;图空返回空串(旧路径零变化)。"""
    lines: list[str] = []
    for table in graph.tables:
        facts = [f"{table.schema_name}.{table.table_name}"]
        if table.role != ROLE_UNKNOWN:
            facts.append(f"角色:{table.role}")
        if table.primary_key:
            facts.append("主键:" + ",".join(table.primary_key))
        lines.append("；".join(facts))
        lines.extend(
            f"  外键 {','.join(fk.source_columns)} -> "
            f"{fk.target_schema}.{fk.target_table}({','.join(fk.target_columns)})"
            for fk in graph.outgoing(table.key)
        )
    return "\n".join(lines)


def path_tables(path: Sequence[GraphForeignKey]) -> set[TableKey]:
    out: set[TableKey] = set()
    for fk in path:
        out.add(fk.source_key)
        out.add(fk.target_key)
    return out


def paths_between(
    graph: SchemaGraph, start: TableKey, target: TableKey, *, max_depth: int = 3
) -> list[tuple[GraphForeignKey, ...]]:
    """枚举有界简单外键路径(最多 3 跳);用于归属路径的唯一性判定。"""
    out: list[tuple[GraphForeignKey, ...]] = []
    stack: list[tuple[TableKey, tuple[GraphForeignKey, ...]]] = [(start, ())]
    while stack:
        node, path = stack.pop()
        if len(path) >= max_depth:
            continue
        for fk in sorted(
            graph.outgoing(node),
            key=lambda x: (x.target_key, x.source_columns, x.target_columns),
        ):
            nxt = fk.target_key
            if nxt in {start, *(f.source_key for f in path), *(f.target_key for f in path)}:
                continue
            next_path = (*path, fk)
            if nxt == target:
                out.append(next_path)
            else:
                stack.append((nxt, next_path))
    return out


def has_reciprocal_edge(graph: SchemaGraph, path: Sequence[GraphForeignKey]) -> bool:
    """路径中任一边存在反向外键(组织/管理环)即视为更强的归属证据。"""
    edges = {(fk.source_key, fk.target_key) for fk in graph.foreign_keys}
    return any((fk.target_key, fk.source_key) in edges for fk in path)


def canonical_path(
    graph: SchemaGraph, start: TableKey, target: TableKey, *, max_depth: int = 3
) -> tuple[GraphForeignKey, ...] | None:
    """唯一可判定的归属路径:单一路径,或唯一带反向外键的路径;否则 None。"""
    paths = paths_between(graph, start, target, max_depth=max_depth)
    if not paths:
        return None
    reciprocal = [p for p in paths if has_reciprocal_edge(graph, p)]
    if len(reciprocal) == 1:
        return reciprocal[0]
    if len(paths) == 1:
        return paths[0]
    return None


__all__ = [
    "ROLE_AMOUNT",
    "ROLE_DIMENSION",
    "ROLE_EVENT",
    "ROLE_UNKNOWN",
    "GraphColumn",
    "GraphForeignKey",
    "GraphTable",
    "SchemaGraph",
    "canonical_path",
    "format_prompt_hint",
    "has_reciprocal_edge",
    "path_tables",
    "paths_between",
    "state_graph",
]
