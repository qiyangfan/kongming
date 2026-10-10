# pyright: reportPrivateUsage=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false
"""协作接线测试(091 组合根 collab_wiring + task_gen 协作分支,剧本化)。

覆盖:开账 fail-closed(开账被拒不执行,无游离协作面)、全链账本时序
(start→worker×N→finish)、逐 worker trace 父子关联与用量归集、Bot 人格注入
与工具求交(授权不提权)、task_gen 协作分支(决策驱动/受控上下文摘要/
聚合结论驱动步骤终态/步骤 token 增量)。
"""

import json
from typing import Any

import pytest

from kongming_agent.collab_wiring import make_collab_runner
from kongming_agent.settings import Settings
from kongming_agent.task_gen import run_task


class FakeLlm:
    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)

    async def chat(self, system: str, user: str, **_kw: object) -> str:
        return self.replies.pop(0)


class FakeCollabApi:
    """协作账本假体(时序/载荷记录;同构 ApiClient 协议)。"""

    def __init__(self, *, open_ok: bool = True) -> None:
        self.open_ok = open_ok
        self.events: list[tuple[str, dict[str, object]]] = []

    async def collab_start(self, **kw: object) -> dict[str, object] | None:
        if not self.open_ok:
            return None
        self.events.append(("start", dict(kw)))
        return {"run_id": "r1", "topology": {"max_workers": 3, "dispatch": "serial"}}

    async def collab_worker_start(self, **kw: object) -> bool:
        self.events.append(("worker_start", dict(kw)))
        return True

    async def collab_worker_result(self, **kw: object) -> bool:
        self.events.append(("worker_result", dict(kw)))
        return True

    async def collab_finish(self, **kw: object) -> dict[str, object] | None:
        self.events.append(("finish", dict(kw)))
        return {"status": "DONE"}


def _topo(*subgoals: str) -> str:
    return json.dumps([{"subgoal": s} for s in subgoals], ensure_ascii=False)


_SETTINGS = Settings()


def _make_react_recorder(
    verdicts: dict[str, str] | None = None, tokens: int = 50
) -> tuple[Any, list[dict[str, object]]]:
    """react 假体:记录请求载荷,按子目标给 verdict,统一 token 用量。"""
    reqs: list[dict[str, object]] = []
    verdict_map = verdicts or {}
    from kongming_agent.llm import _USAGE_COLLECTOR

    async def runner(req: object) -> dict[str, object]:
        r = dict(req) if isinstance(req, dict) else {}
        reqs.append(r)
        # 用量收集器(collab_wiring 经 set_usage_collector 挂载)
        collector = _USAGE_COLLECTOR.get()
        if collector is not None:
            collector.append({"total_tokens": tokens})
        subgoal = str(r.get("question") or "")
        verdict = verdict_map.get(subgoal, "PASS")
        return {"verdict": verdict, "summary": f"结论@{subgoal}", "sql": "SELECT 1"}

    return runner, reqs


def test_collab_runner_open_fail_closed() -> None:
    """开账被拒 → 不派发任何 worker(fail-closed,无游离协作面),
    如实 ERROR 收口并披露未执行子目标。"""

    async def _run() -> None:
        api = FakeCollabApi(open_ok=False)
        react, reqs = _make_react_recorder()
        runner = make_collab_runner(
            api=api,  # type: ignore[arg-type]
            llm=FakeLlm([_topo("甲", "乙")]),
            settings=_SETTINGS,
            react_runner=react,
            task_id="t1",
            tenant_key="default",
            datasource_key="default",
            user_id="u1",
            trace_root="tr",
            tools=[{"name": "sql_query"}],
        )
        out = await runner("s1", "交叉验证", "", {"max_workers": 2})
        assert str(out["verdict"]) == "ERROR"
        assert reqs == []  # 无 worker 执行
        assert all(kind != "worker_start" for kind, _ in api.events)

    import asyncio

    asyncio.run(_run())


