"""消息回显映射:落库行 → SessionMessage(读档优先,缺档确定性重算)。"""

import json
from typing import cast

from kongming.chat.models import (
    ChartCandidate,
    ChartSuggestion,
    derive_artifacts,
    humanize_stats,
    suggest_charts,
)
from kongming.chat.results import MessageResult, SessionMessage


def row_to_message(row: dict[str, object]) -> SessionMessage:
    """行 → 回显项;result_json 读档优先,存量(仅单图/形态)缺失候选时
    以 suggest_charts 纯函数确定性重算补全(落库输入=原始输入,重算=原始输出)。"""
    role = str(row["role"])
    result = _row_result(row) if role == "ASSISTANT" else None
    rated = role == "ASSISTANT"
    question = row.get("question")
    summary = row.get("summary")
    sql_text = row.get("sql_text")
    return SessionMessage(
        id=str(row["id"]),
        role=role,
        attachments=_row_attachments(row),
        question=None if question is None else str(question),
        summary=None if summary is None else str(summary),
        sql=None if sql_text is None else str(sql_text),
        status=str(row["status"]),
        error=result.error if result is not None else None,
        error_detail=result.error_detail if result is not None else None,
        result=result,
        rating=_optional_str(row.get("rating")) if rated else None,
        corrected_sql=_optional_str(row.get("corrected_sql")) if rated else None,
        feedback_comment=_optional_str(row.get("feedback_comment")) if rated else None,
    )


def _optional_str(value: object) -> str | None:
    """可选列值 → 字符串;NULL 保持 None(区分未反馈与已反馈的空串)。"""
    return None if value is None else str(value)


def _row_attachments(row: dict[str, object]) -> list[dict[str, object]]:
    """附件摘要列 → 回显列表;非列表档按空处理,非 dict 元素跳过。"""
    att_raw = row.get("attachments")
    if not isinstance(att_raw, list):
        return []
    return [
        dict(cast("dict[str, object]", a))
        for a in cast("list[object]", att_raw)
        if isinstance(a, dict)
    ]


def _row_result(row: dict[str, object]) -> MessageResult | None:
    """assistant 行的 result_json 档 → 结构化要素;缺档/非法档返回 None。"""
    rj = row.get("result_json")
    if not rj:
        return None
    try:
        parsed = json.loads(str(rj))
    except json.JSONDecodeError:
        parsed = None
    # 合法但非 dict 的 JSON(null/数组等)一律按空档处理,不炸回显
    # (json.loads 的 Any 经 isinstance 收窄键型仍 Unknown,真分支 cast 一次)
    d = cast("dict[str, object]", parsed) if isinstance(parsed, dict) else {}
    return _message_result(row, d)


def _message_result(row: dict[str, object], d: dict[str, object]) -> MessageResult:
    """result_json 档 → 结构化要素;候选/产物缺档时按要素重算。"""
    cols, rws = _read_grid(d)
    # labels 先行读出供统计键归一;存量英文键幂等升级
    col_labels = _read_column_labels(d)
    sql_text = str(row.get("sql_text") or "")
    chart_candidates, chart = _stored_charts(d, cols, rws, sql_text)
    return MessageResult(
        columns=cols,
        rows=rws,
        insights=_read_insights(d),
        stats=_read_stats(d, cols, col_labels),
        chart=chart,
        chart_candidates=chart_candidates,
        error=_optional_str(d.get("error")),  # 读档:原始技术错误(存量档缺失→None)
        error_detail=_optional_str(d.get("error_detail")),
        artifacts=_read_artifacts(row, d, sql_text, chart),
        column_labels=col_labels,  # 读档;存量档缺失→空(消费方回退 columns)
        metric_hits=_read_metric_hits(d),  # 读档;存量档缺失→空,SHALL NOT 反查指标表重算
        sources=_read_sources(d),  # 读档;存量档缺失→空;名称为提问时点快照
    )


def _read_grid(d: dict[str, object]) -> tuple[list[str], list[list[object]]]:
    """列名与结果行读档;行内非序列元素跳过(畸形 JSON 深防御,防手改库)。"""
    cols_raw = d.get("columns")
    rows_raw = d.get("rows")
    cols = [str(c) for c in cast("list[object]", cols_raw)] if isinstance(cols_raw, list) else []
    rws = (
        [
            list(cast("list[object]", r))
            for r in cast("list[object]", rows_raw)
            if isinstance(r, (list, tuple))
        ]
        if isinstance(rows_raw, list)
        else []
    )
    return cols, rws


def _read_column_labels(d: dict[str, object]) -> list[str]:
    """呈现层列标签读档;存量档缺失→空列表(消费方回退 columns)。"""
    lb_raw = d.get("column_labels")
    return [str(x) for x in cast("list[object]", lb_raw)] if isinstance(lb_raw, list) else []


