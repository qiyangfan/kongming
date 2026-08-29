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

import sqlglot
from sqlglot import exp

_PIE_MAX_CARDINALITY = 8  # 低基数分类上界(chat spec)
_BAR_MAX_CARDINALITY = 50  # 超此基数推定明细,不配图

_DATE_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}")


@dataclass(frozen=True)
class Suggestion:
    """对话冷启动建议问题缓存行(039):租户+数据源唯一,questions 为
    3-5 条中文自然语言问句。"""

    tenant_id: str
    datasource_key: str
    questions: list[str]


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


# ============ 041 呈现层列标签:物理名/显示名双轨制 ============

_CJK = re.compile(r"[\u4e00-\u9fff]")


def _strip_type_prefix(comment: str) -> str:
    """剥列注释的 PG 类型前缀(fetch-metadata 生成形态「类型 中文注释」,
    类型段供 LLM 生成用):取首个 CJK 字符起的部分;无 CJK 返回原文。"""
    m = _CJK.search(comment)
    return comment[m.start() :] if m else comment


def _clean_comment(comment: str) -> str:
    """注释 → 可呈现标签:剥类型前缀后须仍含中文(业务注释),否则视为无效
    (防「integer」这类无中文注释的半剥产物混入用户界面)——返回空串即无效。"""
    stripped = _strip_type_prefix(comment.strip()).strip()
    return stripped if stripped and _CJK.search(stripped) else ""


def _label_for_projection(
    proj: exp.Expr,
    out_col: str,
    alias_map: dict[str, str],
    involved: set[str],
    table_comments: dict[str, dict[str, str]],
) -> str:
    """单个投影表达式 → 标签: qualified 列(c.col)按别名定表精确溯源;
    裸列仅在 SQL 涉及表内唯一出现才用(涉及表之外的同名注释不构成候选;
    涉及表内语义冲突如 actor/customer 的 first_name → 回退);
    表达式列(聚合/计算)回退——宁缺毋错,不猜标签。"""
    col = proj.this if isinstance(proj, exp.Alias) and isinstance(proj.this, exp.Column) else proj
    if isinstance(col, exp.Column):
        name = col.name.lower()
        if col.table:
            tbl = alias_map.get(col.table.lower())
            if tbl:
                return _clean_comment(table_comments.get(tbl, {}).get(name, "")) or out_col
        candidates = {
            cleaned
            for tbl in involved
            if (cleaned := _clean_comment(table_comments.get(tbl, {}).get(name, "")))
        }
        return candidates.pop() if len(candidates) == 1 else out_col
    return out_col


def resolve_column_labels(
    columns: list[str], table_comments: dict[str, dict[str, str]], sql: str = ""
) -> list[str]:
    """输出列 → 呈现标签(纯函数;呈现层双轨制,SQL 恒持物理名)。

    sql 可解析时按投影逐列溯源(qualified 别名定表 / 裸列唯一命中),
    投影顺序与结果列一一对应;不可解析/无注释表 → 整列回退物理名(fail-open)。
    与 columns 等长对齐,前端/导出按位取用;语义解释由洞察文本兜底。
    """
    labels = list(columns)
    if not columns or not table_comments or not sql.strip():
        return labels
    try:
        stmt = next(
            (
                s
                for s in sqlglot.parse(sql, read="postgres")
                if isinstance(s, exp.Select)
            ),
            None,
        )
        if stmt is None:
            return labels
        alias_map: dict[str, str] = {}
        for t in stmt.find_all(exp.Table):
            tbl = t.name.lower()
            if t.alias:
                alias_map[t.alias.lower()] = tbl
            alias_map.setdefault(tbl, tbl)
        involved = set(alias_map.values())
        projections = stmt.selects
        if len(projections) != len(columns):
            # 星号投影(SELECT * / t.*)一对多:AST 投影数≠结果列数,按位对位
            # 必错标(SELECT *, extra 会把 extra 的标签贴到星号展开列上)——
            # 宁缺毋错,整体回退物理名
            return labels
        for i, out_col in enumerate(columns):
            labels[i] = _label_for_projection(
                projections[i], out_col, alias_map, involved, table_comments
            )
    except Exception:  # noqa: BLE001  # 解析任何异常整体回退物理名(标签永不阻断)
        return list(columns)
    return labels
