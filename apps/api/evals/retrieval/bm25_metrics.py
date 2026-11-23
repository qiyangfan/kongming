"""BM25 对照统计与判定纯函数(零 IO)。

结论三态:adopt(建议另立生产接入提案)/ keep(保留现有排序)/
pending(待验证:条件或样本缺失、范围检查未过)。判定口径由
`bm25_eval.yaml` 在运行前固定,本模块只做计算与套用。
"""

import math
import random
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

IMPROVE_PP = 0.05
REGRESS_PP = 0.05
REQUIRED_GROUPS = ("precise", "overall", "paraphrase", "multihop")
_CONFIG_KEYS = (
    "top_k",
    "latency_cap_ms_p95",
    "bootstrap_samples",
    "bootstrap_seed",
    "min_group_counts",
)


def mean(values: Sequence[float]) -> float:
    """算术平均;空输入计 0(调用方以样本自检拦空)。"""
    return sum(values) / len(values) if values else 0.0


def percentile(values: Sequence[float], q: float) -> float:
    """最近秩百分位(q∈(0,100]);空输入计 0。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, math.ceil(q / 100.0 * len(ordered)) - 1))
    return ordered[idx]


def paired_ci(
    diffs: Sequence[float], *, samples: int = 2000, seed: int = 7, alpha: float = 0.05
) -> tuple[float, float]:
    """同题差值成对重采样的百分位置信区间(固定种子可复现)。"""
    if not diffs or samples <= 0:
        return (0.0, 0.0)
    rng = random.Random(seed)  # noqa: S311  # 固定种子重采样,非密码学用途
    n = len(diffs)
    means = sorted(sum(diffs[rng.randrange(n)] for _ in range(n)) / n for _ in range(samples))
    lo = means[max(0, int(alpha / 2.0 * samples))]
    hi = means[min(samples - 1, int((1.0 - alpha / 2.0) * samples) - 1)]
    return (lo, hi)


@dataclass(frozen=True)
class GroupStat:
    """单组两组均值;差值 = 实验组 - 基线组。"""

    base_recall: float
    exp_recall: float
    base_full_hop: float
    exp_full_hop: float
    n: int

    @property
    def recall_diff(self) -> float:
        return self.exp_recall - self.base_recall

    @property
    def full_hop_diff(self) -> float:
        return self.exp_full_hop - self.base_full_hop


def build_groups(rows: Sequence[Mapping[str, Any]]) -> dict[str, GroupStat]:
    """逐题记录 → 分组统计;每题可属多组,overall 由调用方显式加入。"""
    buckets: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        for group in cast("list[str]", row["groups"]):
            buckets.setdefault(str(group), []).append(row)
    return {
        name: GroupStat(
            base_recall=mean([float(r["base_recall"]) for r in items]),
            exp_recall=mean([float(r["exp_recall"]) for r in items]),
            base_full_hop=mean([float(r["base_full_hop"]) for r in items]),
            exp_full_hop=mean([float(r["exp_full_hop"]) for r in items]),
            n=len(items),
        )
        for name, items in buckets.items()
    }


def config_issues(cfg: Mapping[str, Any]) -> list[str]:
    """运行条件缺项/非法值;非空时报告只能给待验证。"""
    issues = [f"缺少运行条件: {key}" for key in _CONFIG_KEYS if key not in cfg]
    if issues:
        return issues
    if int(cfg["top_k"]) <= 0:
        issues.append("top_k 非法")
    if int(cfg["latency_cap_ms_p95"]) <= 0:
        issues.append("latency_cap_ms_p95 非法")
    if int(cfg["bootstrap_samples"]) <= 0:
        issues.append("bootstrap_samples 非法")
    if not isinstance(cfg["min_group_counts"], Mapping):
        issues.append("min_group_counts 非法")
    return issues


def _pending_problems(
    groups: Mapping[str, GroupStat],
    config_issues_: Sequence[str],
    scope_ok: bool,
    min_group_counts: Mapping[str, int] | None,
) -> list[str]:
    """缺条件/分组样本低于登记下限/范围检查未过;非空即只能待验证。"""
    problems = list(config_issues_)
    required = min_group_counts or {}
    for name in REQUIRED_GROUPS:
        stat = groups.get(name)
        need = max(1, int(required.get(name, 0)))
        if stat is None or stat.n < need:
            problems.append(f"分组样本不足: {name}({0 if stat is None else stat.n}/{need})")
    if not scope_ok:
        problems.append("范围隔离检查未通过")
    return problems


def _failure_reasons(
    groups: Mapping[str, GroupStat],
    precise_ci: tuple[float, float],
    latency_p95_ms: float,
    latency_cap_ms: float,
) -> list[str]:
    """数据完整时未达改善/回归/耗时条件的原因;空列表=建议接入。"""
    failures: list[str] = []
    precise = groups["precise"]
    if precise.recall_diff < IMPROVE_PP:
        failures.append(f"精确词题 Recall@k 改善 {precise.recall_diff:+.1%},低于 {IMPROVE_PP:.0%}")
    if precise_ci[0] <= 0:
        failures.append(f"精确词题改善的 95% 区间下限 {precise_ci[0]:+.3f} 不大于 0")
    for name in ("overall", "paraphrase", "multihop"):
        stat = groups[name]
        if stat.recall_diff < -REGRESS_PP:
            failures.append(f"{name} Recall@k 回归 {stat.recall_diff:+.1%} 超限")
    if groups["multihop"].full_hop_diff < -REGRESS_PP:
        failures.append("多段证据题全跳覆盖回归超限")
    if latency_p95_ms > latency_cap_ms:
        failures.append(f"检索 P95 耗时 {latency_p95_ms:.0f}ms 超上限 {latency_cap_ms}ms")
    return failures


def coverage_issues(
    questions: Sequence[Mapping[str, Any]], required: Mapping[str, int]
) -> list[str]:
    """正式题集 subtype 覆盖缺口;非空时报告只能给待验证(缺题型只作调试)。"""
    counts: Counter[str] = Counter()
    for q in questions:
        groups = cast("list[str]", q.get("groups") or [])
        subtype = q.get("subtype")
        if "precise" in groups and subtype:
            counts[str(subtype)] += 1
    return [
        f"正式题集缺少题型: {name}({counts.get(name, 0)}/{need})"
        for name, need in required.items()
        if counts.get(name, 0) < need
    ]


def assess(
    groups: Mapping[str, GroupStat],
    *,
    precise_ci: tuple[float, float],
    latency_p95_ms: float,
    latency_cap_ms: float,
    config_issues_: Sequence[str],
    scope_ok: bool,
    min_group_counts: Mapping[str, int] | None = None,
) -> tuple[str, list[str]]:
    """套用预登记条件,返回 (结论, 未通过或缺失原因)。

    样本缺失/低于登记下限/条件缺失/范围检查未过 → pending;数据完整但
    改善或回归不达阈值 → keep;全部条件满足 → adopt。"""
    problems = _pending_problems(groups, config_issues_, scope_ok, min_group_counts)
    if problems:
        return "pending", problems
    failures = _failure_reasons(groups, precise_ci, latency_p95_ms, latency_cap_ms)
    return ("keep", failures) if failures else ("adopt", [])