def _read_stats(d: dict[str, object], cols: list[str], col_labels: list[str]) -> dict[str, object]:
    """统计读档并按列标签归一;非 dict 档按空处理。"""
    stats_raw = d.get("stats")
    if not isinstance(stats_raw, dict):
        return {}
    return humanize_stats(dict(cast("dict[str, object]", stats_raw)), cols, col_labels)


def _read_insights(d: dict[str, object]) -> list[dict[str, str]]:
    """洞察读档;非列表档与畸形元素跳过。"""
    ins_raw = d.get("insights")
    if not isinstance(ins_raw, list):
        return []
    return [
        dict(cast("dict[str, str]", i))
        for i in cast("list[object]", ins_raw)
        if isinstance(i, dict)
    ]


def _read_artifacts(
    row: dict[str, object], d: dict[str, object], sql_text: str, chart: ChartSuggestion
) -> list[dict[str, object]]:
    """产物读档优先;缺档(前存量)按 sql/图表/摘要确定性重算。"""
    art_raw = d.get("artifacts")
    if isinstance(art_raw, list):
        return [
            dict(cast("dict[str, object]", a))
            for a in cast("list[object]", art_raw)
            if isinstance(a, dict)
        ]
    return [
        dict(a)
        for a in derive_artifacts(
            sql=sql_text,
            chart=chart,
            summary=str(row.get("summary") or ""),
            datasource_key=str(row.get("datasource_key") or "default"),
        )
    ]


def _stored_charts(
    d: dict[str, object], cols: list[str], rws: list[list[object]], sql_text: str
) -> tuple[list[ChartCandidate], ChartSuggestion]:
    """候选读档优先;存量档(仅单图/形态)回落解析,缺档按形状重算。"""
    chart_candidates = _read_candidates(d)
    if chart_candidates:
        return chart_candidates, chart_candidates[0].as_default()
    chart_raw = d.get("chart")
    # isinstance 守卫后 cast 收窄键型
    chart_d = cast("dict[str, object]", chart_raw) if isinstance(chart_raw, dict) else None
    if chart_d is not None and chart_d.get("type"):
        # 存量单图档:按档渲染单图,候选置空(前端回落单图绑定)
        return [], ChartSuggestion(
            type=str(chart_d["type"]),
            x=_optional_str(chart_d.get("x")),
            y=_optional_str(chart_d.get("y")),
        )
    # 存量兼容:只存 chartType(无 x/y)/缺档 → 纯函数重算
    recomputed = suggest_charts(cols, rws, sql=sql_text)
    return recomputed, (recomputed[0].as_default() if recomputed else ChartSuggestion("none"))


def _read_candidates(d: dict[str, object]) -> list[ChartCandidate]:
    """图表候选读档;缺 type 的畸形候选跳过。"""
    cands_raw = d.get("chart_candidates")
    if not isinstance(cands_raw, list):
        return []
    out: list[ChartCandidate] = []
    for c in cast("list[object]", cands_raw):
        if not isinstance(c, dict):
            continue
        parsed = _candidate_of(cast("dict[str, object]", c))
        if parsed is not None:
            out.append(parsed)
    return out


def _candidate_of(cd: dict[str, object]) -> ChartCandidate | None:
    """单条候选档 → ChartCandidate;无字符串 type 返回 None。"""
    type_raw = cd.get("type")
    if not isinstance(type_raw, str):
        return None
    y_raw_list = cd.get("y")
    return ChartCandidate(
        type=type_raw,
        x=_optional_str(cd.get("x")),
        y=(
            tuple(str(v) for v in cast("list[object]", y_raw_list))
            if isinstance(y_raw_list, list)
            else ()
        ),
        series=_optional_str(cd.get("series")),
    )


def _read_metric_hits(d: dict[str, object]) -> list[dict[str, str | None]]:
    """语义层命中读档(键集固定);非列表档按空处理。"""
    mh_raw = d.get("metric_hits")
    if not isinstance(mh_raw, list):
        return []
    return [
        {
            "metric_id": str(hd.get("metric_id") or ""),
            "metric_name": str(hd.get("metric_name") or ""),
            "sql_template": str(hd.get("sql_template") or ""),
            "updated_at": (str(hd["updated_at"]) if hd.get("updated_at") is not None else None),
        }
        for h in cast("list[object]", mh_raw)
        if isinstance(h, dict)
        for hd in [cast("dict[str, object]", h)]
    ]


def _read_sources(d: dict[str, object]) -> list[dict[str, str]]:
    """文档道来源读档(键集固定);非列表档按空处理。"""
    src_raw = d.get("sources")
    if not isinstance(src_raw, list):
        return []
    return [
        {
            "citation_key": str(sd.get("citation_key") or ""),
            "source_ref": str(sd.get("source_ref") or ""),
            "doc_name": str(sd.get("doc_name") or ""),
        }
        for si in cast("list[object]", src_raw)
        if isinstance(si, dict)
        for sd in [cast("dict[str, object]", si)]
    ]
