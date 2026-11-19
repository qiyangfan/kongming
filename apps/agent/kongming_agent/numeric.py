"""数值纪律领域纯函数:白名单、审计剥离、派生量计算。

不依赖 Web 框架或数据库;审计只移除不可信数值,不拒绝请求也不触发 LLM 重试。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

# 数值等价容差(归一化后 abs diff)
_TOL = 1e-6

# 结构性常量:结果行数、预览行数(系统给出的计数,入白名单防误伤)
PREVIEW_ROWS = 5

# 句子边界(中英文句号/问号/感叹号/分号/换行)
_SENT_SPLIT = re.compile(r"(?<=[。！？!?；;\n])")

# 数字 token:可选符号/千分位/小数/科学计数/百分号
# 指数段必须进捕获组,否则 normalize 会把 1.5e3 读成 1.5
_NUM_RE = re.compile(
    r"(?<![A-Za-z0-9_])([+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?|[+-]?\d+(?:\.\d+)?)"
    r"((?:[eE][+-]?\d+)?)(\s*%)?(?![A-Za-z0-9_])"
)

# 时间列启发式(预计算环比用;失败不产出,不猜)
_TIME_NAME = re.compile(
    r"(?:^|_)(year|quarter|month|week|day|date|time|period)(?:$|_)", re.IGNORECASE
)


@dataclass(frozen=True)
class NumberToken:
    """文本中抽出并归一化的数值 token。"""

    raw: str
    value: float
    is_percent: bool
    start: int
    end: int


@dataclass
class AuditResult:
    """审计产物:剥离后的文本与统计(便于日志,不影响 columns/rows/stats)。"""

    summary: str
    insights: list[dict[str, str]]
    stripped_sentences: int = 0
    stripped_insights: int = 0


def normalize_number(raw: str) -> float | None:
    """归一化数字 token → float;失败返回 None。百分号取数值部分。"""
    s = raw.strip().replace(",", "").replace("，", "")
    if s.endswith("%"):
        s = s[:-1].strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def extract_numbers(text: str) -> list[NumberToken]:
    """抽取文本中的数值 token(含百分号标记)。"""
    out: list[NumberToken] = []
    for m in _NUM_RE.finditer(text):
        raw = m.group(0)
        mantissa, exponent, pct = m.group(1), m.group(2) or "", m.group(3) or ""
        # 日历年份标记(2024年)不作数据数值,避免误伤含年份的合法句
        if not pct and not exponent and text[m.end() :].lstrip().startswith("年"):
            continue
        val = normalize_number(mantissa + exponent + pct)
        if val is None:
            continue
        out.append(
            NumberToken(
                raw=raw.strip(),
                value=val,
                is_percent=bool(pct),
                start=m.start(),
                end=m.end(),
            )
        )
    return out


def value_in_whitelist(value: float, *, is_percent: bool, whitelist: Sequence[float]) -> bool:
    """数值等价比较:1 与 1.0 等价;百分数 0.25 与 25% 按容差映射后判定。"""
    candidates = [value]
    if is_percent:
        candidates.append(value / 100.0)
        candidates.append(value * 100.0)
    for cand in candidates:
        for w in whitelist:
            if abs(cand - w) <= _TOL * max(1.0, abs(cand), abs(w)):
                return True
    return False


def build_whitelist(
    *,
    columns: Sequence[str] | None = None,
    rows: Sequence[Sequence[object]] | None = None,
    stats: Mapping[str, object] | None = None,
    compute_values: Iterable[float] | None = None,
    structural: Iterable[float] | None = None,
) -> list[float]:
    """数值白名单 = 结果集数值 ∪ stats 数值 ∪ compute 返回 ∪ 结构性常量。"""
    del columns  # 列名不贡献数值;保留参数便于调用方对称传参
    out: list[float] = []

    def _feed(v: object) -> None:
        if isinstance(v, bool):
            return
        if isinstance(v, (int, float)):
            out.append(float(v))

    for row in rows or []:
        for cell in row:
            _feed(cell)
    for v in (stats or {}).values():
        _feed(v)
    for v in compute_values or []:
        _feed(v)
    for v in structural or []:
        _feed(v)
    return out


def _split_sentences(text: str) -> list[str]:
    parts = _SENT_SPLIT.split(text)
    return [p for p in parts if p.strip()]


def audit_text(text: str, whitelist: Sequence[float]) -> tuple[str, int]:
    """剥离含非白名单数值的句子;零违规时逐字返回原文。返回 (文本, 剥离句数)。"""
    if not text:
        return text, 0
    sentences = _split_sentences(text)
    kept: list[str] = []
    stripped = 0
    for sent in sentences:
        tokens = extract_numbers(sent)
        if all(
            value_in_whitelist(t.value, is_percent=t.is_percent, whitelist=whitelist)
            for t in tokens
        ):
            kept.append(sent)
        else:
            stripped += 1
    if stripped == 0:
        return text, 0
    return "".join(kept).strip(), stripped


def filter_insights(
    insights: Sequence[Mapping[str, str]],
    *,
    n_rows: int,
    whitelist: Sequence[float],
) -> list[dict[str, str]]:
    """trend/anomaly 收紧 + 同口径数值审计剥离。"""
    out: list[dict[str, str]] = []
    for item in insights:
        itype = str(item.get("type") or "").strip().lower()
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        if itype == "trend" and n_rows < 3:
            continue
        if itype == "anomaly":
            tokens = extract_numbers(text)
            if tokens and not all(
                value_in_whitelist(t.value, is_percent=t.is_percent, whitelist=whitelist)
                for t in tokens
            ):
                continue
        cleaned, _ = audit_text(text, whitelist)
        if cleaned:
            out.append({"type": itype, "text": cleaned})
    return out


def audit_summarize_output(
    summary: str,
    insights: Sequence[Mapping[str, str]],
    *,
    columns: Sequence[str] | None = None,
    rows: Sequence[Sequence[object]] | None = None,
    stats: Mapping[str, object] | None = None,
    compute_values: Iterable[float] | None = None,
    structural: Iterable[float] | None = None,
) -> AuditResult:
    """summarize/finalize 同口径收口。零违规时 summary/insights 与入参等价。"""
    n_rows = len(rows) if rows is not None else 0
    rows_val = stats.get("rows") if stats else None
    if isinstance(rows_val, (int, float)) and not isinstance(rows_val, bool):
        n_rows = max(n_rows, int(rows_val))
    struct = list(structural) if structural is not None else [float(n_rows), float(PREVIEW_ROWS)]
    wl = build_whitelist(
        columns=columns,
        rows=rows,
        stats=stats,
        compute_values=compute_values,
        structural=struct,
    )
    new_summary, n_stripped = audit_text(summary, wl)
    new_insights = filter_insights(insights, n_rows=n_rows, whitelist=wl)
    return AuditResult(
        summary=new_summary,
        insights=new_insights,
        stripped_sentences=n_stripped,
        stripped_insights=max(0, len(insights) - len(new_insights)),
    )


def _assert_whitelisted(name: str, value: float, whitelist: Sequence[float]) -> str | None:
    if not value_in_whitelist(value, is_percent=False, whitelist=whitelist):
        return (
            f"入参 {name}={value} 不在本轮数值白名单(结果集/stats/既有计算值)，"
            f"请改用查询结果或统计中的数字"
        )
    return None


def compute_delta(
    base: float,
    target: float,
    *,
    whitelist: Sequence[float] | None = None,
) -> dict[str, object]:
    """增幅百分比 (target-base)/abs(base)*100;入参须在白名单(若提供)。"""
    if whitelist is not None:
        for name, val in (("base", base), ("target", target)):
            err = _assert_whitelisted(name, float(val), whitelist)
            if err:
                return {"status": "error", "reason": err}
    if base == 0:
        return {"status": "error", "reason": "base=0,无法计算增幅"}
    pct = (float(target) - float(base)) / abs(float(base)) * 100.0
    return {
        "status": "executed",
        "delta_pct": round(pct, 4),
        "inputs": {"base": float(base), "target": float(target)},
    }


def compute_share(
    part: float,
    total: float,
    *,
    whitelist: Sequence[float] | None = None,
) -> dict[str, object]:
    """占比 part/total;入参须在白名单(若提供)。"""
    if whitelist is not None:
        for name, val in (("part", part), ("total", total)):
            err = _assert_whitelisted(name, float(val), whitelist)
            if err:
                return {"status": "error", "reason": err}
    if total == 0:
        return {"status": "error", "reason": "total=0,无法计算占比"}
    ratio = float(part) / float(total)
    return {
        "status": "executed",
        "share": round(ratio, 6),
        "share_pct": round(ratio * 100.0, 4),
        "inputs": {"part": float(part), "total": float(total)},
    }


def compute_result_values(result: Mapping[str, object]) -> list[float]:
    """compute 工具返回值中应入白名单的数值(不含入参快照)。"""
    out: list[float] = []
    for key in ("delta_pct", "share", "share_pct"):
        v = result.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out.append(float(v))
    return out


def _looks_like_time_col(name: str) -> bool:
    return bool(_TIME_NAME.search(name))


def _time_sort_key(r: Sequence[object], t_idx: int) -> tuple[int, float, str]:
    """时间列排序键:数值期按数值序(避免 "10" < "9" 的字典序)。"""
    v = r[t_idx] if t_idx < len(r) else None
    if v is None:
        return (2, 0.0, "")
    if isinstance(v, bool):
        return (1, 0.0, str(v))
    if isinstance(v, (int, float)):
        return (0, float(v), "")
    s = str(v)
    try:
        return (0, float(s.replace(",", "")), "")
    except ValueError:
        return (1, 0.0, s)


def _column_numbers(rows: Sequence[Sequence[object]], ci: int) -> list[float]:
    """取某列的数值项(跳过缺列与非数值)。"""
    nums: list[float] = []
    for row in rows:
        if ci >= len(row):
            continue
        v = row[ci]
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            nums.append(float(v))
    return nums


def _column_derived(col: str, nums: list[float]) -> dict[str, float]:
    """单列派生量:末两行环比与末值占列和。"""
    if len(nums) < 2:
        return {}
    prev, last = nums[-2], nums[-1]
    out: dict[str, float] = {}
    if prev != 0:
        out[f"{col}_delta_pct"] = round((last - prev) / abs(prev) * 100.0, 4)
    total = sum(nums)
    if total != 0:
        out[f"{col}_share_pct"] = round(last / total * 100.0, 4)
    return out


def precompute_derived_stats(
    columns: Sequence[str], rows: Sequence[Sequence[object]]
) -> dict[str, float]:
    """常见派生量预计算:末两行环比 + 末值占列和。时间列启发式失败则不产出。"""
    if not columns or len(rows) < 2:
        return {}
    time_idx = next((i for i, c in enumerate(columns) if _looks_like_time_col(c)), None)
    if time_idx is None:
        return {}
    try:
        ordered = sorted(rows, key=lambda r: _time_sort_key(r, time_idx))
    except TypeError:
        return {}
    out: dict[str, float] = {}
    for ci, col in enumerate(columns):
        if ci == time_idx:
            continue
        out.update(_column_derived(col, _column_numbers(ordered, ci)))
    return out
