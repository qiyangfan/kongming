"""多智能体协作编排测试(091,剧本化:mock LLM/worker)。

覆盖:拆解→串行派发→聚合 DONE、分歧标注不静默调和、worker 失败如实 ERROR、
拆解失败回落单 worker、空清单收口、worker 数上限、深度超限拒绝、预算不足
不派发/截断披露未完成、worker 授权面结构断言、聚合 summary 恒为合法 JSON。
"""

import inspect
import json
import typing

import pytest

from kongming_agent.collab_gen import (
    DEFAULT_MAX_WORKERS,
    CollabDepthExceeded,
    WorkerRunner,
    run_collab,
)


class FakeLlm:
    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)

    async def chat(self, system: str, user: str, **_kw: object) -> str:
        return self.replies.pop(0)


def _topo(*subgoals: str) -> str:
    return json.dumps([{"subgoal": s} for s in subgoals], ensure_ascii=False)


@pytest.mark.asyncio
async def test_dissent_not_silently_reconciled() -> None:
    """同题双 worker 结论不一致 → dissent 标注(不静默调和)。"""

    async def worker_a(subgoal: str, context: str) -> dict[str, object]:
        return {"verdict": "PASS", "summary": "销售额 100"}

    async def worker_b(subgoal: str, context: str) -> dict[str, object]:
        return {"verdict": "PASS", "summary": "销售额 200"}

    calls: list[str] = []

    async def runner(subgoal: str, context: str) -> dict[str, object]:
        calls.append(subgoal)
        return await (worker_a if len(calls) == 1 else worker_b)(subgoal, context)

    out = await run_collab(
        payload={"subgoal": "查销售额"},
        llm=FakeLlm([_topo("查 A 口径", "查 B 口径")]),
        worker_runner=runner,
    )
    assert out["dissent"] is True
    assert out["verdict"] == "PASS"  # 全部 PASS → PASS,但分歧已标注
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_worker_error_recorded_not_raised() -> None:
    """单 worker 失败 → 结论记 ERROR(细节留日志,结论只带类型名),
    不中断他 worker、不裸抛;交叉裁决:非全 PASS → 整体 ERROR。"""

    async def runner_fail(subgoal: str, context: str) -> dict[str, object]:
        raise RuntimeError("down")

    async def runner_ok(subgoal: str, context: str) -> dict[str, object]:
        return {"verdict": "PASS", "summary": "ok"}

    calls: list[str] = []

    async def runner(subgoal: str, context: str) -> dict[str, object]:
        calls.append(subgoal)
        return await (runner_fail if len(calls) == 1 else runner_ok)(subgoal, context)

    out = await run_collab(
        payload={"subgoal": "查销售额"},
        llm=FakeLlm([_topo("子目标 A", "子目标 B")]),
        worker_runner=runner,
    )
    assert out["verdict"] == "ERROR"  # 单 worker 失败即不通过(ERROR 权重更高)
    conclusions = json.loads(str(out["summary"]))
    assert conclusions[0]["verdict"] == "ERROR"
    assert conclusions[0]["summary"] == "worker 执行失败: RuntimeError"


@pytest.mark.asyncio
async def test_topo_parse_failure_falls_back_single_worker() -> None:
    """拆解 LLM 输出畸形 → 回落单 worker 直执(不失败)。"""

    async def runner(subgoal: str, context: str) -> dict[str, object]:
        return {"verdict": "PASS", "summary": "ok"}

    out = await run_collab(
        payload={"subgoal": "查销售额"},
        llm=FakeLlm(["这不是 JSON"]),
        worker_runner=runner,
    )
    assert out["verdict"] == "PASS"


@pytest.mark.asyncio
async def test_worker_cap_respected() -> None:
    """worker 数上限:拆解产出再多也只派发上限内数量(单一定义上限)。"""
    seen: list[str] = []

    async def runner(subgoal: str, context: str) -> dict[str, object]:
        seen.append(subgoal)
        return {"verdict": "PASS", "summary": "ok"}

    out = await run_collab(
        payload={"subgoal": "查销售额"},
        llm=FakeLlm([_topo(*[f"s{i}" for i in range(10)])]),
        worker_runner=runner,
    )
    assert len(seen) == DEFAULT_MAX_WORKERS
    # 上限外子目标如实披露为未派发清单(部分完成语义)
    pending = typing.cast("list[str]", out["pending_subgoals"])
    assert len(pending) == 10 - DEFAULT_MAX_WORKERS


