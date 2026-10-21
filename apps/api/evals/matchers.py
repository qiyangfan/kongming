"""Execution Match 的严格与宽松比较纯函数。

比较前统一规范化值;宽松评分先按 gold 列投影比较行集,列名不兼容时
退回数值子集比较;只有 gold SQL 含 ORDER BY 时才要求保序。
"""

import math
import uuid
from collections.abc import Sequence
from datetime import date, datetime, time
from decimal import Decimal

Row = tuple[object, ...]


def canon_cell(v: object) -> object:
    """值规范化:数值 round 1e-6(非有限→None,对齐 api _json_cell)、时间
    isoformat、UUID 转字符串(gold 侧 asyncpg 返回 uuid.UUID,gen 侧经 JSON
    已是字符串)、其余原样。"""
    if isinstance(v, bool):  # bool 是 int 子类,先判(bool 不入数值通道)
        return v
    if isinstance(v, (int, float, Decimal)):
        f = float(v)
        return None if not math.isfinite(f) else round(f, 6)
    if isinstance(v, (datetime, date, time)):
        return v.isoformat()
    if isinstance(v, uuid.UUID):
        return str(v)
    return v


def _canon_rows(rows: Sequence[Sequence[object]]) -> list[Row]:
    return [tuple(canon_cell(v) for v in row) for row in rows]


def _safe_sort_key(row: Row) -> tuple[tuple[bool, str, object], ...]:
    """multiset 比较的排序键:None 与异类型混排安全(类型名先序,同类型可比)。"""
    key: list[tuple[bool, str, object]] = []
    for v in row:
        if v is None:
            key.append((True, "", ""))
        elif isinstance(v, bool):
            key.append((False, "0bool", v))
        elif isinstance(v, (int, float)):
            key.append((False, "1num", v))
        else:
            key.append((False, "2str", v))
    return tuple(key)


def result_set_equal(
    gold_rows: Sequence[Sequence[object]], gen_rows: Sequence[Sequence[object]]
) -> bool:
    """Execution Match(严格):归一化后 multiset 精确相等(排序无关)。"""
    a = _canon_rows(gold_rows)
    b = _canon_rows(gen_rows)
    return sorted(a, key=_safe_sort_key) == sorted(b, key=_safe_sort_key)


def rows_approx_equal(
    gold_rows: Sequence[Sequence[object]],
    gen_rows: Sequence[Sequence[object]],
    ordered: bool,
) -> bool:
    """行集合近似等值(数值已 canon 至 1e-6):ordered 保序,否则 multiset。"""
    a = _canon_rows(gold_rows)
    b = _canon_rows(gen_rows)
    if ordered:
        return a == b
    return sorted(a, key=_safe_sort_key) == sorted(b, key=_safe_sort_key)


def columns_compatible(gold_cols: list[str], gen_cols: list[str]) -> bool:
    """列等价:列名集合相等或 gold ⊆ gen(大小写不敏感;gold 只要 ID、
    LLM 多给姓名列不扣分)。"""
    gold_set = {c.lower() for c in gold_cols}
    gen_set = {c.lower() for c in gen_cols}
    return gold_set == gen_set or gold_set.issubset(gen_set)


def project_rows(
    cols: list[str], rows: Sequence[Sequence[object]], target_cols: list[str]
) -> list[Row] | None:
    """rows 按列名投影到 target_cols(大小写不敏感,按 target 顺序重排)——
    gold ⊂ gen 时元数不同无法逐行比;缺列返回 None(防御)。"""
    lower = [c.lower() for c in cols]
    idx: list[int] = []
    for t in target_cols:
        try:
            idx.append(lower.index(t.lower()))
        except ValueError:
            return None
    return [tuple(row[i] for i in idx) for row in rows]


def numeric_multiset(rows: Sequence[Sequence[object]]) -> list[float]:
    """提取结果集全部数值元素(排除 bool)round 1e-6 后排序——容忍列投影/
    聚合别名差异的核心口径。"""
    nums: list[float] = []
    for row in rows:
        for v in row:
            if isinstance(v, bool):
                continue
            if isinstance(v, (int, float, Decimal)):
                f = float(v)
                if math.isfinite(f):
                    nums.append(round(f, 6))
    return sorted(nums)


def result_set_loose_match(
    gold_cols: list[str],
    gold_rows: Sequence[Sequence[object]],
    gen_cols: list[str],
    gen_rows: Sequence[Sequence[object]],
    gold_ordered: bool = False,
) -> bool:
    """Execution Match(宽松,黄金指标):路径 1 列等价+投影行近似,路径 2
    数值子集(双指针);纯文本回退严格集合等值。"""
    # 路径 1:列等价 + 投影后行近似等值
    if columns_compatible(gold_cols, gen_cols):
        gen_proj = project_rows(gen_cols, gen_rows, gold_cols)
        if gen_proj is not None and rows_approx_equal(gold_rows, gen_proj, gold_ordered):
            return True
    # 路径 2:数值 multiset 子集(两侧已排序,双指针)
    gold_nums = numeric_multiset(gold_rows)
    gen_nums = numeric_multiset(gen_rows)
    if not gold_nums:
        # 纯文本结果集:子集恒真会误判,回退严格集合等值兜底
        # (列别名不同但内容完全正确的 gen 不应误判 fail)
        return result_set_equal(gold_rows, gen_rows)
    i = j = 0
    while i < len(gold_nums) and j < len(gen_nums):
        if gold_nums[i] == gen_nums[j]:
            i += 1
            j += 1
        elif gold_nums[i] > gen_nums[j]:
            j += 1
        else:
            return False  # gold[i] 在 gen 中找不到,子集失败
    return i == len(gold_nums)