@pytest.mark.asyncio
async def test_collab_runner_full_chain_ledger() -> None:
    """全链:开账 → worker 派发/回写(逐 worker trace=tr-wN、用量归集)→
    终态回写(裁决/分歧/未派发如实);返回聚合结论供步骤终态驱动。"""
    api = FakeCollabApi()
    react, reqs = _make_react_recorder()
    # 拆解 + 分歧核实仲裁(两 worker 结论互异 → 派核实 worker + 仲裁采信 A)
    llm = FakeLlm([_topo("甲", "乙"), '{"supports": "A"}'])
    runner = make_collab_runner(
        api=api,  # type: ignore[arg-type]
        llm=llm,
        settings=_SETTINGS,
        react_runner=react,
        task_id="t1",
        tenant_key="default",
        datasource_key="default",
        user_id="u1",
        trace_root="tr",
        tools=[{"name": "sql_query"}],
    )
    out = await runner("s1", "交叉验证", "任务目标:xxx", {})
    kinds = [k for k, _ in api.events]
    assert kinds == [
        "start",
        "worker_start",
        "worker_result",
        "worker_start",
        "worker_result",
        "worker_start",  # 核实 worker(分歧路径)
        "worker_result",
        "finish",
    ]
    assert str(out["verdict"]) == "PASS"
    # 逐 worker trace 父子关联口径:tr-w1 / tr-w2(同前缀)
    assert reqs[0]["trace_id"] == "tr-w1"
    assert reqs[1]["trace_id"] == "tr-w2"
    # worker 终态回写携带用量归集(按 worker)
    wr1 = api.events[2][1]
    assert wr1["usage"] == {"llm_calls": 1, "total_tokens": 50}
    assert wr1["status"] == "DONE"
    assert wr1["trace_id"] == "tr-w1"
    # 终态回写携带裁决摘要
    fin = api.events[-1][1]
    assert fin["verdict"] == "PASS"
    assert fin["dissent"] is False


@pytest.mark.asyncio
async def test_collab_runner_capped_worker_not_pass() -> None:
    """worker 终态带 error(轮次触顶兜底)→ PASS 不算数:worker 行 FAILED、
    结论 verdict ERROR(摘要回退 error 文案供归因),聚合裁决整体 ERROR。"""
    api = FakeCollabApi()

    async def react(req: object) -> dict[str, object]:
        r = dict(req) if isinstance(req, dict) else {}
        if str(r.get("question") or "") == "触顶子目标":
            return {
                "error": "已达工具调用轮次上限,请缩小问题范围后重试",
                "execution": {"columns": ["c"], "rows": [["x"]]},
            }
        return {"verdict": "PASS", "summary": "ok", "sql": "SELECT 1"}

    llm = FakeLlm([_topo("甲", "触顶子目标")])
    runner = make_collab_runner(
        api=api,  # type: ignore[arg-type]
        llm=llm,
        settings=_SETTINGS,
        react_runner=react,
        task_id="t1",
        tenant_key="default",
        datasource_key="default",
        user_id="u1",
        trace_root="tr",
        tools=[{"name": "sql_query"}],
    )
    out = await runner("s1", "协作", "", {})
    assert str(out["verdict"]) == "ERROR"  # 非全 PASS,不虚报
    wr = next(p for k, p in api.events if k == "worker_result" and p.get("worker_no") == 2)
    assert wr["status"] == "FAILED"
    concl = wr["conclusion"]
    assert isinstance(concl, dict) and concl["verdict"] == "ERROR"
    assert "轮次上限" in str(concl["summary"])
    fin = api.events[-1][1]
    assert fin["verdict"] == "ERROR"


