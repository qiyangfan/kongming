"""chat 领域模型(零框架,宪法 Article II):图表候选推荐纯函数(049)。

列角色画像(temporal/dimension/measure)→ 结果形状 → 有序图表候选(0~3 个,
每类型至多一个;候选自带 x/y/series 绑定),参照 Tableau「Show Me」思路:
- temporal 维(值形态日期/期间列名)→ line(时序;附低基数分类维则多系列);
- 单分类维 + 度量 → bar 为主;pie 过「可加聚合 + 标签唯一 + 值非负」闸后为辅;
- 两分类维 + 单度量 → 分组柱状图(bar + series=低基数维);
- 双度量无维 → scatter;明细/不适配形状 → 零候选(表格即答案)。
标识列(store_id 类)与期间列(year 类)恒为维度、丧失度量资格——散点 x 轴按
值处理离散 id 产生的碎刻度从根因消除,而非对个例打补丁。
"""

import datetime as dt
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import TypedDict

import sqlglot
from sqlglot import exp

_PIE_MAX_CARDINALITY = 8  # pie/低基数系列维上界(chat spec)
_BAR_MAX_CARDINALITY = 50  # 超此基数推定明细,不配图
_MAX_Y_COLUMNS = 3  # 多度量 y 截断(图例可读性)

_DATE_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}")
# 标识列名信号:num 后缀刻意不入——中文库 order_num(订单数)类计数量是度量
_ID_NAME = re.compile(r"(?:^|_)(?:id|no|code)$|^id_")
_PERIOD_NAMES = {"year", "quarter", "month", "week", "day"}


@dataclass(frozen=True)
class Suggestion:
    """对话冷启动建议问题缓存行(039):租户+数据源唯一,questions 为
    3-5 条中文自然语言问句。"""

    tenant_id: str
    datasource_key: str
    questions: list[str]


@dataclass(frozen=True)
class ChartSuggestion:
    """存量「默认单图」值对象(049 起由首候选展平):type ∈ line/bar/pie/scatter/none;
    x/y 为列名(none 时为空)。落库 chart 键与契约 chart 字段形态不变。"""

    type: str
    x: str | None = None
    y: str | None = None


@dataclass(frozen=True)
class ChartCandidate:
    """图表候选(049):type ∈ line/bar/pie/scatter;y 为值列(多度量多系列),
    series 为系列维(分组柱/时序多系列),无则 None。"""

    type: str
    x: str | None = None
    y: tuple[str, ...] = ()
    series: str | None = None

    def as_default(self) -> ChartSuggestion:
        """展平为存量「默认单图」值对象(y 取首列;落库/契约 chart 形态不变)。"""
        return ChartSuggestion(self.type, x=self.x, y=self.y[0] if self.y else None)


def _is_temporal(v: object) -> bool:
    if isinstance(v, dt.date | dt.datetime):
        return True
    return isinstance(v, str) and bool(_DATE_PREFIX.match(v))


def _is_numeric(v: object) -> bool:
    # bool 是 int 子类,显式排除(PG BOOLEAN 经驱动成 True/False)
    return isinstance(v, (int, float, Decimal)) and not isinstance(v, bool)


_ROLE_TEMPORAL = "temporal"
_ROLE_DIMENSION = "dimension"
_ROLE_MEASURE = "measure"


def _is_dense_sequence(vals: list[object]) -> bool:
    """整数稠密序列(min∈{0,1}、连续、distinct≤16、max≤64)→ 维度信号
    (无名形态的小值域枚举/序号兜底;阈值刻意收紧,design D1)。"""
    ints = [v for v in vals if isinstance(v, int) and not isinstance(v, bool)]
    if not ints or len(ints) != len(vals):
        return False
    distinct = set(ints)
    if not 1 < len(distinct) <= 16:
        return False
    lo, hi = min(ints), max(ints)
    return lo in (0, 1) and hi <= 64 and hi - lo + 1 == len(distinct)


def _profile_columns(columns: list[str], rows: list[list[object]]) -> list[str]:
    """列角色画像(design D1,先中先停):temporal(值形态)→ 期间列/标识列
    (名称信号)→ measure(其余非 bool 数值)→ dimension(分类)。
    整数稠密序列兜底仅当降维后仍剩至少一个度量才落地——防小值域计数列被
    降维后度量集归零,把本可配图的结果打成零候选。"""
    roles: list[str] = []
    for i, name in enumerate(columns):
        vals = [r[i] for r in rows if len(r) > i]
        lowered = name.lower()
        if vals and all(_is_temporal(v) for v in vals):
            roles.append(_ROLE_TEMPORAL)
        elif lowered in _PERIOD_NAMES or _ID_NAME.search(lowered):
            roles.append(_ROLE_DIMENSION)
        elif any(_is_numeric(v) for v in vals):
            roles.append(_ROLE_MEASURE)
        else:
            roles.append(_ROLE_DIMENSION)
    dense = [
        i
        for i, role in enumerate(roles)
        if role == _ROLE_MEASURE and _is_dense_sequence([r[i] for r in rows if len(r) > i])
    ]
    total_measures = sum(1 for r in roles if r == _ROLE_MEASURE)
    if dense and len(dense) < total_measures:
        for i in dense:
            roles[i] = _ROLE_DIMENSION
    return roles


