"""065:评估红线判定逻辑——劣化 ≥5pp 判失败;跨指纹不可比不判劣化。"""

from typing import Any


def _pass_rate(summary: dict[str, Any]) -> float:
    return float(summary.get("pass_rate", 0.0))


def should_fail_redline(
    prev: dict[str, Any], now: dict[str, Any], *, threshold_pp: float = 5.0
) -> tuple[bool, str]:
    """返回 (是否失败, 原因)。指纹不同 → 不可比,不判劣化。"""
    if prev.get("prompt_fingerprint") != now.get("prompt_fingerprint"):
        return False, "cross-fingerprint:不可比,不判劣化"
    drop = (_pass_rate(prev) - _pass_rate(now)) * 100
    if drop >= threshold_pp:
        return True, f"劣化 {drop:.1f}pp ≥ {threshold_pp}pp"
    return False, f"劣化 {drop:.1f}pp < 阈值"


def test_no_baseline_records_only() -> None:
    assert should_fail_redline({}, {"pass_rate": 0.5, "prompt_fingerprint": "a"})[0] is False


def test_drop_5pp_fails() -> None:
    prev = {"pass_rate": 0.90, "prompt_fingerprint": "same"}
    now = {"pass_rate": 0.85, "prompt_fingerprint": "same"}
    fail, reason = should_fail_redline(prev, now)
    assert fail is True
    assert "劣化" in reason


def test_improve_passes() -> None:
    prev = {"pass_rate": 0.80, "prompt_fingerprint": "same"}
    now = {"pass_rate": 0.90, "prompt_fingerprint": "same"}
    assert should_fail_redline(prev, now)[0] is False


def test_cross_fingerprint_not_comparable() -> None:
    prev = {"pass_rate": 0.95, "prompt_fingerprint": "old"}
    now = {"pass_rate": 0.50, "prompt_fingerprint": "new"}
    fail, reason = should_fail_redline(prev, now)
    assert fail is False
    assert "不可比" in reason
