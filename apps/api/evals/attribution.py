"""评估失败归因分类(096,eval spec「失败归因分类」):确定性一级分类纯函数。

判据只用评估器已有的确定性信号(SQL 词边界正则、既有抽表能力、链路
verdict、success 信号),SHALL NOT 调用 LLM——不可复现且自身会错;
不引入新依赖(抽表复用 kongming.feedback.models,与 evals/conftest.gen_tables
同源)。分类是纯函数:零外部调用、零 IO,由常规测试门禁单测守护
(tests/test_eval_attribution.py),SHALL NOT 进任何运行时代码路径。

一级分类与判定序(先匹配先归类,一题一类;verdict 终态与 EXEC 判据在
真实数据中互斥——BLOCKED/NO_DATA 题的 success 恒为 false,verdict 携带时
终态先行,否则 EXEC 兜底,保证 REJECT/ABSTAIN 不成死类):

1. NEG_GUARD  负例/拒绝守护失效(expect_reject/expect_no_data 用例失败,
   不再按正例判据细分);
2. REJECT     生成 SQL 被防火墙拦截终止(verdict=BLOCKED);
3. ABSTAIN    误弃答(正例题链路返回 NO_DATA);
4. EXEC       生成 SQL 执行失败(verdict=ERROR/链路异常)或无生成 SQL
   (含 selfheal 耗尽)——success=false 且非上述终态、或 success=true
   而 SQL 为空;
5. SELECT     选表缺失,附层拆分信号:expected_tables 未被 gen 覆盖
   (=召回层,表没进提示词)或 gold 表集未被覆盖(=生成层,表在
   提示词里没用上);
6. JOIN       关联缺失(gen 表集已覆盖 gold 表集,但 JOIN 数少于 gold);
7. METRIC     口径差异(表与 JOIN 数一致,两侧聚合函数集合不一致);
8. FILTER     谓词/时间窗差异(聚合一致,WHERE 过滤面不一致);
9. OTHER      未归因(必须显式报告数量与题目 id,占比 ≥20% 提示人工扩充)。

用途边界(硬约束):归因结果是提案立项与知识治理排序的证据面,
SHALL NOT 作为回归红线——红线仍为黄金指标劣化 ≥5pp 与提示词指纹可比性。
"""

import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field

from kongming.feedback.models import extract_table_names

CATEGORY_ORDER = (
    "NEG_GUARD",
    "REJECT",
    "ABSTAIN",
    "EXEC",
    "SELECT",
    "JOIN",
    "METRIC",
    "FILTER",
    "OTHER",
)

_OTHER_HINT_THRESHOLD = 0.20  # OTHER 占比提示阈值(spec:≥20% 显式提示)

