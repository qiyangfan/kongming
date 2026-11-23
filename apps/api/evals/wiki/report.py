"""Wiki 对照结论:按运行前登记的条件裁决 wiki_first / parallel / keep / pending。"""

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

_CONFIG_KEYS = (
    "answer_model",
    "prompt_version",
    "embedding_channel",
    "answer_max_tokens",
    "evidence_limit",
    "citation_support_min",
    "cross_doc_improve_pp",
    "single_doc_regress_pp",
    "per_question_cost_cap_cny",
    "wiki_maintenance_minutes_cap",
    "min_question_counts",
    "cross_categories",
    "single_categories",
)
_CATEGORY_KEYS = ("cross_categories", "single_categories")


@dataclass(frozen=True)
class StrategyStat:
    """单方式按题型拆分后的比率;跨资料/单资料题的基线=原文检索。"""

    n: int
    correct_rate: float
    grounded_rate: float
    citation_support: float
    cross_doc_correct: float
    single_doc_correct: float


@dataclass(frozen=True)
class Overhead:
    """开销检查输入:逐题费用与主题页维护人工时间。

    question_costs 为每个问题跨三种方式的回答费用合计;missing_usage 为
    缺用量、费用不可核实的调用次数;maintenance_minutes 为人工核对记录
    合计,record_problems 非空时按未登记处理。"""

    cost_registered: bool
    question_costs: Sequence[float]
    max_question_cost_cny: float
    missing_usage: int
    maintenance_minutes: float | None
    max_maintenance_minutes: float
    record_problems: tuple[str, ...] = ()


def config_issues(cfg: Mapping[str, object]) -> list[str]:
    """运行条件缺项/非法值;非空时结论只能待判断。"""
    issues = [f"缺少运行条件: {key}" for key in _CONFIG_KEYS if key not in cfg]
    for key in _CATEGORY_KEYS:
        if key in cfg:
            value = cfg.get(key)
            if not isinstance(value, list) or not value:
                issues.append(f"{key} 非法(须为非空题型列表)")
    return issues


PRICE_TABLE_VERSION = "2026-11"


def cost_from_usage(
    usage: Mapping[str, object], *, input_per_million: float, output_per_million: float
) -> float | None:
    """按登记单价折算单次调用费用;用量缺失返回 None(报告标未计入,不估算)。"""
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    if not isinstance(prompt, int) or not isinstance(completion, int):
        return None
    return (prompt * input_per_million + completion * output_per_million) / 1_000_000.0


def _candidate_failures(
    stat: StrategyStat,
    baseline: StrategyStat,
    *,
    citation_min: float,
    cross_improve_pp: float,
    single_regress_pp: float,
) -> list[str]:
    """单候选方式未达条件的原因;空列表=通过。"""
    failures: list[str] = []
    if stat.citation_support < citation_min:
        failures.append(f"引用支持率 {stat.citation_support:.1%} 低于 {citation_min:.0%}")
    cross_gain = stat.cross_doc_correct - baseline.cross_doc_correct
    if cross_gain < cross_improve_pp:
        failures.append(f"跨资料题正确率改善 {cross_gain:+.1%} 低于 {cross_improve_pp:.0%}")
    single_drop = stat.single_doc_correct - baseline.single_doc_correct
    if single_drop < -single_regress_pp:
        failures.append(f"单资料题正确率回归 {single_drop:+.1%} 超限")
    return failures


def _preconditions(
    stats: Mapping[str, StrategyStat], *, sources_ok: bool, overhead: Overhead
) -> tuple[str, list[str]] | None:
    """来源与开销前置检查;返回阻断结论,None=条件齐全可继续判定。"""
    if not sources_ok:
        return "keep", ["越权或失效来源进入了作答,不允许接入"]
    if not overhead.cost_registered:
        return "pending", ["未登记逐题费用单价"]
    if overhead.missing_usage > 0:
        return "pending", [f"费用不可核实:{overhead.missing_usage} 次调用缺用量(未计入)"]
    if not overhead.question_costs or overhead.max_question_cost_cny <= 0:
        return "pending", ["未登记逐题费用上限或缺少逐题费用样本"]
    if overhead.record_problems:
        return "pending", list(overhead.record_problems)
    if overhead.maintenance_minutes is None or overhead.max_maintenance_minutes <= 0:
        return "pending", ["未登记主题页维护时间或其上限"]
    baseline = stats.get("raw")
    if baseline is None or baseline.n <= 0:
        return "pending", ["缺少原文检索基线样本"]
    return None


def _overhead_failures(overhead: Overhead) -> list[str]:
    """登记上限的开销超限原因;空列表=开销条件通过。"""
    failures: list[str] = []
    if max(overhead.question_costs) > overhead.max_question_cost_cny:
        failures.append(
            "单题费用超上限:最高"
            f" {max(overhead.question_costs):.4f} CNY > {overhead.max_question_cost_cny:g} CNY"
        )
    assert overhead.maintenance_minutes is not None  # 前置检查已排除未登记
    if overhead.maintenance_minutes > overhead.max_maintenance_minutes:
        failures.append(
            "主题页维护时间超上限:"
            f" {overhead.maintenance_minutes:.0f} 分钟 > {overhead.max_maintenance_minutes:g} 分钟"
        )
    return failures


def decide(
    stats: Mapping[str, StrategyStat],
    *,
    citation_min: float,
    cross_improve_pp: float,
    single_regress_pp: float,
    overhead: Overhead,
    sources_ok: bool,
    config_issues_: Sequence[str],
) -> tuple[str, list[str]]:
    """返回 (结论, 原因)。

    条件缺失/费用不可核实/维护时间未登记 → pending;越权或失效来源、
    候选未达条件或开销超登记上限 → keep;仅 Wiki 优先通过 → wiki_first;
    仅并行通过 → parallel(两者都过按 Wiki 优先报,接入方向更简单)。"""
    if config_issues_:
        return "pending", list(config_issues_)
    blocked = _preconditions(stats, sources_ok=sources_ok, overhead=overhead)
    if blocked is not None:
        return blocked
    baseline = stats["raw"]
    overhead_failures = _overhead_failures(overhead)
    reasons: list[str] = []
    for name in ("wiki_first", "parallel"):
        stat = stats.get(name)
        if stat is None or stat.n <= 0:
            reasons.append(f"缺少分组样本: {name}")
            continue
        failures = _candidate_failures(
            stat,
            baseline,
            citation_min=citation_min,
            cross_improve_pp=cross_improve_pp,
            single_regress_pp=single_regress_pp,
        )
        if not failures and not overhead_failures:
            return name, []
        if not failures:
            reasons.append(f"{name}: 质量条件通过,但开销条件未过")
        else:
            reasons.extend(f"{name}: {item}" for item in failures)
    return "keep", [*overhead_failures, *reasons]


def question_coverage_issues(
    questions: Sequence[Mapping[str, Any]], required: Mapping[str, int]
) -> list[str]:
    """正式题集题型数量缺口;非空时结论只能待判断。"""
    counts: Counter[str] = Counter(str(q.get("category")) for q in questions)
    return [
        f"正式题集缺少题型: {name}({counts.get(name, 0)}/{need})"
        for name, need in required.items()
        if counts.get(name, 0) < need
    ]