@pytest.mark.asyncio
async def test_empty_topology_finishes() -> None:
    """拆解产出空清单 → 空结论收口(verdict ERROR,不挂起)。"""

    async def runner(subgoal: str, context: str) -> dict[str, object]:
        raise AssertionError("不应派发")

    out = await run_collab(
        payload={"subgoal": "查销售额"},
        llm=FakeLlm(["[]"]),
        worker_runner=runner,
    )
    assert out["worker_count"] == 0
    assert out["verdict"] == "ERROR"


@pytest.mark.asyncio
async def test_depth_exceeded_refuses_nesting() -> None:
    """深度上限:超限构造即拒绝嵌套(失控防护;不产生任何派发)。"""

    async def runner(subgoal: str, context: str) -> dict[str, object]:
        raise AssertionError("超深协作不应派发任何 worker")

    with pytest.raises(CollabDepthExceeded):
        await run_collab(
            payload={"subgoal": "查销售额"},
            llm=FakeLlm([_topo("子目标")]),
            worker_runner=runner,
            depth=2,  # 任意 > 上限 1
        )


@pytest.mark.asyncio
async def test_budget_exhausted_no_dispatch() -> None:
    """预算继承不足(worker_budget=0)→ 不拆解不派发,空收口并披露未完成。"""

    async def runner(subgoal: str, context: str) -> dict[str, object]:
        raise AssertionError("预算尽不应派发")

    out = await run_collab(
        payload={"subgoal": "查销售额"},
        llm=FakeLlm([_topo("子目标")]),
        worker_runner=runner,
        worker_budget=0,
    )
    assert out["worker_count"] == 0
    assert out["verdict"] == "ERROR"
    assert out["pending_subgoals"] == ["查销售额"]


@pytest.mark.asyncio
async def test_budget_truncates_and_discloses_pending() -> None:
    """预算截断:只派发预算内数量,未派发子目标如实披露(不谎称完成)。"""
    seen: list[str] = []

    async def runner(subgoal: str, context: str) -> dict[str, object]:
        seen.append(subgoal)
        return {"verdict": "PASS", "summary": "ok"}

    out = await run_collab(
        payload={"subgoal": "查销售额"},
        llm=FakeLlm([_topo("甲", "乙", "丙")]),
        worker_runner=runner,
        worker_budget=1,
    )
    assert len(seen) == 1
    assert out["pending_subgoals"] == ["乙", "丙"]
    assert out["verdict"] == "PASS"  # 已派发部分全 PASS


@pytest.mark.asyncio
async def test_worker_authorization_surface_structural() -> None:
    """授权不提权结构断言(D5):worker 执行器签名仅 (subgoal, context),
    编排器入口不持 api/token/credentials 句柄(接线组合根时同步收紧)。"""
    sig = inspect.signature(run_collab)
    for forbidden in ("api", "token", "credentials", "tools"):
        assert forbidden not in sig.parameters, f"run_collab 不得持有 {forbidden} 参数"
    params, _return = typing.get_args(WorkerRunner)
    assert list(params) == [str, str]  # (subgoal, context) —— 无授权载荷


@pytest.mark.asyncio
async def test_aggregate_summary_always_valid_json() -> None:
    """聚合 summary 恒为合法 JSON(不再外层截断;长结论完整保留)。"""

    async def runner(subgoal: str, context: str) -> dict[str, object]:
        return {"verdict": "PASS", "summary": "很长的结论" * 500, "sql": "SELECT 1"}

    out = await run_collab(
        payload={"subgoal": "查销售额"},
        llm=FakeLlm([_topo("甲", "乙", "丙")]),
        worker_runner=runner,
    )
    parsed = json.loads(str(out["summary"]))
    assert len(parsed) == 3
    # 字段级 2000 上限保留(设计内);外层 JSON 完整合法、不被拦腰截断
    assert all(len(str(c["summary"])) == 2000 for c in parsed)
