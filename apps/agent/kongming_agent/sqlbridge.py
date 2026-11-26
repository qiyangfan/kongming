"""桥接 JOIN 自动改写的结构证据:只允许基表与桥表之间的外键等值连接。"""

from __future__ import annotations

from typing import Any

from sqlglot import exp

from kongming_agent.schemagraph import SchemaGraph, TableKey


def bridge_join_only_links_base(
    *,
    graph: SchemaGraph,
    aliases: dict[str, TableKey | None],
    base_key: TableKey,
    bridge_key: TableKey,
    bridge_join: exp.Join,
) -> bool:
    """桥 JOIN 的 ON 只能有基表与桥表的外键配对;附加谓词删除会改变语义。"""
    on = bridge_join.args.get("on")
    if not isinstance(on, exp.Expr):
        return False
    conditions = _conjuncts(on)
    return bool(conditions) and all(
        _condition_links_base(
            graph=graph,
            aliases=aliases,
            base_key=base_key,
            bridge_key=bridge_key,
            condition=condition,
        )
        for condition in conditions
    )


def _condition_links_base(
    *,
    graph: SchemaGraph,
    aliases: dict[str, TableKey | None],
    base_key: TableKey,
    bridge_key: TableKey,
    condition: exp.Expr,
) -> bool:
    """单个 ON 条件是否为基表-桥表外键两侧的等值配对。"""
    if not isinstance(condition, exp.EQ):
        return False
    left = condition.args.get("this")
    right = condition.args.get("expression")
    if not isinstance(left, exp.Column) or not isinstance(right, exp.Column):
        return False
    left_key = aliases.get(left.table) if left.table else None
    right_key = aliases.get(right.table) if right.table else None
    if {left_key, right_key} != {base_key, bridge_key}:
        return False
    base_column, bridge_column = (left, right) if left_key == base_key else (right, left)
    return _fk_matches_pair(
        graph,
        base_key=base_key,
        bridge_key=bridge_key,
        base_column=base_column.name,
        bridge_column=bridge_column.name,
    )


def _fk_matches_pair(
    graph: SchemaGraph,
    *,
    base_key: TableKey,
    bridge_key: TableKey,
    base_column: str,
    bridge_column: str,
) -> bool:
    """两列属于同一条基表-桥表外键的配对位置。"""
    for fk in graph.foreign_keys:
        if fk.source_key == base_key and fk.target_key == bridge_key:
            base_columns, bridge_columns = fk.source_columns, fk.target_columns
        elif fk.source_key == bridge_key and fk.target_key == base_key:
            base_columns, bridge_columns = fk.target_columns, fk.source_columns
        else:
            continue
        if base_column not in base_columns or bridge_column not in bridge_columns:
            continue
        if base_columns.index(base_column) == bridge_columns.index(bridge_column):
            return True
    return False


def _conjuncts(node: Any) -> list[exp.Expr]:
    """拆分 AND 条件;OR/比较等非合取节点整体返回,由调用方拒绝。"""
    if isinstance(node, exp.And):
        return [*_conjuncts(node.this), *_conjuncts(node.expression)]
    return [node] if isinstance(node, exp.Expr) else []
