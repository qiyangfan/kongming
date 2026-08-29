"""Agent 图拓扑测试:重试必经 selfheal(计数),权限类不重试,图必然终止。

回归背景:曾存在 execute 失败 → 直达 generate 的边,retry_count 永不增长,
图靠 recursion_limit 兜底——本测试锁死该缺陷不再复发。
005 起权限类(BLOCKED)从 execute/validate 直接终态,不进 selfheal、不耗配额。
"""

# pyright: reportPrivateUsage=false
# 测试惯例:路由函数为模块私有实现细节,测试直引锁死拓扑契约(仓库约定,不改名)

from kongming_agent.graph.build import (
    MAX_RETRY,
    _after_execute,
    _after_generate,
    _after_selfheal,
    _after_validate,
)
from kongming_agent.graph.state import ExecutionResult, ValidationResult

BLOCKED = ExecutionResult(verdict="BLOCKED", reason="BLOCKED_ROOT: Delete")
FAILED = ExecutionResult(verdict="ERROR", reason="column goods.missing does not exist")
PASSED = ExecutionResult(verdict="PASS")


def test_execute_error_goes_to_selfheal_not_generate() -> None:
    assert _after_execute({"execution": FAILED}) == "selfheal"


def test_execute_blocked_goes_direct_fail() -> None:
    """权限/策略拦截直接终态(不耗重试配额)。"""
    assert _after_execute({"execution": BLOCKED}) == "fail"


def test_execute_success_goes_to_summarize() -> None:
    assert _after_execute({"execution": PASSED}) == "summarize"


def test_validate_permission_block_goes_direct_fail() -> None:
    v = ValidationResult(passed=False, permission_block=True, reason="BLOCKED_ROOT: Delete")
    assert _after_validate({"validation": v}) == "fail"


def test_validate_retryable_failure_goes_to_selfheal() -> None:
    v = ValidationResult(passed=False, reason="PARSE_ERROR: syntax")
    assert _after_validate({"validation": v}) == "selfheal"


def test_validate_pass_goes_to_execute() -> None:
    assert _after_validate({"validation": ValidationResult(passed=True)}) == "execute"


def test_generate_sentinel_short_circuit_goes_fail() -> None:
    """043:哨兵短路产物(end_reason=no_data)直达 fail 终态,不进 validate/execute。"""
    assert _after_generate({"end_reason": "no_data"}) == "fail"


def test_generate_normal_goes_to_validate() -> None:
    assert _after_generate({}) == "validate"


def test_retry_exhaustion_terminates() -> None:
    """预算耗尽由 selfheal 标 end_reason(计数=实际发起的重试次数),路由收束 fail。"""
    assert _after_selfheal({"execution": FAILED, "retry_count": MAX_RETRY,
                            "end_reason": "retry_exhausted"}) == "fail"


def test_retry_only_via_selfheal_counting() -> None:
    assert _after_selfheal({"execution": FAILED, "retry_count": 0}) == "retry"
