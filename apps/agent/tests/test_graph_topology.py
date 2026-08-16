"""Agent 图拓扑测试:重试必经 selfheal(计数),权限类不重试,图必然终止。

回归背景:曾存在 execute 失败 → 直达 generate 的边,retry_count 永不增长,
图靠 recursion_limit 兜底——本测试锁死该缺陷不再复发。
"""

from kongming_agent.graph.build import MAX_RETRY, _after_execute, _after_selfheal, build
from kongming_agent.graph.state import ExecutionResult

BLOCKED = ExecutionResult(verdict_passed=False, verdict_reason="BLOCKED_ROOT: Delete")
FAILED = ExecutionResult(verdict_passed=False, verdict_reason="ERROR: column missing")
PASSED = ExecutionResult(verdict_passed=True)


def test_execute_failure_goes_to_selfheal_not_generate() -> None:
    assert _after_execute({"execution": FAILED}) == "selfheal"
    assert _after_execute({"execution": BLOCKED}) == "selfheal"


def test_execute_success_goes_to_summarize() -> None:
    assert _after_execute({"execution": PASSED}) == "summarize"


def test_permission_block_never_retries() -> None:
    assert _after_selfheal({"execution": BLOCKED, "retry_count": 1}) == "fail"


def test_retry_exhaustion_terminates() -> None:
    assert _after_selfheal({"execution": FAILED, "retry_count": MAX_RETRY}) == "fail"


def test_retry_only_via_selfheal_counting() -> None:
    assert _after_selfheal({"execution": FAILED, "retry_count": 0}) == "retry"


def test_graph_terminates_without_execution_result() -> None:
    """骨架节点(execute 未接回调)跑完整图必须终止,不得依赖 recursion_limit。"""
    graph = build()
    final = graph.invoke({"trace_id": "t", "tenant_id": "x", "question": "测试问句"})
    assert final["retry_count"] == MAX_RETRY  # 每轮经 selfheal 计数,耗尽即终止
