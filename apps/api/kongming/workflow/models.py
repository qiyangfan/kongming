"""workflow 领域模型(029,零框架):DAG 结构校验/引用解析/模板替换/
条件比较——全纯函数,引擎与路由共用唯一实现。

定义形态:{nodes: [{id, type, config, depends_on}]};依赖即边
(edges 隐含于 depends_on,单向无冗余)。
"""

import copy
import re
from dataclasses import dataclass, field
from typing import Any

NODE_TYPES = ("sql", "http", "condition", "loop", "transform", "agent")

# 类型必填配置键(design D2)
_REQUIRED_CONFIG: dict[str, tuple[str, ...]] = {
    "sql": ("sql",),
    "http": ("url", "method"),
    "condition": ("left", "op", "right"),
    "loop": ("items_ref", "body", "max_iterations"),
    "transform": ("template",),
    "agent": ("instruction",),
}

_CONDITION_OPS = ("==", "!=", ">=", "<=", ">", "<", "contains")

_REF_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+?)\s*\}\}")

# R1-J5:节点 id 形态与引用语法字符集对齐(连字符/空格 id 会让引用
# 静默解析为空串);保留名(loop 内建上下文)禁用作节点 id(R1-J1)
_NODE_ID_RE = re.compile(r"^[A-Za-z0-9_]+$")
_RESERVED_IDS = frozenset({"inputs", "_loop"})


class DagValidationError(Exception):
    """结构校验失败(文案面向管理面,指出问题节点)。"""


class DefinitionConflictError(Exception):
    """并发保存同 key 版本冲突(唯一索引兜底;领域层 409 语义)。"""


@dataclass(frozen=True)
class NodeDef:
    id: str
    type: str
    config: dict[str, Any] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)


def _parse_nodes(definition: dict[str, Any]) -> list[NodeDef]:
    raw = definition.get("nodes")
    if not isinstance(raw, list) or not raw:
        raise DagValidationError("definition.nodes 必须是非空数组")
    nodes: list[NodeDef] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise DagValidationError(f"nodes[{i}] 非对象")
        nid = str(item.get("id") or "").strip()
        if not nid:
            raise DagValidationError(f"nodes[{i}] 缺 id")
        ntype = str(item.get("type") or "")
        config = item.get("config")
        deps = item.get("depends_on")
        nodes.append(
            NodeDef(
                id=nid,
                type=ntype,
                config=config if isinstance(config, dict) else {},
                depends_on=[str(d) for d in deps] if isinstance(deps, list) else [],
            )
        )
    return nodes


