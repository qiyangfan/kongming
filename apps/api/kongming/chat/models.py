"""问答结果的图表候选推荐纯函数。按列角色和结果形状返回至多 3 个候选:
时间维优先折线图,分类维与度量优先柱状图,可加且非负的数据可补饼图,
双度量可用散点图。

标识列和期间列始终作为维度,不参与度量判断。"""

import datetime as dt
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from functools import partial
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
class MessageMeta:
    """消息质量观测附属字段(可选;流式/同步/报表路径按需携带)。"""

    trace_id: str | None = None  # 轮次质量信号(三表关联键)
    retry_count: int = 0
    resolved_question: str | None = None  # 仅多轮改写实际发生时非空
    prompt_version: str | None = None  # agent prompt 资产指纹
    fallback_type: str | None = None  # 兜底轮分类(闲聊/澄清;仅兜底轮非空)
    stage_trace: list[dict[str, object]] | None = None  # 节点轨迹(流式轮;同步轮 None)


@dataclass(frozen=True)
class Suggestion:
    """对话冷启动建议问题缓存行:租户+数据源唯一,questions 为
    3-5 条中文自然语言问句。"""

    tenant_id: str
    datasource_key: str
    questions: list[str]


@dataclass(frozen=True)
class ChartSuggestion:
    """存量「默认单图」值对象(由首候选展平而来):type ∈ line/bar/pie/scatter/none;
    x/y 为列名(none 时为空)。落库 chart 键与契约 chart 字段形态不变。"""

    type: str
    x: str | None = None
    y: str | None = None


@dataclass(frozen=True)
class ChartCandidate:
    """图表候选:type ∈ line/bar/pie/scatter;y 为值列(多度量多系列),
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
    (无名形态的小值域枚举/序号兜底;阈值刻意收紧)。"""
    ints = [v for v in vals if isinstance(v, int) and not isinstance(v, bool)]
    if not ints or len(ints) != len(vals):
        return False
    distinct = set(ints)
    if not 1 < len(distinct) <= 16:
        return False
    lo, hi = min(ints), max(ints)
    return lo in (0, 1) and hi <= 64 and hi - lo + 1 == len(distinct)


