"""064-rag-relevance-gate:召回相关度闸门(纯函数,零 IO)。

判据设计(proposal Why 的三重障碍结论):
- **分数出口在 SQL**:各路命中带原生分(向量/历史路=余弦相似度,全文路=
  ts_rank 检索得分),RRF 融合分只用于排序与观测;
- **相关性判据是双条件**:`(过闸命中数 ≥ min_count ∧ top1 ≥ 首档阈值)`
  或 `(过闸命中数 ≥ min_count ∧ 前 N 均值 ≥ 次级阈值)`——第二条专治
  "单条差一点、多条互相印证"的真实相关形态;
- **禁止拿 RRF 融合分当相关度**:融合值由命中路数与名次决定,与"有多相似"
  无关(单路首位 1/61≈0.0164 < 双路并列末位 2/65≈0.0308,拍绝对阈值会
  系统性惩罚单路真命中);
- **零命中不投票**:某路零命中即该路不参与判定,SHALL NOT 把"零命中"
  当"低相关";
- **恒生效裁剪**(2026-10-11 owner 裁决收编三态):无生产流量,off/shadow/
  primary 的标定前提不存在;零误拒对拍已证即直接切,不保留关闭/影子形态。
  零知识数据源自动旁路在 service 层收口(zero-knowledge ≠ low-relevance)。
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol


class ScoredHit(Protocol):
    """带原生分的命中(各路召回命中的结构契约;鸭子类型,不绑定具体类)。"""

    @property
    def score(self) -> float: ...


@dataclass(frozen=True)
class GateVerdict:
    """闸门判定(纯函数产物):passed=是否过闸;reason=归因(no_hits/
    min_count/top1_below/mean_below/passed)。"""

    passed: bool
    reason: str
    top1: float = 0.0
    mean_top_n: float = 0.0
    count: int = 0

    def as_dict(self) -> dict[str, object]:
        """trace 投影(可观测:生效阈值与判定结论)。"""
        return {
            "passed": self.passed,
            "reason": self.reason,
            "top1": round(self.top1, 6),
            "mean_top_n": round(self.mean_top_n, 6),
            "count": self.count,
        }


def is_relevant(
    hits: Sequence[ScoredHit],
    *,
    top1_threshold: float,
    min_count: int,
    mean_threshold: float,
    mean_window: int,
) -> GateVerdict:
    """双条件判据(纯函数)。

    - hits 为该路命中(已按各自原生分降序);
    - 零命中 → not passed(reason=no_hits,不参与后续判定);
    - 命中数 < min_count → not passed(reason=min_count);
    - 条件一(top1 ≥ 首档)或条件二(前 mean_window 均值 ≥ 次级档)成立
      → passed;
    - 否则 not passed(reason 取未满足的主因)。
    """
    count = len(hits)
    if count == 0:
        return GateVerdict(passed=False, reason="no_hits", count=0)
    if count < min_count:
        return GateVerdict(passed=False, reason="min_count", count=count)
    top1 = hits[0].score
    window = hits[:mean_window] if mean_window > 0 else hits[:1]
    mean_top_n = sum(h.score for h in window) / len(window)
    if top1 >= top1_threshold:
        return GateVerdict(
            passed=True, reason="passed", top1=top1, mean_top_n=mean_top_n, count=count
        )
    if mean_top_n >= mean_threshold:
        return GateVerdict(
            passed=True, reason="passed", top1=top1, mean_top_n=mean_top_n, count=count
        )
    return GateVerdict(
        passed=False, reason="mean_below", top1=top1, mean_top_n=mean_top_n, count=count
    )


@dataclass(frozen=True)
class GateResult[T]:
    """闸门消费结果(passed=进入提示词的命中——过闸全留;
    dropped=整路被裁的命中数——不过闸全裁,路级命中共进退)。"""

    passed: list[T] = field(default_factory=list[T])
    dropped: int = 0
    verdict: GateVerdict | None = None


def gate_hits[T](
    hits: list[T],
    *,
    top1_threshold: float,
    min_count: int,
    mean_threshold: float,
    mean_window: int,
) -> GateResult[T]:
    """闸门消费(纯函数,恒裁剪形态)。

    过闸全留,不过闸整路全裁(非按条过滤);verdict 恒产出
    (trace/试跑可观测)。零命中路:空集即空集(零命中不投票)。
    """
    scored = [_Score(getattr(h, "score", 0.0)) for h in hits]
    verdict = is_relevant(
        scored,
        top1_threshold=top1_threshold,
        min_count=min_count,
        mean_threshold=mean_threshold,
        mean_window=mean_window,
    )
    if verdict.passed:
        return GateResult[T](passed=hits, dropped=0, verdict=verdict)
    return GateResult[T](passed=[], dropped=len(hits), verdict=verdict)


@dataclass(frozen=True)
class _Score:
    """内部适配器:把任意命中分数包成 ScoredHit 形态(纯函数管道)。"""

    value: float

    @property
    def score(self) -> float:
        return self.value