def validate_dag(
    definition: dict[str, Any], *, _reserved: frozenset[str] = _RESERVED_IDS
) -> list[NodeDef]:
    """DAG 结构校验(纯函数):id 唯一/形态合法/类型合法/必填配置/引用
    存在/无环(Kahn,报环节点)/无孤立(多节点时)/loop 子段递归校验
    (外层 id 与保留名碰撞拒绝)。通过则返回节点表。"""
    nodes = _parse_nodes(definition)
    if len(nodes) > 100:  # R1-提示3:体量上限(管理面资产防爆)
        raise DagValidationError(f"节点数超上限 100(当前 {len(nodes)})")
    seen: set[str] = set()
    for n in nodes:
        if n.id in seen:
            raise DagValidationError(f"节点 id 重复: {n.id}")
        if not _NODE_ID_RE.fullmatch(n.id):
            raise DagValidationError(
                f"节点 id 形态非法(仅字母/数字/下划线): {n.id}"
            )
        if n.id in _reserved:
            raise DagValidationError(f"节点 id 撞保留名/外层节点: {n.id}")
        seen.add(n.id)
        if n.type not in NODE_TYPES:
            raise DagValidationError(
                f"节点 {n.id} 类型非法: {n.type}(合法: {', '.join(NODE_TYPES)})"
            )
        for key in _REQUIRED_CONFIG[n.type]:
            if key not in n.config:
                raise DagValidationError(f"节点 {n.id}({n.type})缺必填配置 {key}")
        # R1-J4:config.on 值校验(typo 静默反转分支语义)
        on = n.config.get("on")
        if on is not None and on not in ("true", "false"):
            raise DagValidationError(f"节点 {n.id} 的 on 仅可为 true/false")
        if len(set(n.depends_on)) != len(n.depends_on):
            raise DagValidationError(f"节点 {n.id} 存在重复依赖项")
        if n.id in n.depends_on:
            raise DagValidationError(f"节点 {n.id} 自依赖")
        # R1-J1:loop 子段递归校验(保留名+外层全量 id 集,两遍制:
        # 先收集再校验——body 撞后声明的外层节点也拒)
        if n.type == "loop":
            body = n.config.get("body")
            if isinstance(body, list) and body:
                validate_dag(
                    {"nodes": body},
                    _reserved=frozenset(
                        set(_reserved) | {x.id for x in nodes}
                    ),
                )
    ids = {n.id for n in nodes}
    for n in nodes:
        for dep in n.depends_on:
            if dep not in ids:
                raise DagValidationError(f"节点 {n.id} 依赖不存在的节点: {dep}")
    # 无环:Kahn 拓扑(残留即环)
    indeg: dict[str, int] = {n.id: len(n.depends_on) for n in nodes}
    queue = [i for i, d in indeg.items() if d == 0]
    done = 0
    while queue:
        cur = queue.pop()
        done += 1
        for n in nodes:
            if cur in n.depends_on:
                indeg[n.id] -= 1
                if indeg[n.id] == 0:
                    queue.append(n.id)
    if done < len(nodes):
        cyclic = sorted(i for i, d in indeg.items() if d > 0)
        raise DagValidationError(f"定义存在环,参与节点: {', '.join(cyclic)}")
    # 孤立节点(多节点时):无依赖且无后继
    if len(nodes) > 1:
        has_successor = {d for n in nodes for d in n.depends_on}
        for n in nodes:
            if not n.depends_on and n.id not in has_successor:
                raise DagValidationError(f"孤立节点(无依赖且无后继): {n.id}")
    return nodes


def resolve_ref(ref: str, context: dict[str, Any]) -> Any:
    """引用解析:点路径逐层走字典(node_id.outputs.a.b / _loop.item);
    不存在 → None(渲染层转空串,不炸)。"""
    cur: Any = context
    for part in ref.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def render_template(template: Any, context: dict[str, Any]) -> Any:
    """模板递归替换:字符串内 {{ref}} 替换(整串恰为单引用时保留原值类型,
    否则字符串化);dict/list 递归;其余原样。"""
    if isinstance(template, str):
        full = _REF_RE.fullmatch(template)
        if full:
            value = resolve_ref(full.group(1), context)
            # 深拷贝:全引用直通不共享可变引用(上下文不可变语义严格化)
            return copy.deepcopy(value)
        return _REF_RE.sub(
            lambda m: "" if resolve_ref(m.group(1), context) is None
            else str(resolve_ref(m.group(1), context)),
            template,
        )
    if isinstance(template, dict):
        return {k: render_template(v, context) for k, v in template.items()}
    if isinstance(template, list):
        return [render_template(v, context) for v in template]
    return template


def compare(op: str, left: Any, right: Any) -> bool:
    """条件比较(无 eval):数值优先(双方可转数值),否则字符串兜底;
    contains=子串(字符串化后)。"""
    if op not in _CONDITION_OPS:
        raise DagValidationError(f"非法比较符: {op}")
    if op == "contains":
        return str(right) in str(left)

    def _num(v: Any) -> float | None:
        if isinstance(v, bool):
            return None
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            try:
                return float(v)
            except ValueError:
                return None
        return None

    ln, rn = _num(left), _num(right)
    a: Any
    b: Any
    if ln is not None and rn is not None:
        a, b = ln, rn
    else:
        a, b = str(left), str(right)
    if op == "==":
        return bool(a == b)
    if op == "!=":
        return bool(a != b)
    if op == ">":
        return bool(a > b)
    if op == ">=":
        return bool(a >= b)
    if op == "<":
        return bool(a < b)
    return bool(a <= b)