def _profile_columns(columns: list[str], rows: list[list[object]]) -> list[str]:
    """列角色画像(先中先停):temporal(值形态)→ 期间列/标识列
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
    except Exception:  # noqa: BLE001  # 解析异常按不可加处理,标签推导不阻断主流程
        return set()
    else:
        return out


def suggest_charts(
    columns: list[str], rows: list[list[object]], sql: str = ""
) -> list[ChartCandidate]:
    """结果形状 → 有序图表候选(纯函数,同步/流式/回显三路径同源,
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
        return _temporal_candidates(columns, dims, temporal[0], card, y_cols)

    if len(dims) == 1:
        return _single_dim_candidates(columns, rows, dims[0], measures[0], sql, card, y_cols)

    if len(dims) == 2 and len(measures) == 1:
        low, high = sorted(dims, key=card)
        # 分组柱语义闸:x 标签须跨系列重复(真 GROUP BY 形状);小表明细行
        # 标签唯一(每行一个 x 值),分组柱退化成逐行摆柱,零候选
        return (
            [ChartCandidate("bar", x=columns[high], y=y_cols, series=columns[low])]
            if card(low) <= _PIE_MAX_CARDINALITY
            and card(high) <= _BAR_MAX_CARDINALITY
            and card(high) < len(rows)
            else []
        )

    # 无维两度量=散点;两维多度量/≥3 维等:任何单图必失真,表格即答案
    return (
        [ChartCandidate("scatter", x=columns[measures[0]], y=(columns[measures[1]],))]
        if not dims and len(measures) >= 2
        else []
    )


def _temporal_candidates(
    columns: list[str],
    dims: list[int],
    t: int,
    card: Callable[[int], int],
    y_cols: tuple[str, ...],
) -> list[ChartCandidate]:
    """时间维在前:无分类维=单折线;恰一低基数维=多系列折线;其余零候选。"""
    if not dims:
        return [ChartCandidate("line", x=columns[t], y=y_cols)]
    if len(dims) == 1 and card(dims[0]) <= _PIE_MAX_CARDINALITY:
        return [ChartCandidate("line", x=columns[t], y=y_cols, series=columns[dims[0]])]
    return []  # T+多维/高基数维:粒度超单图表达,宁缺毋滥


def _single_dim_candidates(
    columns: list[str],
    rows: list[list[object]],
    di: int,
    measure: int,
    sql: str,
    card: Callable[[int], int],
    y_cols: tuple[str, ...],
) -> list[ChartCandidate]:
    """单分类维:基数达标给柱图;构成语义齐备(低基数/标签唯一/非期间维/
    可加非负)再补饼图。"""
    if card(di) > _BAR_MAX_CARDINALITY:
        return []
    cands = [ChartCandidate("bar", x=columns[di], y=y_cols)]
    if not _pie_eligible(columns, rows, di, measure, sql, card):
        return cands
    cands.append(ChartCandidate("pie", x=columns[di], y=(columns[measure],)))
    return cands


def _pie_eligible(
    columns: list[str],
    rows: list[list[object]],
    di: int,
    measure: int,
    sql: str,
    card: Callable[[int], int],
) -> bool:
    """饼图语义闸:构成整体才成立(标签唯一、非期间维、可加聚合且值非负)。"""
    labels = {str(r[di]) for r in rows if len(r) > di}
    vals = [r[measure] for r in rows if len(r) > measure]
    return (
        card(di) <= _PIE_MAX_CARDINALITY
        and len(labels) == len(rows)  # 标签唯一(GROUP BY 键语义)
        and columns[di].lower() not in _PERIOD_NAMES  # 期间维(年份)非构成整体
        and columns[measure].lower() in _additive_outputs(sql)  # 可加聚合才谈构成
        and all(
            isinstance(v, (int, float, Decimal)) and not isinstance(v, bool) and v >= 0
            for v in vals
        )
    )


# ============ 结果统计键名用户化 ============

_STAT_KIND_CN = {"max": "最高", "min": "最低", "avg": "均值"}


def humanize_stats(
    stats: dict[str, object], columns: list[str], labels: list[str]
) -> dict[str, object]:
    """统计键 → 用户可读形态(纯函数,幂等):rows→「行数」;「{列}_max/_min/_avg」
    在列名唯一命中结果列时→「{列标签} 最高/最低/均值」(列标签单源;标签
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


# ============ conversation-artifacts:产物派生纯函数 ============


class Artifact(TypedDict):
    """对话产物:type ∈ sql/chart/document/report;content 主内容;metadata 辅助。"""

    type: str
    content: str
    metadata: dict[str, object]


def derive_artifacts(
    *,
    sql: str,
    chart: ChartSuggestion,
    summary: str,
    datasource_key: str,
    report: dict[str, object] | None = None,  # 报告轮次(互斥派生)
) -> list[Artifact]:
    """从既有要素确定性派生产物(纯函数,零 LLM):报告轮次→report 卡
    (互斥于问答三卡——报告轮次不叠加派生);否则 sql 非空→sql 卡;
    chart.type≠none→chart 卡;summary 非空→document 卡。
    派生对同步/流式/回显三路径同源(回显缺档时按要素重算,与 chart 同款)。"""
    if report is not None:
        rid = str(report.get("report_id") or "")
        status = str(report.get("status") or "")
        title = str(report.get("title") or "")
        return [
            Artifact(
                type="report",
                # content=标题+终态摘要(partial/failed 如实,不表述为全部完成)
                content=f"{title}({report.get('summary') or status})",
                metadata={
                    "report_id": rid,
                    "version": report.get("version"),
                    "status": status,
                },
            )
        ]
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


# ============ 呈现层列标签:物理名/显示名双轨制 ============

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


_LABEL_TEMPORAL_SUFFIXES = ("月份", "日期", "季度", "月", "天", "年", "周")
_LABEL_COUNT_SUFFIXES = ("笔数", "数量", "个数", "次数", "人数", "条数", "部数", "台数", "家数")
_LABEL_AMOUNT_SUFFIXES = ("金额", "总额", "合计", "总和", "均值")
_LABEL_PHRASE_WINDOW = 12
# 问句短语截断点:连接词/量词/标点都不属于业务名词短语本身
_LABEL_PHRASE_CUT = frozenset(
    "，。；、,:：!?！？()（）[]【】{}<> \t\r\n"
    "的和与及以及按每各所有分别在从到于为是有里超前多少几"
    "高低统计查询列出显示计算求"
)
_LABEL_TRAILING_CONNECTORS = (
    "的和与及以及按每各所有分别在从到于为是有超过多少几最高最低最多最少统计查询列出显示计算求"
)
_LABEL_MEASURE = re.compile(r"(?:多少|几)([位名人家台部件条笔次])")
_LABEL_STOP_NOUNS = frozenset(("一共", "共", "总计", "合计"))
_LABEL_FILTER_TERMS = ("超过",)


def _trim_label_prefix(value: str) -> str:
    """取问句片段中最后一个连接词之后的业务名词部分。"""
    # 先去掉结尾连接词(「客户有」→「客户」),但保留标点作为强边界,
    # 避免「租赁？按」先吃掉问号后把「多少」误当成名词。
    value = value.rstrip(_LABEL_TRAILING_CONNECTORS)
    cut = max((i for i, ch in enumerate(value) if ch in _LABEL_PHRASE_CUT), default=-1)
    # 「超过」是完整过滤词:按单字切会留下「过三个月」;「过去」不是过滤词。
    for term in _LABEL_FILTER_TERMS:
        if (idx := value.rfind(term)) >= 0:
            cut = max(cut, idx + len(term) - 1)
    return value[cut + 1 :].strip().strip("0123456789０１２３４５６７８９.-_/ ")


def _dedupe_label_phrases(items: list[str]) -> list[str]:
    """去重并丢掉被更长短语覆盖的前缀(如「支付月」与「支付月份」并存)。"""
    out: list[str] = []
    for item in items:
        if item and item not in out:
            out.append(item)
    return [
        item for item in out if not any(other != item and other.startswith(item) for other in out)
    ]


def _sort_phrases_by_question(items: list[str], question: str, resolved_question: str) -> list[str]:
    """按短语在问句中的出现顺序排序;同词优先原问句位置。"""
    texts = (question, resolved_question)

    def position(item: str) -> int:
        hits = [text.find(item) for text in texts if text.find(item) >= 0]
        return min(hits) if hits else 10**9

    return sorted(items, key=position)


def _suffix_phrases(text: str, suffixes: Sequence[str]) -> list[str]:
    """按业务后缀从问句取短语;只保留纯中文,避免把数字/英文别名带进标签。"""
    found: list[str] = []
    for suffix in sorted(suffixes, key=len, reverse=True):
        start = 0
        while (idx := text.find(suffix, start)) >= 0:
            start = idx + len(suffix)
            before = _trim_label_prefix(text[max(0, idx - _LABEL_PHRASE_WINDOW) : idx])
            if len(before) < 2 or not _CJK.search(before) or re.search(r"[0-9A-Za-z０-９]", before):
                continue
            found.append(before + suffix)
    return _dedupe_label_phrases(found)


def _leading_cjk(value: str) -> str:
    """取开头连续中文(用于「有多少部不同的影片」的量词后名词)。"""
    match = re.match(r"[\u4e00-\u9fff]{1,8}", value)
    return match.group(0) if match is not None else ""


def _trailing_cjk(value: str) -> str:
    """取结尾连续中文(用于「客户有多少位」的量词前名词)。"""
    match = re.search(r"[\u4e00-\u9fff]{1,8}$", value)
    return match.group(0) if match is not None else ""


def _measure_count_phrases(text: str) -> list[str]:
    """从「多少/几 + 量词」问法推导计数标签,如「客户有多少位」→「客户数量」。"""
    found: list[str] = []
    for match in _LABEL_MEASURE.finditer(text):
        measure = match.group(1)
        tail = re.sub(
            r"^(?:不同(?:的)?|的|个|些|所有|各|一| )+",
            "",
            text[match.end() : match.end() + _LABEL_PHRASE_WINDOW],
        )
        noun = _leading_cjk(tail)
        if not noun:
            window_start = max(0, match.start() - _LABEL_PHRASE_WINDOW)
            before = _trim_label_prefix(text[window_start : match.start()])
            noun = _trailing_cjk(before)
        if not noun or noun in _LABEL_STOP_NOUNS:
            continue
        suffix = "笔数" if measure == "笔" else "次数" if measure == "次" else "数量"
        found.append(noun + suffix)
    return _dedupe_label_phrases(found)


def _question_label_candidates(question: str, resolved_question: str) -> dict[str, list[str]]:
    """从原问句优先、改写句兜底提取时间/计数/金额候选短语。"""
    temporal: list[str] = []
    count: list[str] = []
    amount: list[str] = []
    for text in (question, resolved_question):
        if not text.strip():
            continue
        temporal.extend(_suffix_phrases(text, _LABEL_TEMPORAL_SUFFIXES))
        count.extend(_suffix_phrases(text, _LABEL_COUNT_SUFFIXES))
        count.extend(_measure_count_phrases(text))
        amount.extend(_suffix_phrases(text, _LABEL_AMOUNT_SUFFIXES))
    return {
        "temporal": _sort_phrases_by_question(
            _dedupe_label_phrases(temporal), question, resolved_question
        ),
        "count": _sort_phrases_by_question(
            _dedupe_label_phrases(count), question, resolved_question
        ),
        "amount": _sort_phrases_by_question(
            _dedupe_label_phrases(amount), question, resolved_question
        ),
    }


def _projection_kind(proj: exp.Expr) -> str:
    """SQL 投影结构 → 语义类别;标签取词只看结构,不按列名猜业务。"""
    # 比率/占比投影先于 count 判定:分子分母里的 Count 不把整列变成计数
    if proj.find(exp.Div):
        return "ratio"
    if proj.find(exp.Count):
        return "count"
    if proj.find(exp.Sum) or proj.find(exp.Avg):
        return "amount"
    if proj.find(exp.TimestampTrunc) or proj.find(exp.DateTrunc) or proj.find(exp.Date):
        return "temporal"
    return ""


def _base_comment_for_projection(
    proj: exp.Expr,
    alias_map: dict[str, str],
    involved: set[str],
    table_comments: dict[str, dict[str, str]],
) -> str:
    """从投影里出现的基础列取数据源注释,供计算列在无问句短语时兜底。"""
    for col in proj.find_all(exp.Column):
        name = col.name.lower()
        if col.table:
            tbl = alias_map.get(col.table.lower())
            if tbl:
                cleaned = _clean_comment(table_comments.get(tbl, {}).get(name, ""))
                if cleaned:
                    return cleaned
            continue
        candidates = {
            cleaned
            for tbl in involved
            if (cleaned := _clean_comment(table_comments.get(tbl, {}).get(name, "")))
        }
        if len(candidates) == 1:
            return candidates.pop()
    return ""


def _computed_labels_by_projection(
    projections: Sequence[exp.Expr],
    question: str,
    resolved_question: str,
) -> dict[int, str]:
    """同类候选与投影一一对应;数量不一致不猜,留给注释/物理名兜底。"""
    candidates = _question_label_candidates(question, resolved_question)
    positions: dict[str, list[int]] = {}
    for i, proj in enumerate(projections):
        kind = _projection_kind(proj)
        if kind in candidates:
            positions.setdefault(kind, []).append(i)
    labels: dict[int, str] = {}
    for kind, indexes in positions.items():
        values = candidates[kind]
        if len(indexes) == 1 and values:
            labels[indexes[0]] = values[0]
        elif len(values) == len(indexes):
            labels.update(zip(indexes, values, strict=True))
    return labels


def _temporal_label(proj: exp.Expr, question: str, resolved_question: str) -> str:
    """时间投影无候选中标时的确定性回退。"""
    day_words = ("每天", "每日", "按天", "日期")
    if any(word in question or word in resolved_question for word in day_words):
        return "日期"
    if proj.find(exp.TimestampTrunc) and ("按月" in question or "按月" in resolved_question):
        return "月份"
    return "日期"


def _computed_label(
    *,
    kind: str,
    proj: exp.Expr,
    question: str,
    resolved_question: str,
    alias_map: dict[str, str],
    involved: set[str],
    table_comments: dict[str, dict[str, str]],
) -> str:
    """计算列无候选标签时的确定性兜底(不按列名猜测)。"""
    if not question.strip() and not resolved_question.strip():
        return ""
    if kind == "amount":
        return _base_comment_for_projection(proj, alias_map, involved, table_comments)
    if kind == "temporal":
        return _temporal_label(proj, question, resolved_question)
    return ""


def _label_for_projection(
    proj: exp.Expr,
    out_col: str,
    alias_map: dict[str, str],
    involved: set[str],
    table_comments: dict[str, dict[str, str]],
    *,
    question: str = "",
    resolved_question: str = "",
    computed_label: str = "",
) -> str:
    """单个投影表达式 → 标签: qualified 列(c.col)按别名定表精确溯源;
    裸列仅在 SQL 涉及表内唯一出现才用(涉及表之外的同名注释不构成候选;
    涉及表内语义冲突如 actor/customer 的 first_name → 回退);
    计算列按聚合/时间结构和问句短语生成业务标签,不直接暴露 SQL 别名。"""
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
    kind = _projection_kind(proj)
    if kind:
        if computed_label:
            return computed_label
        label = _computed_label(
            kind=kind,
            proj=proj,
            question=question,
            resolved_question=resolved_question,
            alias_map=alias_map,
            involved=involved,
            table_comments=table_comments,
        )
        if label:
            return label
    return out_col


def _projection_inputs(
    sql: str, columns: list[str]
) -> tuple[dict[str, str], set[str], list[exp.Expr]] | None:
    """解析 SQL 并对齐投影;解析失败/星号投影返回 None(调用方整体回退)。"""
    try:
        stmt = next(
            (s for s in sqlglot.parse(sql, read="postgres") if isinstance(s, exp.Select)),
            None,
        )
        if stmt is None:
            return None
        alias_map: dict[str, str] = {}
        for table in stmt.find_all(exp.Table):
            name = table.name.lower()
            if table.alias:
                alias_map[table.alias.lower()] = name
            alias_map.setdefault(name, name)
        projections = stmt.selects
        if len(projections) != len(columns):
            # 星号投影一对多:按位对位会把后续投影标签贴到星号列上,整体回退
            return None
        return alias_map, set(alias_map.values()), projections
    except Exception:  # noqa: BLE001  # 解析任何异常整体回退物理名(标签永不阻断)
        return None


def _assign_projection_labels(
    labels: list[str],
    columns: list[str],
    projections: list[exp.Expr],
    alias_map: dict[str, str],
    involved: set[str],
    table_comments: dict[str, dict[str, str]],
    *,
    question: str,
    resolved_question: str,
) -> None:
    """按投影逐列写标签,并对重复计算标签回退物理名。"""
    computed_labels = _computed_labels_by_projection(projections, question, resolved_question)
    for i, out_col in enumerate(columns):
        labels[i] = _label_for_projection(
            projections[i],
            out_col,
            alias_map,
            involved,
            table_comments,
            question=question,
            resolved_question=resolved_question,
            computed_label=computed_labels.get(i, ""),
        )
    seen: dict[str, int] = {}
    for i, proj in enumerate(projections):
        if not _projection_kind(proj) or labels[i] == columns[i]:
            continue
        previous = seen.get(labels[i])
        if previous is None:
            seen[labels[i]] = i
            continue
        labels[previous] = columns[previous]
        labels[i] = columns[i]


def resolve_column_labels(
    columns: list[str],
    table_comments: dict[str, dict[str, str]],
    sql: str = "",
    *,
    question: str = "",
    resolved_question: str = "",
) -> list[str]:
    """输出列 → 呈现标签(纯函数;呈现层双轨制,SQL 恒持物理名)。

    sql 可解析时按投影逐列溯源;纯列取数据源注释,计算列按聚合/时间结构
    从用户原句取业务短语(改写句仅兜底)。无问句/无可解析 SQL 时回退物理名。
    与 columns 等长对齐,前端/导出按位取用;语义解释由洞察文本兜底。
    """
    labels = list(columns)
    if not columns or not sql.strip():
        return labels
    if not table_comments and not (question.strip() or resolved_question.strip()):
        return labels
    inputs = _projection_inputs(sql, columns)
    if inputs is None:
        return labels
    alias_map, involved, projections = inputs
    _assign_projection_labels(
        labels,
        columns,
        projections,
        alias_map,
        involved,
        table_comments,
        question=question,
        resolved_question=resolved_question,
    )
    return labels


def _label_replacement(_match: re.Match[str], label: str) -> str:
    """正则替换回调:返回目标标签。"""
    return label


def relabel_column_mentions(text: str, columns: Sequence[str], labels: Sequence[str]) -> str:
    """把总结/洞察中的物理列名替换成业务标签;整词匹配,长名优先防截断。"""
    if not text:
        return text
    pairs = [
        (column, label)
        for column, label in zip(columns, labels, strict=False)
        if column and label and column != label
    ]
    for column, label in sorted(pairs, key=lambda pair: len(pair[0]), reverse=True):
        text = re.sub(
            rf"(?<![A-Za-z0-9_]){re.escape(column)}(?![A-Za-z0-9_])",
            partial(_label_replacement, label=label),
            text,
        )
    return text
