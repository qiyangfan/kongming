"""答案数值的槽位契约:槽位注册、契约校验与确定性渲染。

数字与含数字标签由系统渲染(模型只写 `{{槽位}}` 引用),替代对自由文本的
数值抽取与白名单比对——判定只查文法与键界,与租户词汇无关。

渲染只按值类型格式化,不按列名推断单位或业务语义:多租户任意数据源没有
统一的命名约定,`*_pct`/`rate`/`total` 等列名不构成百分比、比率或总量的证据;
单位与口径由 SQL 投影、数据源元数据或用户问句决定。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime

from kongming_agent import numeric
from kongming_agent.graph.streams import emit_event

# 槽位 token:{{键}}(键内不含空白与花括号);未成对/空键会残留为畸形
_SLOT_TOKEN = re.compile(r"\{\{([^{}\s]+)\}\}")
# 引用键标记(【…】)属系统文法,扫描数字前剥离,防十六进制后缀误伤
_CITATION = re.compile(r"【[^【】]*】")
_CELL_KEY = re.compile(r"cell\.(\d+)\.(\d+)")
_DIGIT = re.compile(r"[0-9０-９]")
# 文档逐字校验的数值 token:连续数字段(可含 - , . 连接的段),逐字匹配
_DOC_NUMBER = re.compile(r"[0-9０-９]+(?:[.,][0-9０-９]+)*")
# 键内出现空白/花括号即不可作槽位键(列名兜底改用列序号)
_UNSAFE_KEY = re.compile(r"[{}\s]")

# 预览行数(与 summarize 上下文一致;仅用于提示说明)
PREVIEW_ROWS = 5


def format_number(value: int | float) -> str:
    """数值渲染:整数原样,小数最多两位并去尾零。"""
    if isinstance(value, int):
        return str(value)
    text = f"{float(value):.2f}".rstrip("0").rstrip(".")
    return text or "0"


def format_cell(value: object) -> str:
    """单元格渲染:空值「空」,布尔「是/否」,日期取日期部分,数值按值格式化。

    不从列名推断单位(百分比/币种/比率):列名在多租户数据源里不可信,
    单位由 SQL 投影或数据源元数据负责。
    """
    if value is None:
        return "空"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (int, float)):
        return format_number(value)
    return str(value)


def _col_key(col: str, idx: int) -> str:
    return col if col and not _UNSAFE_KEY.search(col) else f"c{idx}"


@dataclass(frozen=True)
class Extreme:
    """某度量列的极值行:值、行号与标识列文本。"""

    row: int
    value: int | float
    label: str | None
    label_col: str | None


def _extreme_label(
    columns: Sequence[str],
    row_cells: Sequence[object],
    idx: int,
    label_index: int | None,
) -> tuple[str | None, str | None]:
    """取极值行的标签与列名:优先问句关注列,否则首个非空文本列。"""
    if label_index is not None and label_index != idx and label_index < len(row_cells):
        preferred = row_cells[label_index]
        if isinstance(preferred, str) and preferred.strip():
            return format_cell(preferred), columns[label_index]
    for ci, col in enumerate(columns):
        if ci == idx or ci >= len(row_cells):
            continue
        value = row_cells[ci]
        if isinstance(value, str) and value.strip():
            return format_cell(value), col
    return None, None


def _extreme(
    columns: Sequence[str],
    rows: Sequence[Sequence[object]],
    idx: int,
    *,
    want_max: bool,
    label_index: int | None = None,
) -> Extreme | None:
    """在列 idx 上取最大/最小值(并列取首行);标签优先取问句关注的文本列。"""
    candidates = ((r, row[idx]) for r, row in enumerate(rows) if idx < len(row))
    nums = [
        (r, v) for r, v in candidates if isinstance(v, (int, float)) and not isinstance(v, bool)
    ]
    if not nums:
        return None
    target = max(v for _, v in nums) if want_max else min(v for _, v in nums)
    row_idx = next(r for r, v in nums if v == target)
    label, label_col = _extreme_label(columns, rows[row_idx], idx, label_index)
    return Extreme(row=row_idx, value=target, label=label, label_col=label_col)


def _normalize_label_text(text: str) -> str:
    """标签匹配归一:去空白/标点并转小写;中文按连续字保留。"""
    return re.sub(r"[\W_]+", "", text.lower())


_LABEL_CORE_SUFFIXES = ("名称", "名字", "编号", "ID", "id")
_FOCUS_CUE = re.compile(
    r"(?:哪个|哪些|哪种|哪类|来自|属于|位于|所在|按|每个|每|各)"
    r"(?:哪个|哪些)?([\u4e00-\u9fff]{2,8}?)"
    r"(?=的|和|与|、|，|。|；|？|\?|！|!|按|每|各|在|从|到|为|是|"
    r"统计|查询|列出|显示|计算|求|$)"
)


def _label_core(label: str) -> str:
    """取列标签的核心名词,剥离名称/编号类后缀;状态/金额等保持原样。"""
    value = label.strip()
    for suffix in _LABEL_CORE_SUFFIXES:
        if value.endswith(suffix) and len(value) > len(suffix):
            return value[: -len(suffix)]
    return value


def _has_text_column(rows: Sequence[Sequence[object]], idx: int) -> bool:
    """结果列是否存在非空文本值(只有文本列可作可读标签)。"""
    for row in rows:
        if idx < len(row):
            value = row[idx]
            if isinstance(value, str) and value.strip():
                return True
    return False


def focus_label_index(
    columns: Sequence[str],
    rows: Sequence[Sequence[object]],
    question: str,
    column_labels: Mapping[str, str] | None = None,
) -> int | None:
    """选问句关注的可读文本列;无明确维度线索时返回 None。"""
    terms = {_normalize_label_text(m.group(1)) for m in _FOCUS_CUE.finditer(question)}
    terms.discard("")
    if not terms:
        return None
    labels = column_labels or {}
    best_idx: int | None = None
    best_score = 0
    for idx, col in enumerate(columns):
        if not _has_text_column(rows, idx):
            continue
        core = _normalize_label_text(_label_core(labels.get(col, "") or col))
        if core in terms and len(core) > best_score:
            best_idx, best_score = idx, len(core)
    return best_idx


def summary_mentions_label(
    text: str,
    columns: Sequence[str],
    rows: Sequence[Sequence[object]],
    label_index: int | None,
    column_labels: Mapping[str, str] | None = None,
) -> bool:
    """总结是否覆盖问句关注的维度(列名/注释或任一该列值);无关注列时恒真。"""
    if label_index is None or label_index >= len(columns):
        return True
    labels = column_labels or {}
    semantic = labels.get(columns[label_index], "")
    if semantic and semantic in text:
        return True
    if columns[label_index] in text:
        return True
    for row in rows:
        if label_index < len(row):
            value = row[label_index]
            if isinstance(value, str) and value.strip() and value in text:
                return True
    return False


@dataclass
class SlotRegistry:
    """一轮的槽位表:静态键 + 单元格按需解析(行/列界内)。

    scope 非空时键须带该前缀(如 `r0.rows`),用于 ReAct 多次执行时隔离
    同名槽位;recipe 单结果路径保持无前缀。
    """

    values: dict[str, str]
    descriptions: dict[str, str]
    columns: Sequence[str] = field(default_factory=tuple)
    rows: Sequence[Sequence[object]] = field(default_factory=tuple)
    scope: str = ""

    def resolve(self, key: str) -> str | None:
        """解析槽位键;不在静态表且非界内单元格即返回 None(调用方判违约)。"""
        if self.scope:
            prefix = f"{self.scope}."
            if not key.startswith(prefix):
                return None
            key = key[len(prefix) :]
        if key in self.values:
            return self.values[key]
        m = _CELL_KEY.fullmatch(key)
        if m is not None:
            r, c = int(m.group(1)), int(m.group(2))
            if r < len(self.rows) and c < len(self.columns) and c < len(self.rows[r]):
                return format_cell(self.rows[r][c])
        return None

    def _display_key(self, key: str) -> str:
        return f"{self.scope}.{key}" if self.scope else key

    def prompt_block(self) -> str:
        """提示词用槽位清单:列静态键与单元格形态;越界引用由解析期拒绝。"""
        lines = ["可用槽位(只能引用下列键;槽位之外禁止出现任何阿拉伯数字):"]
        lines.extend(
            f"- {{{{{self._display_key(key)}}}}} = {self.descriptions[key]}"
            for key in sorted(self.descriptions)
        )
        if self.columns:
            colmap = ", ".join(f"{i}={c}" for i, c in enumerate(self.columns))
            lines.append(
                f"- {{{{{self._display_key('cell.<行号>.<列号>')}}}}} = 结果单元格"
                f"(0 起;共 {len(self.rows)} 行;列号: {colmap})"
            )
        return "\n".join(lines)


@dataclass
class ScopedSlotRegistry:
    """多个结果作用域的合并解析:键前缀互斥,任一作用域命中即返回。"""

    registries: Sequence[SlotRegistry]

    def resolve(self, key: str) -> str | None:
        for registry in self.registries:
            value = registry.resolve(key)
            if value is not None:
                return value
        return None


def _put(
    values: dict[str, str], descriptions: dict[str, str], key: str, text: str, desc: str
) -> None:
    if _UNSAFE_KEY.search(key) or key in values:
        return
    values[key] = text
    descriptions[key] = desc


def _add_stats_slots(
    values: dict[str, str], descriptions: dict[str, str], stats: Mapping[str, object]
) -> None:
    for key, val in stats.items():
        if key == "rows":
            continue
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            # 仅系统预计算的派生统计键(*_delta_pct/*_share_pct)是百分数;
            # 结果列是否百分比不得由列名判断,cell/argmax 一律原值渲染
            if key.endswith("_pct"):
                text = f"{format_number(val)}%"
                desc = f"统计值 {key}(百分比)"
            else:
                text = format_number(val)
                desc = f"统计值 {key}"
            _put(values, descriptions, f"stats.{key}", text, desc)


def _add_extreme_slots(
    values: dict[str, str],
    descriptions: dict[str, str],
    columns: Sequence[str],
    rows: Sequence[Sequence[object]],
    stats: Mapping[str, object],
    *,
    label_index: int | None = None,
) -> None:
    for idx, col in enumerate(columns):
        base = _col_key(col, idx)
        for suffix, want_max, name in (("max", True, "最大"), ("min", False, "最小")):
            if f"{col}_{suffix}" not in stats:
                continue
            ext = _extreme(columns, rows, idx, want_max=want_max, label_index=label_index)
            if ext is None:
                continue
            _put(
                values,
                descriptions,
                f"arg{suffix}.{base}.value",
                format_number(ext.value),
                f"{col} 的{name}值",
            )
            if ext.label is not None:
                label_col = ext.label_col or ""
                _put(
                    values,
                    descriptions,
                    f"arg{suffix}.{base}.label",
                    ext.label,
                    f"{col} {name}值所在行的标识文本(取自 {label_col} 列)",
                )


def _add_premise_slots(values: dict[str, str], descriptions: dict[str, str], question: str) -> None:
    for i, tok in enumerate(numeric.extract_numbers(question)):
        text = format_number(tok.value) + ("%" if tok.is_percent else "")
        desc = f"问句中的第 {i + 1} 个数字" + ("(百分比)" if tok.is_percent else "")
        _put(values, descriptions, f"q.{i}", text, desc)


def _add_calc_slots(
    values: dict[str, str],
    descriptions: dict[str, str],
    compute_slots: Mapping[str, int | float] | None,
) -> None:
    for key, val in (compute_slots or {}).items():
        if isinstance(val, bool):
            continue
        if key.split(".", 1)[0] in ("delta_pct", "share_pct"):
            _put(
                values,
                descriptions,
                f"calc.{key}",
                f"{format_number(val)}%",
                f"计算工具结果 {key}(百分比)",
            )
        else:
            _put(values, descriptions, f"calc.{key}", format_number(val), f"计算工具结果 {key}")


def _add_alias_slots(
    values: dict[str, str], descriptions: dict[str, str], aliases: Sequence[tuple[str, str]]
) -> None:
    for i, (alias, physical) in enumerate(aliases):
        if alias:
            _put(values, descriptions, f"alias.{i}", alias, "表的业务称谓")
        if physical:
            _put(values, descriptions, f"alias.{i}.physical", physical, "表的物理全名")


def _add_null_slots(
    values: dict[str, str],
    descriptions: dict[str, str],
    columns: Sequence[str],
    rows: Sequence[Sequence[object]],
) -> None:
    for idx, col in enumerate(columns):
        nulls = sum(1 for row in rows if idx < len(row) and row[idx] is None)
        if nulls:
            _put(
                values,
                descriptions,
                f"nulls.{_col_key(col, idx)}",
                str(nulls),
                f"{col} 列的空值个数",
            )


def build_registry(
    *,
    question: str,
    columns: Sequence[str],
    rows: Sequence[Sequence[object]],
    stats: Mapping[str, object],
    compute_slots: Mapping[str, int | float] | None = None,
    aliases: Sequence[tuple[str, str]] = (),
    scope: str = "",
    column_labels: Mapping[str, str] | None = None,
    label_question: str = "",
    label_index: int | None = None,
) -> SlotRegistry:
    """按本轮结果形态构造槽位表(只暴露存在的键;不改对外 stats)。"""
    values: dict[str, str] = {}
    desc: dict[str, str] = {}
    focus = label_index
    if focus is None:
        focus = focus_label_index(columns, rows, label_question or question, column_labels)
    _put(values, desc, "rows", str(len(rows)), "结果行数")
    _add_stats_slots(values, desc, stats)
    _add_extreme_slots(values, desc, columns, rows, stats, label_index=focus)
    _add_premise_slots(values, desc, question)
    _add_calc_slots(values, desc, compute_slots)
    _add_alias_slots(values, desc, aliases)
    _add_null_slots(values, desc, columns, rows)
    return SlotRegistry(values=values, descriptions=desc, columns=columns, rows=rows, scope=scope)


def build_calc_registry(compute_slots: Mapping[str, int | float] | None) -> SlotRegistry:
    """只含计算工具槽位的无作用域注册表(跨多次执行共用,键不与结果坐标冲突)。"""
    values: dict[str, str] = {}
    descriptions: dict[str, str] = {}
    _add_calc_slots(values, descriptions, compute_slots)
    return SlotRegistry(values=values, descriptions=descriptions)


def parse_schema_aliases(schema_hint: str) -> list[tuple[str, str]]:
    """从召回形态 schema_hint 解析(业务称谓, 物理全名)。

    只有 `schema.table(别名)(列清单)` 的双括号形态才算别名;单括号是列清单,
    无法区分时按无别名处理(与表说明段注入的判定同口径)。
    """
    out: list[tuple[str, str]] = []
    for line in schema_hint.splitlines():
        m = re.match(r"^([A-Za-z_]\w*)\.([A-Za-z_]\w*)\(([^()]+)\)\(", line)
        if m is not None:
            out.append((m.group(3).strip(), f"{m.group(1)}.{m.group(2)}"))
    return out


@dataclass(frozen=True)
class ContractResult:
    """契约校验产物:渲染文本 + 违约类型(合规为 None)。"""

    text: str
    violation: str | None = None
    detail: str | None = None


def apply_contract(
    text: str,
    registry: SlotRegistry | ScopedSlotRegistry,
    *,
    doc_parts: Sequence[str] | None = None,
) -> ContractResult:
    """按槽位契约校验并渲染。

    数据面:去槽位后不得残留阿拉伯数字(半角/全角),不豁免引用键。
    融合面传 doc_parts 时:引用键不参与扫描,残留数字必须是某段下发文档原文的逐字片段。
    违约返回违规类型,调用方弃用整段文本(不做句级删除)。
    """
    if not text:
        return ContractResult(text)
    unknown: list[str] = []

    def _replace(m: re.Match[str]) -> str:
        key = m.group(1)
        val = registry.resolve(key)
        if val is None:
            unknown.append(key)
            return ""
        return val

    rendered = _SLOT_TOKEN.sub(_replace, text)
    scanned = _CITATION.sub("", text) if doc_parts is not None else text
    scan = _SLOT_TOKEN.sub("", scanned)
    if unknown:
        return ContractResult(rendered, "unknown_slot", unknown[0])
    violation, detail = _scan_violation(scan, doc_parts)
    return ContractResult(rendered, violation, detail)


def _scan_violation(scan: str, doc_parts: Sequence[str] | None) -> tuple[str | None, str | None]:
    """扫描残留文本:括号畸形、字面数字(数据面)或非原文数字(融合面)。"""
    if "{" in scan or "}" in scan:
        return "malformed", None
    if doc_parts is None:
        return ("literal_digit", None) if _DIGIT.search(scan) else (None, None)
    normalized = [re.sub(r"\s+", "", part) for part in doc_parts]
    for m in _DOC_NUMBER.finditer(scan):
        raw = m.group(0)
        if not any(raw in part for part in normalized):
            return "doc_number_unquoted", raw
    return None, None


def emit_contract_event(*, node: str, result: ContractResult, tier: str) -> None:
    """契约结果入 trace:违约类型、具体键与兜底层级可观测。"""
    emit_event(
        "trace",
        {
            "node": node,
            "fields": {
                "kind": "answer_contract",
                "violation": result.violation or "none",
                "detail": result.detail or "",
                "tier": tier,
            },
        },
    )


def _measure_word(question: str) -> str:
    """问句量词只取「多少/几」后的量词;不把阈值里的货币/日期单位当答案单位。"""
    match = re.search(r"(?:多少|几)(美元|条|个|位|名|笔|次|家|人|台|件|部|元|%)", question)
    return match.group(1) if match is not None else ""


# 措辞只由执行 SQL 的聚合结构决定(调用方经 scalar_aggregate_kind 传入),不看列名
_SCALAR_PHRASES = {
    "count": "共",
    "sum": "合计为",
    "avg": "平均为",
    "max": "最高为",
    "min": "最低为",
}


def apply_summary_contract(
    registry: SlotRegistry | ScopedSlotRegistry,
    summary: str,
    insights: Sequence[Mapping[str, str]],
) -> tuple[str, list[dict[str, str]], ContractResult]:
    """summary/insights 过契约;返回(渲染文本或空, 合法洞察, 最差违约)。"""
    checked = apply_contract(summary, registry) if summary else ContractResult("")
    worst = checked
    rendered: list[dict[str, str]] = []
    for item in insights:
        r = apply_contract(str(item.get("text") or ""), registry)
        if r.violation is None and r.text:
            rendered.append({"type": str(item.get("type") or ""), "text": r.text})
        elif worst.violation is None:
            worst = r
    text = checked.text if checked.violation is None else ""
    return text, rendered, worst


def scalar_answer(
    *,
    column: str,
    value: int | float,
    question: str,
    aggregate: str | None = None,
    unit_question: str = "",
) -> str:
    """单行单列数值的确定性作答(不经语言模型)。

    措辞取自执行 SQL 的聚合结构;列名只作标签。量词优先取用户原问句,
    改写问句只在原问句没有量词时兜底。
    """
    unit = _measure_word(unit_question) or _measure_word(question)
    suffix = f" {unit}" if unit else ""
    number = format_number(value)
    phrase = _SCALAR_PHRASES.get((aggregate or "").lower())
    if phrase == "共":
        return f"共 {number}{suffix}。"
    if phrase:
        return f"{phrase} {number}{suffix}。"
    if not column or column.startswith("?"):
        return f"结果为 {number}{suffix}。"
    return f"{column} 为 {number}{suffix}。"


def _focus_values(
    columns: Sequence[str], rows: Sequence[Sequence[object]], label_index: int, limit: int = 5
) -> tuple[list[str], bool]:
    """取问句关注列的去重值(保持行序);返回是否还有未列出的值。"""
    if label_index >= len(columns):
        return [], False
    values: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if label_index >= len(row):
            continue
        value = row[label_index]
        if not isinstance(value, str) or not value.strip():
            continue
        text = format_cell(value)
        if text in seen:
            continue
        seen.add(text)
        if len(values) < limit:
            values.append(text)
    return values, len(seen) > len(values)


def outline_answer(
    *,
    columns: Sequence[str],
    rows: Sequence[Sequence[object]],
    stats: Mapping[str, object],
    label_index: int | None = None,
) -> str:
    """多行结果的确定性轮廓(违约/渲染失败兜底;不罗列 max/min/avg)。"""
    n = len(rows)
    focus = ""
    if label_index is not None:
        values, truncated = _focus_values(columns, rows, label_index)
        if values:
            focus = f"{columns[label_index]} 包括 {'、'.join(values)}{' 等' if truncated else ''}"
    metric: tuple[int, str] | None = None
    for idx, col in enumerate(columns):
        if f"{col}_max" in stats:
            metric = (idx, col)
            break
    if metric is None:
        return f"共 {n} 行；{focus}。" if focus else f"共 {n} 行。"
    idx, col = metric
    hi = _extreme(columns, rows, idx, want_max=True, label_index=label_index)
    lo = _extreme(columns, rows, idx, want_max=False, label_index=label_index)
    if hi is None or lo is None:
        return f"共 {n} 行；{focus}。" if focus else f"共 {n} 行。"
    prefix = f"共 {n} 行；" + (f"{focus}；" if focus else "")
    if hi.label is not None and lo.label is not None and hi.label_col:
        if focus:
            return (
                f"{prefix}其中 {hi.label} 的 {col} 最高（{format_number(hi.value)}），"
                f"{lo.label} 最低（{format_number(lo.value)}）。"
            )
        return (
            f"{prefix}{hi.label_col} 为 {hi.label} 的 {col} 最高（{format_number(hi.value)}），"
            f"{lo.label} 最低（{format_number(lo.value)}）。"
        )
    return f"{prefix}{col} 最高为 {format_number(hi.value)}，最低为 {format_number(lo.value)}。"
