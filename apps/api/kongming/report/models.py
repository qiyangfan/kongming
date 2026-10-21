"""report 领域模型:状态机/章节树/绑定快照/导出装配(零框架)。

spec: report-generation——数据真实原则(数值断言只来自快照)、版本化
快照语义(同版本恒同一份快照)、状态迁移 fail-closed(终态不复活)。
本模块不依赖 fastapi 或 sqlalchemy;import-linter 会校验这条边界。
"""

import json
from dataclasses import dataclass, field
from typing import Any, cast

# ── 状态机 ──

STATUS_GENERATING = "GENERATING"
STATUS_READY = "READY"
STATUS_PARTIAL = "PARTIAL"
STATUS_FAILED = "FAILED"
STATUS_DELETED = "DELETED"

# 迁移白名单(缺席即非法):GENERATING 为唯一活动态;终态不复活;
# DELETED 任何状态可达但自身无出边(软删仅留痕)。
STATUS_TRANSITIONS: dict[str, frozenset[str]] = {
    STATUS_GENERATING: frozenset({STATUS_READY, STATUS_PARTIAL, STATUS_FAILED, STATUS_DELETED}),
    STATUS_READY: frozenset({STATUS_DELETED}),
    STATUS_PARTIAL: frozenset({STATUS_DELETED}),
    STATUS_FAILED: frozenset({STATUS_DELETED}),
    STATUS_DELETED: frozenset(),
}

# 生成进度(写回用;终态时置 DONE)
PHASE_PLANNING = "PLANNING"
PHASE_FETCHING = "FETCHING"
PHASE_WRITING = "WRITING"
PHASE_ASSEMBLING = "ASSEMBLING"
PHASE_DONE = "DONE"
PHASES = frozenset({PHASE_PLANNING, PHASE_FETCHING, PHASE_WRITING, PHASE_ASSEMBLING, PHASE_DONE})

# 章节数据状态
DATA_OK = "OK"
DATA_MISSING = "MISSING"
DATA_PENDING = "PENDING"

# 编辑只允许作用于这些状态(FAILED 保留章节仅供诊断,不可编辑成品化)
EDITABLE_STATUSES = frozenset({STATUS_READY, STATUS_PARTIAL})


class InvalidTransition(Exception):
    """状态迁移不在白名单(fail-closed,调用方映射 409)。"""


def transition(current: str, target: str) -> str:
    """状态迁移纯函数:白名单外一律拒绝,不静默放行。"""
    if target not in STATUS_TRANSITIONS.get(current, frozenset()):
        raise InvalidTransition(f"{current} -> {target}")
    return target


# ── 章节树与绑定快照 ──


@dataclass(frozen=True)
class Binding:
    """数据绑定=可再执行的引用:SQL 文本 + 执行时结果快照(截断标注)。

    spec「报告结构契约」:行数有上限,超出截断并标注;失败章节 status=
    MISSING 且不携带编造数据(与「有证据才完成」同源)。"""

    sql: str
    status: str = DATA_OK  # OK / MISSING
    columns: list[str] = field(default_factory=list[str])
    rows: list[list[object]] = field(default_factory=list[list[object]])
    row_count: int = 0  # 截断前的总行数(执行端已知则填,未知=已返回行数)
    truncated: bool = False
    fail_reason: str | None = None


@dataclass(frozen=True)
class SectionNode:
    """章节树节点:叶子持绑定与图表;树结构约束见 validate_tree。"""

    title: str
    narrative_md: str = ""
    bindings: list[Binding] = field(default_factory=list[Binding])
    chart: dict[str, object] | None = None
    section_id: str | None = None  # 落库后的行 id(内存树为 None)
    parent_id: str | None = None
    ord: int = 0
    data_status: str = DATA_PENDING
    fail_reason: str | None = None