_JOIN_RE = re.compile(r"\bjoin\b", re.IGNORECASE)
# 聚合函数词边界+左括号(词边界防 sum_income 之类标识符误命中)
_AGG_RE = re.compile(r"\b(count|sum|avg|min|max)\s*\(", re.IGNORECASE)
# WHERE 段提取:到下一个顶层子句关键字或串尾(子查询 where 会被并入——
# 两侧对称处理,证据面用途可接受;sqlglot 深解析在此是过度设计)
_WHERE_RE = re.compile(
    r"\bwhere\b(.*?)(?=\bgroup\s+by\b|\bhaving\b|\border\s+by\b|\blimit\b|\boffset\b|$)",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class Attribution:
    """单题归因产物:唯一一级分类 + SELECT 层拆分 + 判据原文 + 原始信号。

    reason 恒非空(SHALL NOT 只给结论不给依据);signals 保留判定所用的
    原始信号(表集/JOIN 数/聚合集合/WHERE 面),失败明细原样透出可审计。
    """

    category: str
    stage: str = ""  # 仅 SELECT 有值:recall | generate
    reason: str = ""
    signals: dict[str, object] = field(default_factory=dict[str, object])


def _normalize_fragment(text: str) -> str:
    """WHERE 面规范化:小写+压缩全部空白(措辞等价的不同排版不误报)。"""
    return re.sub(r"\s+", " ", text).strip().lower()


def _where_face(sql: str) -> str:
    m = _WHERE_RE.search(sql)
    return _normalize_fragment(m.group(1)) if m else ""


def classify_failure(
    *,
    question: str = "",
    gold_sql: str = "",
    gen_sql: str = "",
    success: bool = False,
    verdict: str = "",
    expect_reject: bool = False,
    expect_no_data: bool = False,
    expected_tables: Collection[str] = frozenset[str](),
    gold_cols: Sequence[str] = (),
    gold_rows: Sequence[Sequence[object]] = (),
    gen_cols: Sequence[str] = (),
    gen_rows: Sequence[Sequence[object]] = (),
) -> Attribution:
    """失败题 → 唯一一级归因(纯函数,零外部调用;判定序见模块 docstring)。

    列/行参数(gold_cols 等)为既有宽松匹配的输入产物,分类判据当前未
    消费——保留入参形态是为了让调用方无需分流装配(spec:入参=失败题的
    既有产物),后续扩充规则时零调用方变更。"""
    v = (verdict or "").strip().upper()
    has_sql = bool(gen_sql and gen_sql.strip())

    # 1. 负例/拒绝守护:用例失败即此类,不再按正例判据细分(spec)
    if expect_reject or expect_no_data:
        kind = "expect_reject 未拒" if expect_reject else "expect_no_data 生成了 SQL"
        return Attribution(
            category="NEG_GUARD",
            reason=f"负例/拒绝守护失效:{kind}(哨兵/绑定校验未生效)",
            signals={"verdict": v, "success": success, "has_gen_sql": has_sql},
        )

    # 2./3. 链路终态(与 EXEC 判据在真实数据中互斥,verdict 携带时先行)
    if v == "BLOCKED":
        return Attribution(
            category="REJECT",
            reason="生成 SQL 被防火墙拦截终止(verdict=BLOCKED)",
            signals={"verdict": v},
        )
    if v == "NO_DATA":
        return Attribution(
            category="ABSTAIN",
            reason="正例题链路返回 NO_DATA(误弃答,哨兵短路触发)",
            signals={"verdict": v, "question": question},
        )

    # 4. 执行失败/无 SQL(ERROR、selfheal 耗尽、链路异常、空生成)
    if not success or not has_sql:
        return Attribution(
            category="EXEC",
            reason=(
                f"生成 SQL 执行失败或无生成 SQL(success={success},"
                f" verdict={v or '—'}, has_sql={has_sql};含 selfheal 耗尽)"
            ),
            signals={"success": success, "verdict": v, "has_gen_sql": has_sql},
        )

    # 5.-8. 结构判据(需要可解析的生成 SQL;抽表失败按零表处理落 OTHER)
    gen_tables, gen_parse_err = extract_table_names(gen_sql)
    gold_tables, _gold_parse_err = extract_table_names(gold_sql)
    # expected_tables 允许全名/末段名混传:与抽表输出同口径折叠到末段名再比
    expected = {t.strip().lower().split(".")[-1] for t in expected_tables if t.strip()}
    exp_missing = sorted(expected - gen_tables)
    gold_missing = sorted(gold_tables - gen_tables)

    if exp_missing:
        return Attribution(
            category="SELECT",
            stage="recall",
            reason=f"期望表未被生成 SQL 覆盖(召回层):缺失 {exp_missing}",
            signals={
                "expected_tables": sorted(set(expected_tables)),
                "gen_tables": sorted(gen_tables),
                "missing": exp_missing,
            },
        )
    if gold_missing:
        return Attribution(
            category="SELECT",
            stage="generate",
            reason=f"gold 表集未被生成 SQL 覆盖(生成层):缺失 {gold_missing}",
            signals={
                "gold_tables": sorted(gold_tables),
                "gen_tables": sorted(gen_tables),
                "missing": gold_missing,
            },
        )

    gold_joins = len(_JOIN_RE.findall(gold_sql))
    gen_joins = len(_JOIN_RE.findall(gen_sql))
    if gen_joins < gold_joins:
        return Attribution(
            category="JOIN",
            reason=f"表集齐但 JOIN 数少:gold {gold_joins} vs gen {gen_joins}",
            signals={"gold_joins": gold_joins, "gen_joins": gen_joins},
        )

    gold_aggs = sorted({m.group(1).lower() for m in _AGG_RE.finditer(gold_sql)})
    gen_aggs = sorted({m.group(1).lower() for m in _AGG_RE.finditer(gen_sql)})
    if gold_aggs != gen_aggs:
        return Attribution(
            category="METRIC",
            reason=f"聚合函数集合不一致:gold {gold_aggs} vs gen {gen_aggs}",
            signals={"gold_aggs": gold_aggs, "gen_aggs": gen_aggs},
        )

    gold_where = _where_face(gold_sql)
    gen_where = _where_face(gen_sql)
    if gold_where != gen_where:
        return Attribution(
            category="FILTER",
            reason="聚合一致但 WHERE 过滤面不一致",
            signals={"gold_where": gold_where, "gen_where": gen_where},
        )

    # 9. OTHER:显式未归因(统计块 SHALL 报告数量与题目 id,不静默吸收)
    return Attribution(
        category="OTHER",
        reason=(
            "无判据命中(表/JOIN/聚合/谓词面一致,失败源于判据未覆盖的形态"
            f"{'或 SQL 解析失败' if gen_parse_err else ''})"
        ),
        signals={"gen_parse_error": gen_parse_err},
    )


def summarize_attributions(items: list[tuple[str, Attribution]]) -> str:
    """失败归因统计块(纯函数):一级分类计数 + SELECT 层拆分 + OTHER 纪律。

    OTHER 桶 SHALL 显式报告数量与题目 id;占比 ≥20% 时提示归因规则不足
    需人工扩充(SHALL NOT 静默吸收)。"""
    if not items:
        return ""
    counts: dict[str, int] = dict.fromkeys(CATEGORY_ORDER, 0)
    other_ids: list[str] = []
    select_stage: dict[str, int] = {"recall": 0, "generate": 0}
    for case_id, a in items:
        counts[a.category] = counts.get(a.category, 0) + 1
        if a.category == "SELECT" and a.stage in select_stage:
            select_stage[a.stage] += 1
        if a.category == "OTHER":
            other_ids.append(case_id)
    total = len(items)
    select_part = (
        f"SELECT={counts['SELECT']}"
        f"(召回层 {select_stage['recall']}/生成层 {select_stage['generate']})"
    )
    parts = [select_part if cat == "SELECT" else f"{cat}={counts[cat]}" for cat in CATEGORY_ORDER]
    lines = [f"=== 失败归因({total} 题失败): " + " | ".join(parts) + " ==="]
    if other_ids:
        lines.append(f"OTHER 题目 id({len(other_ids)}): {', '.join(other_ids)}")
        if len(other_ids) / total >= _OTHER_HINT_THRESHOLD:
            lines.append(
                f"⚠ OTHER 占比 {len(other_ids)}/{total}"
                f"({len(other_ids) / total:.0%})≥20%:归因规则不足,需人工扩充"
                "(SHALL NOT 静默接受)"
            )
    return "\n".join(lines)
