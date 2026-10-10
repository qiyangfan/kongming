"""多智能体协作编排测试(091,剧本化:mock LLM/worker)。

覆盖:拆解→派发→聚合 DONE、分歧标注不静默调和、分歧核实采纳/预算不足保留、
critic 审查通过/修订成功/轮次尽保留意见、worker 失败如实 ERROR、拆解失败回落、
空清单收口、worker 数上限与部署 cap、深度上限、预算不足不派发/截断披露、
parallel 派发(输入冻结、结果齐整)、worker 授权面结构断言、聚合 summary 恒为
合法 JSON、接线后无游离协作入口结构断言(agent 面路由零 collab 端点)。
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
        self.calls: list[tuple[str, str]] = []

    async def chat(self, system: str, user: str, **_kw: object) -> str:
        self.calls.append((system, user))
        return self.replies.pop(0)


def _topo(*subgoals: str) -> str:
    return json.dumps([{"subgoal": s} for s in subgoals], ensure_ascii=False)


@pytest.mark.asyncio
async def test_dissent_annotated_with_verification() -> None:
    """同题双 worker 结论不一致 → 派发核实 worker 后 LLM 仲裁不支持任一方
    → 分歧如实标注(不静默调和、不编造调和值)。"""

    async def runner(subgoal: str, context: str) -> dict[str, object]:
        if "A 口径" in subgoal:
            return {"verdict": "PASS", "summary": "销售额 100"}
        if "B 口径" in subgoal:
            return {"verdict": "PASS", "summary": "销售额 200"}
        return {"verdict": "PASS", "summary": "核实执行完成"}  # 核实 worker

    llm = FakeLlm([_topo("查 A 口径", "查 B 口径"), '{"supports": "neither"}'])
    out = await run_collab(payload={"subgoal": "查销售额"}, llm=llm, worker_runner=runner)
    assert out["dissent"] is True
    assert out["verdict"] == "PASS"  # 核实条目不计入交叉判定:工作结论仍全 PASS
    assert out["reservation"] is not None
    conclusions = typing.cast("list[dict[str, object]]", out["conclusions"])
    assert conclusions[-1].get("verification") is True  # 核实条目入账可查


@pytest.mark.asyncio
async def test_dissent_verification_adopts_one_side() -> None:
    """核实仲裁支持 A → 分歧收口(采纳 A,标注采纳依据),dissent 清除。"""

    async def runner(subgoal: str, context: str) -> dict[str, object]:
        if "A 口径" in subgoal:
            return {"verdict": "PASS", "summary": "销售额 100"}
        if "B 口径" in subgoal:
            return {"verdict": "PASS", "summary": "销售额 200"}
        return {"verdict": "PASS", "summary": "核实执行完成"}

    llm = FakeLlm([_topo("查 A 口径", "查 B 口径"), '{"supports": "A"}'])
    out = await run_collab(payload={"subgoal": "查销售额"}, llm=llm, worker_runner=runner)
    assert out["dissent"] is False
    conclusions = typing.cast("list[dict[str, object]]", out["conclusions"])
    assert "采纳 A 方" in str(conclusions[-1]["summary"])


@pytest.mark.asyncio
async def test_dissent_budget_exhausted_keeps_annotation() -> None:
    """预算不足(验证 worker 派不出)→ 分歧如实保留标注,不静默调和。"""

    async def runner(subgoal: str, context: str) -> dict[str, object]:
        return {"verdict": "PASS", "summary": f"结论@{subgoal}"}

    llm = FakeLlm([_topo("查 A 口径", "查 B 口径")])
    out = await run_collab(
        payload={"subgoal": "查销售额"}, llm=llm, worker_runner=runner, worker_budget=2
    )
    assert out["dissent"] is True
    assert "预算不足" in str(out["reservation"])


@pytest.mark.asyncio
async def test_critic_pass_keeps_verdict() -> None:
    """critic 审查通过 → 结论保持 PASS,无保留意见。"""

    async def runner(subgoal: str, context: str) -> dict[str, object]:
        return {"verdict": "PASS", "summary": "ok"}  # 同结论:不触发分歧,纯 critic 场景

    llm = FakeLlm([_topo("甲", "乙"), '{"pass": true, "issues": []}'])
    out = await run_collab(
        payload={"subgoal": "查销售额"}, llm=llm, worker_runner=runner, critic=True
    )
    assert out["verdict"] == "PASS"
    assert out["reservation"] is None


@pytest.mark.asyncio
async def test_critic_revision_redoes_flagged_worker() -> None:
    """critic 打回某 worker → 有限轮修订:重派该子目标(带反馈),修订后
    全 PASS → verdict PASS(替换原结论,不新增同题条目)。"""

    attempts: list[str] = []

    async def runner(subgoal: str, context: str) -> dict[str, object]:
        attempts.append(subgoal)
        if "critic 反馈" in context:
            return {"verdict": "PASS", "summary": "扎实结论"}  # 修订重跑
        return {"verdict": "PASS", "summary": "草稿结论"}  # 首轮(同结论:无分歧)

    llm = FakeLlm(
        [
            _topo("甲", "乙"),
            '{"pass": false, "issues": [{"subgoal": "乙", "feedback": "证据不足"}]}',
            '{"pass": true, "issues": []}',
        ]
    )
    out = await run_collab(
        payload={"subgoal": "查销售额"}, llm=llm, worker_runner=runner, critic=True
    )
    assert out["verdict"] == "PASS"
    assert attempts.count("乙") == 2  # 原派发 + critic 修订重派
    conclusions = typing.cast("list[dict[str, object]]", out["conclusions"])
    assert len([c for c in conclusions if c["subgoal"] == "乙"]) == 1  # 替换非追加


@pytest.mark.asyncio
async def test_critic_rounds_exhausted_keeps_reservation() -> None:
    """critic 修订后仍未全通过 → 轮次尽如实附保留意见(不无限循环)。"""

    async def runner(subgoal: str, context: str) -> dict[str, object]:
        if "critic 反馈" in context:
            return {"verdict": "ERROR", "summary": "重做仍失败"}
        return {"verdict": "PASS", "summary": "ok"}

    llm = FakeLlm(
        [
            _topo("甲", "乙"),
            '{"pass": false, "issues": [{"subgoal": "乙", "feedback": "重做"}]}',
        ]
    )
    out = await run_collab(
        payload={"subgoal": "查销售额"},
        llm=llm,
        worker_runner=runner,
        critic=True,
        worker_budget=3,
    )
    assert out["verdict"] == "ERROR"
    assert "保留" in str(out["reservation"]) or "未全通过" in str(out["reservation"])


@pytest.mark.asyncio
async def test_critic_multi_round_continues_until_pass_or_exhausted() -> None:
    """critic 轮次 N=2:第 1 轮修订后仍未全通过 → 进入第 2 轮审查再修订
    (修订循环真实生效,非恒单轮);第 2 轮通过 → PASS。"""

    redo_attempts: list[int] = []

    async def runner(subgoal: str, context: str) -> dict[str, object]:
        if "critic 反馈" in context:
            redo_attempts.append(1)
            if len(redo_attempts) == 1:
                return {"verdict": "ERROR", "summary": "第一轮重做仍失败"}
            return {"verdict": "PASS", "summary": "第二轮重做通过"}
        return {"verdict": "PASS", "summary": "ok"}

    llm = FakeLlm(
        [
            _topo("甲", "乙"),
            '{"pass": false, "issues": [{"subgoal": "乙", "feedback": "证据不足"}]}',
            '{"pass": false, "issues": [{"subgoal": "乙", "feedback": "再核实"}]}',
            '{"pass": true, "issues": []}',
        ]
    )
    out = await run_collab(
        payload={"subgoal": "查销售额"},
        llm=llm,
        worker_runner=runner,
        critic=True,
        worker_budget=5,
        max_revision_rounds=2,
    )
    assert out["verdict"] == "PASS"
    assert len(redo_attempts) == 2  # 两轮各重做一次
    assert len(llm.calls) == 3  # 拆解 + 两轮 critic 审查;第 2 轮修订后本地重算全 PASS 即收口


@pytest.mark.asyncio
async def test_parallel_dispatch_preserves_results() -> None:
    """parallel 派发:全部 worker 执行、结果与子目标一一对应(输入冻结不串扰)。"""
    seen: list[str] = []

    async def runner(subgoal: str, context: str) -> dict[str, object]:
        seen.append(subgoal)
        return {"verdict": "PASS", "summary": f"结论@{subgoal}"}

    llm = FakeLlm([_topo("甲", "乙", "丙")])
    out = await run_collab(
        payload={"subgoal": "查销售额"}, llm=llm, worker_runner=runner, dispatch="parallel"
    )
    assert sorted(seen) == sorted(["甲", "乙", "丙"])
    conclusions = typing.cast("list[dict[str, object]]", out["conclusions"])
    assert {str(c["subgoal"]) for c in conclusions} == {"甲", "乙", "丙"}
    assert out["verdict"] == "PASS"


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
async def test_worker_cap_respected_and_deploy_cap_tightens() -> None:
    """worker 数上限:拆解再多也只派发上限内数量;部署 cap 可再收紧。"""
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
    pending = typing.cast("list[str]", out["pending_subgoals"])
    assert len(pending) == 10 - DEFAULT_MAX_WORKERS

    seen.clear()
    await run_collab(
        payload={"subgoal": "查销售额"},
        llm=FakeLlm([_topo(*[f"s{i}" for i in range(10)])]),
        worker_runner=runner,
        max_workers_cap=2,
    )
    assert len(seen) == 2  # 部署 cap 收紧生效


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
async def test_deploy_depth_cap_parameterized() -> None:
    """部署级深度上限参数化:cap=2 时 depth=2 放行、depth=3 仍拒。"""

    async def runner(subgoal: str, context: str) -> dict[str, object]:
        return {"verdict": "PASS", "summary": "ok"}

    out = await run_collab(
        payload={"subgoal": "查销售额"},
        llm=FakeLlm([_topo("子目标")]),
        worker_runner=runner,
        depth=2,
        max_depth=2,
    )
    assert out["verdict"] == "PASS"
    with pytest.raises(CollabDepthExceeded):
        await run_collab(
            payload={"subgoal": "查销售额"},
            llm=FakeLlm([_topo("子目标")]),
            worker_runner=runner,
            depth=3,
            max_depth=2,
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
    编排器入口不持 api/token/credentials/tools 句柄(协作不引入新主体)。"""
    sig = inspect.signature(run_collab)
    for forbidden in ("api", "token", "credentials", "tools"):
        assert forbidden not in sig.parameters, f"run_collab 不得持有 {forbidden} 参数"
    params, _return = typing.get_args(WorkerRunner)
    assert list(params) == [str, str]  # (subgoal, context) —— 无授权载荷


@pytest.mark.asyncio
async def test_no_free_collab_entry_structural() -> None:
    """无游离协作入口(1.1 结构断言):agent 面模块只经任务编排进入,
    run_collab 入口要求宿主载荷且不提供任何独立发起端点;
    协作发起=任务步骤 step-start 决策(接线组合根),模块自身无 server 面。"""
    # 模块不定义 FastAPI 路由/APIRouter(游离端点无从谈起)
    import kongming_agent.collab_gen as cg

    assert not hasattr(cg, "router") and not hasattr(cg, "app")
    # 入口签名要求宿主载荷(payload 为必填 kwarg),无 task 步骤语义则无发起面
    sig = inspect.signature(cg.run_collab)
    assert "payload" in sig.parameters and "api" not in sig.parameters


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