def validate_tree(nodes: list[SectionNode], *, max_depth: int = 2) -> None:
    """章节树结构校验(纯函数,违规 ValueError):

    - parent_id 必须指向树内存在的节点(悬空引用拒绝);
    - 嵌套深度 ≤ max_depth(章/节两层,v1 不支持更深);
    - 有子节点的节点不得携带绑定/图表(绑定语义只在叶子);
    - 标题非空。"""
    by_id = {n.section_id: n for n in nodes if n.section_id is not None}
    parent_ids = {n.parent_id for n in nodes if n.parent_id is not None}
    depth_of: dict[str | None, int] = {None: 0}
    for n in nodes:
        if not n.title.strip():
            raise ValueError("章节标题不得为空")
        if n.parent_id is not None and n.parent_id not in by_id:
            raise ValueError(f"章节父节点不存在: {n.parent_id}")
        if n.parent_id not in depth_of:
            # 父节点必然先于子节点出现(生成/编辑两侧都按序构造)
            raise ValueError(f"章节父节点未先出现: {n.parent_id}")
        depth = depth_of[n.parent_id] + 1
        if depth > max_depth:
            raise ValueError(f"章节嵌套超过 {max_depth} 层: {n.title}")
        depth_of[n.section_id] = depth
        if n.section_id in parent_ids and (n.bindings or n.chart):
            raise ValueError(f"非叶子章节不得携带数据绑定/图表: {n.title}")


def all_sections_complete(nodes: list[SectionNode]) -> bool:
    """报告是否全部章节完成(READY/PARTIAL 终态判据):
    每个叶子 data_status=OK 且无失败标注;扁平树全部视为叶子。"""
    parent_ids = {n.parent_id for n in nodes if n.parent_id is not None}
    leaves = [n for n in nodes if n.section_id not in parent_ids] or nodes
    return all(n.data_status == DATA_OK and not n.fail_reason for n in leaves)


# ── 导出(纯函数装配;不调 LLM、不渲染图片,图表降级为数据表)──

_MD_TABLE_CAP = 10_000  # 防御上限:导出表格行数硬顶(快照本身已截断,这是二次护栏)


def _md_escape(cell: object) -> str:
    text = str(cell if cell is not None else "")
    return text.replace("|", "\\|").replace("\n", " ")


def _md_table(columns: list[str], rows: list[list[object]]) -> list[str]:
    lines = ["| " + " | ".join(_md_escape(c) for c in columns) + " |"]
    lines.append("| " + " | ".join("---" for _ in columns) + " |")
    for r in rows[:_MD_TABLE_CAP]:
        lines.append("| " + " | ".join(_md_escape(v) for v in r) + " |")
    return lines


def render_binding_markdown(binding: Binding) -> list[str]:
    """单个绑定的 markdown 形态:OK=数据表(+截断标注);MISSING=缺失说明。"""
    if binding.status != DATA_OK:
        reason = binding.fail_reason or "执行失败"
        return [f"> 数据缺失:{reason}", ""]
    lines = _md_table(binding.columns, binding.rows)
    if binding.truncated:
        lines.append("")
        lines.append(f"_(结果截断:共 {binding.row_count} 行,快照保留前 {len(binding.rows)} 行)_")
    lines.append("")
    return lines


def render_chart_markdown(chart: dict[str, object], bindings: list[Binding]) -> list[str]:
    """图表降级:取首个 OK 绑定的快照渲染数据表(SHALL NOT 输出图片/外链);
    无可用绑定时如实标注。"""
    ok = next((b for b in bindings if b.status == DATA_OK), None)
    if ok is None:
        return ["_(图表无可用数据)_", ""]
    lines = ["**图表数据表:**", ""]
    lines.extend(_md_table(ok.columns, ok.rows))
    lines.append("")
    return lines


def export_markdown(
    *,
    title: str,
    status: str,
    version: int,
    data_time_point: str,
    sections: list[SectionNode],
) -> str:
    """报告导出装配(纯函数):文档头 + 章节树(叙述 + 绑定快照表 + 图表数据表)。
    行数与快照截断口径一致(同份快照,不重执行)。"""
    lines = [f"# {title}", ""]
    lines.append(f"- 状态:{status}(版本 {version})")
    lines.append(f"- 数据时点:{data_time_point}")
    lines.append("")
    order = sorted(range(len(sections)), key=lambda i: _depth_key(sections[i], sections))
    for idx in order:
        n = sections[idx]
        depth = _depth(n, sections)
        lines.append(f"{'#' * (depth + 2)} {n.title}")
        lines.append("")
        if n.narrative_md:
            lines.append(n.narrative_md)
            lines.append("")
        if n.data_status == DATA_MISSING:
            lines.append(f"> 数据缺失:{n.fail_reason or '执行失败'}")
            lines.append("")
        for b in n.bindings:
            lines.extend(render_binding_markdown(b))
        if n.chart:
            lines.extend(render_chart_markdown(n.chart, n.bindings))
    return "\n".join(lines).rstrip() + "\n"


