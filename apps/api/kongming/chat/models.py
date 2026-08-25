"""chat 领域模型(零框架,宪法 Article II):图表配置建议纯函数。

推断规则(chat spec「图表可视化」):
- 首列日期/时间 → line(时序趋势);
- 存在两个数值列(任意位置)→ scatter(x/y=前两个数值列;
  011 吸收列序解耦——prompt 会给明细附标识列,数值列未必在首列);
- 首列低基数分类(≤8 类)+ 有数值列 → pie(占比);
- 首列分类 + 有数值列(基数 ≤50) → bar;
- 其余(明细/无数值列/首列为唯一数值列) → none。
基数 >50 推定为明细行,不强加图表。
"""

import datetime as dt
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import TypedDict

_PIE_MAX_CARDINALITY = 8  # 低基数分类上界(chat spec)
_BAR_MAX_CARDINALITY = 50  # 超此基数推定明细,不配图

_DATE_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}")


@dataclass(frozen=True)
class ChartSuggestion:
    """type: line / bar / pie / scatter / none;x/y 为列名(none 时为空)。"""

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
    # 全列扫描(含首列):scatter 双数值判定不依赖列序(标识列可在任意位置)
    numeric_cols = [
        i
        for i in range(len(columns))
        if any(_is_numeric(r[i]) for r in rows if len(r) > i)
    ]
    if not numeric_cols:
        return ChartSuggestion("none")
    if all(_is_temporal(v) for v in first_vals):
        # temporal 首列自身非数值,y 必在其后
        return ChartSuggestion("line", x=columns[0], y=columns[numeric_cols[0]])
    if len(numeric_cols) >= 2:
        return ChartSuggestion(
            "scatter", x=columns[numeric_cols[0]], y=columns[numeric_cols[1]]
        )
    if 0 in numeric_cols:
        return ChartSuggestion("none")  # 仅首列数值:单个数值列不成对,不猜
    y = columns[numeric_cols[0]]
    cardinality = len({str(v) for v in first_vals})
    if cardinality <= _PIE_MAX_CARDINALITY:
        return ChartSuggestion("pie", x=columns[0], y=y)
    if cardinality <= _BAR_MAX_CARDINALITY:
        return ChartSuggestion("bar", x=columns[0], y=y)
    return ChartSuggestion("none")


# ============ 026 conversation-artifacts:产物派生纯函数 ============


class Artifact(TypedDict):
    """对话产物(026):type ∈ sql/chart/document;content 主内容;metadata 辅助。"""

    type: str
    content: str
    metadata: dict[str, object]


def derive_artifacts(
    *, sql: str, chart: ChartSuggestion, summary: str, datasource_key: str
) -> list[Artifact]:
    """从既有要素确定性派生产物(纯函数,零 LLM):sql 非空→sql 卡;
    chart.type≠none→chart 卡;summary 非空→document 卡。
    派生对同步/流式/回显三路径同源(回显缺档时按要素重算,与 chart 同款)。"""
    out: list[Artifact] = []
    if sql.strip():
        out.append(
            Artifact(
                type="sql",
                content=sql,
                metadata={"datasource_key": datasource_key},
            )
        )
    if chart.type != "none":
        out.append(
            Artifact(
                type="chart",
                content="",
                metadata={
                    "chart": {"type": chart.type, "x": chart.x, "y": chart.y},
                },
            )
        )
    if summary.strip():
        out.append(Artifact(type="document", content=summary, metadata={}))
    return out