def _additive_outputs(sql: str) -> set[str]:
    """可加聚合(COUNT/SUM)的输出列名(未别名取 PG 默认列名);空 SQL/解析
    失败 → 空集(fail-closed,pie 是锦上添花,排除比误推更符合「宁缺毋滥」)。"""
    if not sql.strip():
        return set()
    try:
        stmt = next(
            (s for s in sqlglot.parse(sql, read="postgres") if isinstance(s, exp.Select)),
            None,
        )
        if stmt is None:
            return set()
        out: set[str] = set()
        for p in stmt.selects:
            inner = p.this if isinstance(p, exp.Alias) else p
            if isinstance(inner, exp.Count):
                out.add((p.alias or "count").lower())
            elif isinstance(inner, exp.Sum):
                out.add((p.alias or "sum").lower())
        return out
    except Exception:  # noqa: BLE001  # 任何解析异常按不可加处理(标签永不阻断同款)
        return set()


def suggest_charts(
    columns: list[str], rows: list[list[object]], sql: str = ""
) -> list[ChartCandidate]:
    """结果形状 → 有序图表候选(design D3;纯函数,同步/流式/回显三路径同源,
    回显缺档按同输入重算)。维基数=行集内 distinct 值数;零候选=不配图。"""
    if len(columns) < 2 or not rows:
        return []

    def card(i: int) -> int:
        return len({str(r[i]) for r in rows if len(r) > i})

    roles = _profile_columns(columns, rows)
    temporal = [i for i, r in enumerate(roles) if r == _ROLE_TEMPORAL]
    dims = [i for i, r in enumerate(roles) if r == _ROLE_DIMENSION]
    measures = [i for i, r in enumerate(roles) if r == _ROLE_MEASURE]
    if not measures:
        return []
    y_cols = tuple(columns[i] for i in measures[:_MAX_Y_COLUMNS])

    if temporal:
        t = temporal[0]
        if not dims:
            return [ChartCandidate("line", x=columns[t], y=y_cols)]
        if len(dims) == 1 and card(dims[0]) <= _PIE_MAX_CARDINALITY:
            return [ChartCandidate("line", x=columns[t], y=y_cols, series=columns[dims[0]])]
        return []  # T+多维/高基数维:粒度超单图表达,宁缺毋滥

    if len(dims) == 1:
        di = dims[0]
        if card(di) > _BAR_MAX_CARDINALITY:
            return []
        cands = [ChartCandidate("bar", x=columns[di], y=y_cols)]
        first = columns[measures[0]]
        labels = {str(r[di]) for r in rows if len(r) > di}
        vals = [r[measures[0]] for r in rows if len(r) > measures[0]]
        if (
            card(di) <= _PIE_MAX_CARDINALITY
            and len(labels) == len(rows)  # 标签唯一(GROUP BY 键语义)
            and columns[di].lower() not in _PERIOD_NAMES  # 期间维(年份)非构成整体
            and first.lower() in _additive_outputs(sql)  # 可加聚合才谈构成
            and all(
                isinstance(v, (int, float, Decimal)) and not isinstance(v, bool) and v >= 0
                for v in vals
            )
        ):
            cands.append(ChartCandidate("pie", x=columns[di], y=(first,)))
        return cands

    if len(dims) == 2 and len(measures) == 1:
        low, high = sorted(dims, key=card)
        # 分组柱语义闸:x 标签须跨系列重复(真 GROUP BY 形状);小表明细行
        # 标签唯一(每行一个 x 值),分组柱退化成逐行摆柱,零候选
        if (
            card(low) <= _PIE_MAX_CARDINALITY
            and card(high) <= _BAR_MAX_CARDINALITY
            and card(high) < len(rows)
        ):
            return [ChartCandidate("bar", x=columns[high], y=y_cols, series=columns[low])]
        return []

    if not dims and len(measures) >= 2:
        return [ChartCandidate("scatter", x=columns[measures[0]], y=(columns[measures[1]],))]
    return []  # 两维多度量/≥3 维等:任何单图必失真,表格即答案


# ============ 050 结果统计键名用户化 ============

_STAT_KIND_CN = {"max": "最高", "min": "最低", "avg": "均值"}


def humanize_stats(
    stats: dict[str, object], columns: list[str], labels: list[str]
) -> dict[str, object]:
    """统计键 → 用户可读形态(纯函数,幂等):rows→「行数」;「{列}_max/_min/_avg」
    在列名唯一命中结果列时→「{列标签} 最高/最低/均值」(041 列标签单源;标签
    缺失回退物理列名);歧义/未命中保留原键 fail-open。已中文化键不匹配
    「列_后缀」形态,二次应用原样保留(存量档读档归一路径依赖此幂等性)。"""
    out: dict[str, object] = {}
    lowered = [c.lower() for c in columns]
    for k, v in stats.items():
        if k == "rows":
            out["行数"] = v
            continue
        mapped = False
        for suffix, cn in _STAT_KIND_CN.items():
            mark = f"_{suffix}"
            if not k.endswith(mark):
                continue
            col = k[: -len(mark)].lower()
            hits = [i for i, c in enumerate(lowered) if c == col]
            if len(hits) == 1:
                i = hits[0]
                label = labels[i] if i < len(labels) and labels[i] else columns[i]
                out[f"{label} {cn}"] = v
                mapped = True
            break  # 一键至多匹配一种后缀,未命中即原样保留
        if not mapped:
            out[k] = v
    return out


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
            (s for s in sqlglot.parse(sql, read="postgres") if isinstance(s, exp.Select)),
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