@pytest.mark.asyncio
async def test_collab_runner_bot_persona_and_tool_narrowing() -> None:
    """Bot 绑定:人格注入(bot_system_prompt 含人格+上下文摘要)、
    工具集 = 任务授权工具 ∩ Bot 工具名单(授权不提权);bot_key 随账本。"""
    api = FakeCollabApi()
    react, reqs = _make_react_recorder()
    runner = make_collab_runner(
        api=api,  # type: ignore[arg-type]
        llm=FakeLlm([_topo("甲")]),
        settings=_SETTINGS,
        react_runner=react,
        task_id="t1",
        tenant_key="default",
        datasource_key="default",
        user_id="u1",
        trace_root="tr",
        tools=[{"name": "sql_query"}, {"name": "web_search"}, {"name": "write_tool"}],
    )
    cfg: dict[str, object] = {
        "max_workers": 1,
        "bot": {
            "bot_key": "fin_analyst",
            "system_prompt": "你是财务分析专家",
            "tools": ["sql_query"],  # Bot 子集:web_search/write_tool 不可达
        },
    }
    out = await runner("s1", "财务口径核对", "已有上下文:Q1 数据", cfg)
    assert str(out["verdict"]) == "PASS"
    r = reqs[0]
    assert r["tools"] == [{"name": "sql_query"}]  # 求交后仅剩 Bot 子集
    prompt = str(r["bot_system_prompt"])
    assert "你是财务分析专家" in prompt
    assert "已有上下文:Q1 数据" in prompt  # 上下文摘要=系统提示附加段(D2)
    ws = api.events[1][1]
    assert ws["bot_key"] == "fin_analyst"


@pytest.mark.asyncio
async def test_task_gen_collab_branch_end_to_end() -> None:
    """task_gen 协作分支:step-start 决策带 collab → 协作执行器接管
    (受控上下文摘要含已完成步结论);聚合结论驱动步骤终态(PASS→DONE
    携协作语义证据;ERROR→FAILED 附因);协作步 token 增量照常入步账。"""
    llm = FakeLlm([json.dumps([{"goal": "先查数"}, {"goal": "交叉验证"}])])

    class FakeApi:
        def __init__(self) -> None:
            self.results: list[dict[str, object]] = []
            self.collab_calls: list[tuple[str, str, str, dict[str, object]]] = []
            self.finished: dict[str, object] | None = None

        async def task_plan(self, **kw: object) -> list[dict[str, object]] | None:
            return [
                {"id": "a", "ord": 0, "goal": "先查数", "require_confirm": False},
                {"id": "b", "ord": 1, "goal": "交叉验证", "require_confirm": False},
            ]

        async def task_step_start(
            self, *, task_id: str, tenant_key: str, step_id: str, epoch: int = 0
        ) -> dict[str, object] | None:
            if step_id == "b":
                return {
                    "decision": "execute",
                    "collab": {"max_workers": 2, "dispatch": "serial", "critic": False},
                }
            return {"decision": "execute"}

        async def task_step_result(self, **kw: object) -> dict[str, object] | None:
            self.results.append(dict(kw))
            return {"decision": "continue"}

        async def task_finish(self, **kw: object) -> bool:
            self.finished = dict(kw)
            return True

        async def task_steps(self, **kw: object) -> list[dict[str, object]] | None:
            return []

    api = FakeApi()
    collab_inputs: list[tuple[str, str, str, dict[str, object]]] = []

    async def collab_runner(
        step_id: str, goal: str, context: str, cfg: dict[str, object]
    ) -> dict[str, object]:
        collab_inputs.append((step_id, goal, context, cfg))
        # 模拟聚合结论(PASS,含 worker/分歧语义披露)
        return {
            "verdict": "PASS",
            "summary": json.dumps([{"subgoal": "甲", "verdict": "PASS"}], ensure_ascii=False),
            "conclusions": [{"subgoal": "甲", "verdict": "PASS"}],
            "pending_subgoals": [],
            "dissent": False,
            "worker_count": 1,
            "reservation": None,
        }

    async def step_runner(question: str, extra: dict[str, object]) -> dict[str, object]:
        return {"verdict": "PASS", "summary": "基础数据就绪", "sql": "SELECT 1"}

    out = await run_task(
        payload={
            "task_id": "t1",
            "trace_root": "tr",
            "tenant_key": "default",
            "datasource_key": "default",
            "user_id": "u1",
            "goal": "综合分析",
            "budget": {},
            "tools": [],
        },
        llm=llm,
        api=api,  # type: ignore[arg-type]
        settings=_SETTINGS,
        step_runner=step_runner,
        collab_runner=collab_runner,
    )
    assert out["final_status"] == "DONE"
    # 协作执行器收到:step_id/子目标/受控上下文摘要(含前步结论)/决策协作载荷
    assert len(collab_inputs) == 1
    sid, goal, digest, cfg = collab_inputs[0]
    assert (sid, goal) == ("b", "交叉验证")
    assert "基础数据就绪" in digest  # 受控摘要含已完成结论(D2)
    assert cfg["max_workers"] == 2
    # 步骤终态由聚合结论驱动:协作步 DONE,证据带协作语义披露
    collab_result = api.results[1]
    assert collab_result["status"] == "DONE"
    ev = collab_result["evidence"]
    assert isinstance(ev, dict) and ev["trace_id"] == "tr"
    assert ev["collab"]["worker_count"] == 1
    assert ev["collab"]["dissent"] is False