def _depth(n: SectionNode, nodes: list[SectionNode]) -> int:
    d, cur = 0, n
    seen: set[str | None] = set()
    while cur.parent_id is not None and cur.parent_id not in seen:
        seen.add(cur.parent_id)
        parent = next((m for m in nodes if m.section_id == cur.parent_id), None)
        if parent is None:
            break
        d += 1
        cur = parent
    return d


def _depth_key(n: SectionNode, nodes: list[SectionNode]) -> tuple[object, ...]:
    """为章节排序生成父级路径和同级顺序键。"""
    chain: list[int] = []
    cur, seen = n, set[str | None]()
    while cur.parent_id is not None and cur.parent_id not in seen:
        seen.add(cur.parent_id)
        parent = next((m for m in nodes if m.section_id == cur.parent_id), None)
        if parent is None:
            break
        chain.append(parent.ord)
        cur = parent
    return (*reversed(chain), n.ord)


# ── 载荷解析(agent 返回/库行 → 领域节点;畸形拒绝不静默修复)──


def _bindings_of(raw: object, *, status_fallback: str = DATA_OK) -> list[Binding]:
    if not isinstance(raw, list):
        return []
    out: list[Binding] = []
    for b in cast("list[object]", raw):
        if not isinstance(b, dict):
            continue
        d = cast("dict[str, object]", b)
        columns_raw = d.get("columns")
        rows_raw = d.get("rows")
        out.append(
            Binding(
                sql=str(d.get("sql") or ""),
                status=str(d.get("status") or status_fallback),
                columns=[str(c) for c in cast("list[object]", columns_raw or [])],
                rows=[
                    list(cast("list[object]", row)) for row in cast("list[object]", rows_raw or [])
                ],
                row_count=int(str(d.get("row_count") or 0)),
                truncated=bool(d.get("truncated")),
                fail_reason=cast("str | None", d.get("fail_reason")),
            )
        )
    return out


def section_from_row(r: dict[str, Any]) -> SectionNode:
    """版本章节库行 → 领域节点(bindings/chart jsonb 归一化:asyncpg 常回 str)。"""
    bindings_raw: object = r.get("bindings")
    if isinstance(bindings_raw, str):
        bindings_raw = json.loads(bindings_raw)
    chart: object = r.get("chart")
    if isinstance(chart, str):
        chart = json.loads(chart)
    return SectionNode(
        title=str(r["title"]),
        narrative_md=str(r.get("narrative_md") or ""),
        bindings=_bindings_of(bindings_raw),
        chart=cast("dict[str, object]", chart) if isinstance(chart, dict) else None,
        section_id=str(r["id"]),
        parent_id=str(r["parent_id"]) if r.get("parent_id") else None,
        ord=int(str(r.get("ord") or 0)),
        data_status=str(r.get("data_status") or DATA_PENDING),
        fail_reason=cast("str | None", r.get("fail_reason")),
    )


def sections_from_payload(raw: object) -> list[SectionNode]:
    """agent 返回的章节载荷 → 领域树(非 dict 条目/缺标题拒绝)。"""
    if not isinstance(raw, list):
        raise ValueError("章节载荷必须是数组")
    out: list[SectionNode] = []
    for i, item in enumerate(cast("list[object]", raw)):
        if not isinstance(item, dict):
            raise ValueError(f"章节条目畸形: index={i}")
        d = cast("dict[str, object]", item)
        title = str(d.get("title") or "")
        if not title.strip():
            raise ValueError(f"章节标题缺失: index={i}")
        chart = d.get("chart")
        out.append(
            SectionNode(
                title=title,
                narrative_md=str(d.get("narrative_md") or ""),
                bindings=_bindings_of(d.get("bindings") or [], status_fallback=DATA_OK),
                chart=cast("dict[str, object]", chart) if isinstance(chart, dict) else None,
                section_id=str(d.get("section_id") or f"s{i}"),
                parent_id=str(d["parent_id"]) if d.get("parent_id") else None,
                ord=int(str(d.get("ord") if d.get("ord") is not None else i)),
                data_status=str(d.get("data_status") or DATA_PENDING),
                fail_reason=cast("str | None", d.get("fail_reason")),
            )
        )
    return out
