"""chat 领域模型(零框架,宪法 Article II):图表配置建议纯函数。

推断规则(chat spec「问答查询端点」):
- 首列日期/时间 → line(时序趋势);
- 首列低基数分类(≤8 类)+ 有数值列 → pie(占比);
- 首列分类 + 有数值列(基数 ≤50) → bar;
- 其余(明细/无数值列) → none。
基数 >50 推定为明细行,不强加图表。
"""

import datetime as dt
import re
from dataclasses import dataclass
from decimal import Decimal

_PIE_MAX_CARDINALITY = 8  # 低基数分类上界(chat spec)
_BAR_MAX_CARDINALITY = 50  # 超此基数推定明细,不配图

_DATE_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}")


@dataclass(frozen=True)
class ChartSuggestion:
    """type: line / bar / pie / none;x/y 为列名(none 时为空)。"""

    type: str
    x: str | None = None
    y: str | None = None


def _is_temporal(v: object) -> bool:
    if isinstance(v, dt.date | dt.datetime):
        return True
    return isinstance(v, str) and bool(_DATE_PREFIX.match(v))


def _is_numeric(v: object) -> bool:
    # bool 是 int 子类,显式排除(PG BOOLEAN 经驱动成 True/False)
    return isinstance(v, (int, float, Decimal)) and not isinstance(v, bool)


def suggest_chart(columns: list[str], rows: list[list[object]]) -> ChartSuggestion:
    if len(columns) < 2 or not rows:
        return ChartSuggestion("none")
    first_vals = [r[0] for r in rows if len(r) > 0]
    if not first_vals:
        return ChartSuggestion("none")
    numeric_cols = [
        i
        for i in range(1, len(columns))
        if any(_is_numeric(r[i]) for r in rows if len(r) > i)
    ]
    if not numeric_cols:
        return ChartSuggestion("none")
    y = columns[numeric_cols[0]]
    if all(_is_temporal(v) for v in first_vals):
        return ChartSuggestion("line", x=columns[0], y=y)
    if any(_is_numeric(v) for v in first_vals):
        return ChartSuggestion("none")  # 首列即数值:非典型分组形态,不猜
    cardinality = len({str(v) for v in first_vals})
    if cardinality <= _PIE_MAX_CARDINALITY:
        return ChartSuggestion("pie", x=columns[0], y=y)
    if cardinality <= _BAR_MAX_CARDINALITY:
        return ChartSuggestion("bar", x=columns[0], y=y)
    return ChartSuggestion("none")