@pytest.mark.asyncio
async def test_task_gen_collab_failed_honest() -> None:
    """协作裁决 ERROR → 步骤 FAILED 附因(未派发清单/保留意见如实);任务 FAILED。"""
    llm = FakeLlm([json.dumps([{"goal": "协作步"}])])

    class FakeApi:
        def __init__(self) -> None:
            self.results: list[dict[str, object]] = []
            self.finished: dict[str, object] | None = None

        async def task_plan(self, **kw: object) -> list[dict[str, object]] | None:
            return [{"id": "a", "ord": 0, "goal": "协作步", "require_confirm": False}]

        async def task_step_start(
            self, *, task_id: str, tenant_key: str, step_id: str, epoch: int = 0
        ) -> dict[str, object] | None:
            return {"decision": "execute", "collab": {"max_workers": 1}}

        async def task_step_result(self, **kw: object) -> dict[str, object] | None:
            self.results.append(dict(kw))
            return {"decision": "continue"}

        async def task_finish(self, **kw: object) -> bool:
            self.finished = dict(kw)
            return True

        async def task_steps(self, **kw: object) -> list[dict[str, object]] | None:
            return []

    async def collab_runner(
        step_id: str, goal: str, context: str, cfg: dict[str, object]
    ) -> dict[str, object]:
        return {
            "verdict": "ERROR",
            "summary": "[]",
            "conclusions": [],
            "pending_subgoals": ["预算不足未派发"],
            "dissent": False,
            "worker_count": 0,
            "reservation": "预算继承不足",
        }

    async def step_runner(question: str, extra: dict[str, object]) -> dict[str, object]:
        raise AssertionError("协作步不应走单步执行器")

    api = FakeApi()
    out = await run_task(
        payload={
            "task_id": "t1",
            "trace_root": "tr",
            "tenant_key": "default",
            "datasource_key": "default",
            "user_id": "u1",
            "goal": "g",
            "budget": {},
            "tools": [],
        },
        llm=llm,
        api=api,  # type: ignore[arg-type]
        settings=_SETTINGS,
        step_runner=step_runner,
        collab_runner=collab_runner,
    )
    assert out["final_status"] == "FAILED"
    assert api.results[0]["status"] == "FAILED"
    reason = str(api.results[0]["fail_reason"])
    assert "协作裁决 ERROR" in reason
    assert "预算不足未派发" in reason
    assert isinstance(api.finished, dict)  # finish 未被调到时显式失败而非 TypeError
    assert str(api.finished["status"]) == "FAILED"
