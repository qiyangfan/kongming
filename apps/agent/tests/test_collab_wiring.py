# pyright: reportPrivateUsage=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false
"""协作接线测试(091 组合根 collab_wiring + task_gen 协作分支,剧本化)。

覆盖:开账 fail-closed(开账被拒不执行,无游离协作面)、全链账本时序
(start→worker×N→finish)、逐 worker trace 父子关联与用量归集、Bot 人格注入
与工具求交(授权不提权)、task_gen 协作分支(决策驱动/受控上下文摘要/
聚合结论驱动步骤终态/步骤 token 增量)。
"""

import json
from typing import Any, cast

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

    def __init__(self, *, open_ok: bool = True, topology: dict[str, object] | None = None) -> None:
        self.open_ok = open_ok
        self.topology = topology or {"max_workers": 3, "dispatch": "serial"}
        self.events: list[tuple[str, dict[str, object]]] = []

    async def collab_start(self, **kw: object) -> dict[str, object] | None:
        if not self.open_ok:
            return None
        self.events.append(("start", dict(kw)))
        return {"run_id": "r1", "topology": dict(self.topology)}

    async def collab_worker_start(self, **kw: object) -> bool:
        self.events.append(("worker_start", dict(kw)))
        return True

    async def collab_worker_result(self, **kw: object) -> bool:
        self.events.append(("worker_result", dict(kw)))
        return True

    async def collab_turn(self, **kw: object) -> bool:
        self.events.append(("turn", dict(kw)))
        return True

    async def collab_finish(self, **kw: object) -> dict[str, object] | None:
        self.events.append(("finish", dict(kw)))
        return {"status": "DONE"}


def _topo(*subgoals: str) -> str:
    return json.dumps([{"subgoal": s} for s in subgoals], ensure_ascii=False)


# 显式钉死钳制上限:裸 Settings() 读真实环境,COLLAB_MAX_WORKERS/ROUNDS
# 漂移会让依赖缺省上限的断言(名册 3/两轮收口)在别的环境假红
_SETTINGS = Settings(collab_max_workers=3, collab_max_rounds=3)


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


# ── 圆桌分支(115)──


def _roundtable_cfg(
    participants: list[dict[str, object]] | None = None, max_rounds: int = 2
) -> dict[str, object]:
    """圆桌决策载荷假体(参与者=api 决策解析形态)。"""
    return {
        "kind": "roundtable",
        "max_rounds": max_rounds,
        "participants": participants
        or [
            {"role": "analyst", "bot_key": None, "bot": None},
            {"role": "skeptic", "bot_key": None, "bot": None},
        ],
    }


_ROUNDTABLE_TOPO = {
    "kind": "roundtable",
    "max_rounds": 2,
    "participants": [{"role": "analyst"}, {"role": "skeptic"}],
}


@pytest.mark.asyncio
async def test_roundtable_full_chain_ledger() -> None:
    """圆桌全链:开账 → 参与者 worker 行 → 逐轮发言/判定上账 → 参与者聚合
    回写 → 收口(共识 dissent=False);用量按参与者归集并回注总账。"""
    api = FakeCollabApi(topology=dict(_ROUNDTABLE_TOPO))
    # 覆写摘要带立场行(圆桌发言契约)
    from kongming_agent.llm import _USAGE_COLLECTOR

    async def react_speech(req: object) -> dict[str, object]:
        r = dict(req) if isinstance(req, dict) else {}
        collector = _USAGE_COLLECTOR.get()
        if collector is not None:
            collector.append({"total_tokens": 40})
        who = "A" if r.get("trace_id") == "tr-w1" else "B"
        return {
            "verdict": "PASS",
            "summary": f"{who}:结论成立,立场:支持",
            "sql": "SELECT 1",
        }

    llm = FakeLlm(['{"consensus": true, "reason": "结论一致"}'])
    runner = make_collab_runner(
        api=api,  # type: ignore[arg-type]
        llm=llm,
        settings=_SETTINGS,
        react_runner=react_speech,
        task_id="t1",
        tenant_key="default",
        datasource_key="default",
        user_id="u1",
        trace_root="tr",
        tools=[{"name": "sql_query"}],
    )
    out = await runner("s1", "评估异常成因", "任务目标:xxx", _roundtable_cfg())
    kinds = [k for k, _ in api.events]
    assert kinds == [
        "start",
        "worker_start",
        "worker_start",
        "turn",  # w1 speech
        "turn",  # w2 speech
        "turn",  # 主席判定(0 号位)
        "worker_result",
        "worker_result",
        "finish",
    ]
    assert str(out["verdict"]) == "PASS"
    assert out["consensus"] is True
    assert out["dissent"] is False
    verdict_turn = next(p for k, p in api.events if k == "turn" and p.get("worker_no") == 0)
    assert verdict_turn["kind"] == "verdict"
    assert cast("dict[str, object]", verdict_turn["content"])["consensus"] is True
    # 参与者聚合:末位发言 PASS → DONE,用量按参与者归集
    wr1 = next(p for k, p in api.events if k == "worker_result" and p.get("worker_no") == 1)
    assert wr1["status"] == "DONE"
    assert wr1["usage"] == {"llm_calls": 1, "total_tokens": 40}
    fin = api.events[-1][1]
    assert fin["verdict"] == "PASS"
    assert fin["dissent"] is False
    assert cast("dict[str, object]", out["usage"])["total_tokens"] == 80


@pytest.mark.asyncio
async def test_roundtable_zero_tool_fail_closed_quorum() -> None:
    """零工具参与者 fail-closed 不派发;剔除后有效名册不足 → 不开桌
    (无 worker 行无轮次),部分完成语义 + 保留意见披露。"""
    api = FakeCollabApi(topology=dict(_ROUNDTABLE_TOPO))
    react, reqs = _make_react_recorder()
    runner = make_collab_runner(
        api=api,  # type: ignore[arg-type]
        llm=FakeLlm([]),
        settings=_SETTINGS,
        react_runner=react,
        task_id="t1",
        tenant_key="default",
        datasource_key="default",
        user_id="u1",
        trace_root="tr",
        tools=[{"name": "sql_query"}],
    )
    cfg = _roundtable_cfg(
        participants=[
            {
                "role": "analyst",
                "bot_key": "empty_bot",
                "bot": {"bot_key": "empty_bot", "system_prompt": "x", "tools": []},
            },
            {"role": "skeptic", "bot_key": None, "bot": None},
        ]
    )
    out = await runner("s1", "评估", "", cfg)
    kinds = [k for k, _ in api.events]
    assert kinds == ["start", "finish"]  # 无 worker 行、无轮次
    assert str(out["verdict"]) == "ERROR"
    assert out["pending_subgoals"] == ["评估"]
    assert "参与者未派发" in str(out["reservation"]) and "fail-closed" in str(out["reservation"])
    fin = api.events[-1][1]
    assert fin["pending_count"] == 1
    assert reqs == []


@pytest.mark.asyncio
async def test_roundtable_zero_tool_excluded_disclosed_quorum_ok() -> None:
    """三声明一零工具:剔除后 2 人开桌,剔除事实如实披露于保留意见。"""
    topo: dict[str, object] = {
        "kind": "roundtable",
        "max_rounds": 1,
        "participants": [{"role": "analyst"}, {"role": "skeptic"}, {"role": "auditor"}],
    }
    api = FakeCollabApi(topology=topo)
    react, reqs = _make_react_recorder()
    runner = make_collab_runner(
        api=api,  # type: ignore[arg-type]
        llm=FakeLlm(['{"consensus": true, "reason": "一致"}']),
        settings=_SETTINGS,
        react_runner=react,
        task_id="t1",
        tenant_key="default",
        datasource_key="default",
        user_id="u1",
        trace_root="tr",
        tools=[{"name": "sql_query"}],
    )
    cfg = _roundtable_cfg(
        participants=[
            {"role": "analyst", "bot_key": None, "bot": None},
            {
                "role": "skeptic",
                "bot_key": "mute_bot",
                "bot": {"bot_key": "mute_bot", "system_prompt": "x", "tools": []},
            },
            {"role": "auditor", "bot_key": None, "bot": None},
        ],
        max_rounds=1,
    )
    out = await runner("s1", "评估", "", cfg)
    assert str(out["verdict"]) == "PASS"
    assert out["worker_count"] == 2
    assert "mute_bot" in str(out["reservation"])
    # Bot 名单为空集不放大为任务全量:该参与者被剔除而非放行
    assert all(dict(r)["tools"] != [] for r in reqs)


@pytest.mark.asyncio
async def test_roundtable_multi_round_and_open_fail_closed() -> None:
    """多轮:主席首轮不收敛 → 次轮再议;开账被拒 → 无任何轮次与 worker 行。"""
    api = FakeCollabApi(topology=dict(_ROUNDTABLE_TOPO))
    react, _ = _make_react_recorder()
    llm = FakeLlm(
        ['{"consensus": false, "reason": "仍有分歧"}', '{"consensus": true, "reason": "一致"}']
    )
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
    out = await runner("s1", "评估", "", _roundtable_cfg(max_rounds=2))
    assert out["consensus"] is True
    assert out["rounds_used"] == 2
    speech_turns = [p for k, p in api.events if k == "turn" and p.get("kind") == "speech"]
    assert [t["round_no"] for t in speech_turns] == [1, 1, 2, 2]

    closed = FakeCollabApi(open_ok=False)
    runner2 = make_collab_runner(
        api=closed,  # type: ignore[arg-type]
        llm=FakeLlm([]),
        settings=_SETTINGS,
        react_runner=react,
        task_id="t1",
        tenant_key="default",
        datasource_key="default",
        user_id="u1",
        trace_root="tr",
        tools=[{"name": "sql_query"}],
    )
    out2 = await runner2("s1", "评估", "", _roundtable_cfg())
    assert str(out2["verdict"]) == "ERROR"
    assert [k for k, _ in closed.events] == []


@pytest.mark.asyncio
async def test_roundtable_reservation_surfaces_on_pass_summary() -> None:
    """触顶未共识但执行证据全 PASS(PASS 收口):保留意见随摘要同行收口——
    步骤证据/运行账摘要可查,截断语境不许无痕(诚实收口口径)。"""
    api = FakeCollabApi(topology=dict(_ROUNDTABLE_TOPO))
    react, _ = _make_react_recorder()
    llm = FakeLlm(['{"consensus": false, "reason": "仍有分歧"}'])  # 恒不收敛 → 触顶
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
    out = await runner("s1", "评估", "", _roundtable_cfg(max_rounds=2))
    assert str(out["verdict"]) == "PASS"
    assert out["dissent"] is True
    reservation = str(out.get("reservation") or "")
    assert "触顶" in reservation
    assert f"#保留:{reservation}" in str(out.get("summary"))


@pytest.mark.asyncio
async def test_roundtable_roster_execution_cap_disclosed() -> None:
    """执行面部署上限双钳:名册超 settings.collab_max_workers 的条目按既有
    fail-closed 通道剔除并披露(不静默截断);剔除后足 2 人照常开桌。"""
    api = FakeCollabApi(topology=dict(_ROUNDTABLE_TOPO))
    react, _ = _make_react_recorder()
    llm = FakeLlm(['{"consensus": true, "reason": "一致"}'])
    runner = make_collab_runner(
        api=api,  # type: ignore[arg-type]
        llm=llm,
        settings=Settings(collab_max_workers=2, collab_max_rounds=3),
        react_runner=react,
        task_id="t1",
        tenant_key="default",
        datasource_key="default",
        user_id="u1",
        trace_root="tr",
        tools=[{"name": "sql_query"}],
    )
    out = await runner(
        "s1",
        "评估",
        "",
        _roundtable_cfg(
            participants=[
                {"role": "analyst", "bot_key": None, "bot": None},
                {"role": "skeptic", "bot_key": None, "bot": None},
                {"role": "auditor", "bot_key": None, "bot": None},
            ],
            max_rounds=2,
        ),
    )
    assert str(out["verdict"]) == "PASS"
    assert out["worker_count"] == 2  # 开桌规模=执行面上限,名册不被穿透
    reservation = str(out.get("reservation") or "")
    assert "auditor" in reservation and "超执行面部署上限" in reservation
    worker_nos = [p.get("worker_no") for k, p in api.events if k == "worker_start"]
    assert worker_nos == [1, 2]


@pytest.mark.asyncio
async def test_roundtable_abstain_participant_skipped_row() -> None:
    """全程弃权参与者:worker 行 SKIPPED(诚实弃权非失败),发言账为零。"""
    api = FakeCollabApi(topology=dict(_ROUNDTABLE_TOPO))
    from kongming_agent.llm import _USAGE_COLLECTOR

    async def react_mixed(req: object) -> dict[str, object]:
        r = dict(req) if isinstance(req, dict) else {}
        collector = _USAGE_COLLECTOR.get()
        if collector is not None:
            collector.append({"total_tokens": 10})
        if r.get("trace_id") == "tr-w2":
            return {"verdict": "PASS", "summary": "弃权", "sql": ""}
        return {"verdict": "PASS", "summary": "结论成立,立场:支持", "sql": "SELECT 1"}

    runner = make_collab_runner(
        api=api,  # type: ignore[arg-type]
        llm=FakeLlm(['{"consensus": true, "reason": "一致"}']),
        settings=_SETTINGS,
        react_runner=react_mixed,
        task_id="t1",
        tenant_key="default",
        datasource_key="default",
        user_id="u1",
        trace_root="tr",
        tools=[{"name": "sql_query"}],
    )
    out = await runner("s1", "评估", "", _roundtable_cfg(max_rounds=1))
    assert str(out["verdict"]) == "PASS"
    wr2 = next(p for k, p in api.events if k == "worker_result" and p.get("worker_no") == 2)
    assert wr2["status"] == "SKIPPED"
    assert cast("dict[str, object]", wr2["conclusion"])["abstained"] is True


@pytest.mark.asyncio
async def test_roundtable_unresolved_bot_binding_fail_closed() -> None:
    """声明 bot_key 而运行时解析失败(Bot 已删/停用):按零工具同径剔除
    (fail-closed,声明收窄不静默放宽为任务全量授权)。"""
    api = FakeCollabApi(topology=dict(_ROUNDTABLE_TOPO))
    react, reqs = _make_react_recorder()
    runner = make_collab_runner(
        api=api,  # type: ignore[arg-type]
        llm=FakeLlm([]),
        settings=_SETTINGS,
        react_runner=react,
        task_id="t1",
        tenant_key="default",
        datasource_key="default",
        user_id="u1",
        trace_root="tr",
        tools=[{"name": "sql_query"}],
    )
    cfg = _roundtable_cfg(
        participants=[
            # 声明了 bot_key 但 api 决策载荷解析为 None(运行时失效)
            {"role": "analyst", "bot_key": "dead_bot", "bot": None},
            {"role": "skeptic", "bot_key": None, "bot": None},
        ]
    )
    out = await runner("s1", "评估", "", cfg)
    kinds = [k for k, _ in api.events]
    assert kinds == ["start", "finish"]  # 有效名册仅 1 → 不开桌
    assert str(out["verdict"]) == "ERROR"
    assert "dead_bot" in str(out["reservation"])
    assert "解析失败" in str(out["reservation"])
    assert reqs == []  # 未派发任何参与者(不含任务全量工具的执行)


# ── 分工名册接线(collab-worker-roster):装配/注入/账本 ──


def _roster_cfg(workers: list[dict[str, object]]) -> dict[str, object]:
    return {"max_workers": 2, "workers": workers}


def _ok_bot(key: str, prompt: str, tool: str = "sql_query") -> dict[str, object]:
    return {"bot_key": key, "system_prompt": prompt, "tools": [tool]}


@pytest.mark.asyncio
async def test_collab_runner_roster_per_worker_assembly() -> None:
    """名册装配与逐成员注入:绑定解析失败/零工具 fail-closed 剔除并披露;
    各成员按其 Bot 注入人格、工具逐成员求交;账本逐 worker 落 bot_key 与
    成员标签前缀;治理语义(无 PASS 交叉)不受名册影响。"""
    api = FakeCollabApi(topology={"max_workers": 2, "dispatch": "serial"})
    react, reqs = _make_react_recorder(verdicts={"查用户": "ERROR"})
    llm = FakeLlm(
        [
            json.dumps(
                [
                    {"subgoal": "查收入", "worker": "财务"},
                    {"subgoal": "查用户", "worker": "运营"},
                ],
                ensure_ascii=False,
            )
        ]
    )
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
    cfg = _roster_cfg(
        [
            {"name": "财务", "bot_key": "fin", "bot": _ok_bot("fin", "财务人格")},
            {
                "name": "运营",
                "bot_key": "yunying",
                # 双工具名单:与任务授权求交后仍留 sql_query(求交收窄可见)
                "bot": {
                    "bot_key": "yunying",
                    "system_prompt": "运营人格",
                    "tools": ["sql_query", "report"],
                },
            },
            {"name": "ops", "bot_key": "ops", "bot": None},  # 解析失败 → 剔除
            {  # 求交空集 → 剔除
                "name": "空白",
                "bot_key": "blank",
                "bot": _ok_bot("blank", "x", tool="web_search"),
            },
        ]
    )
    out = await runner("s1", "经营指标分工", "", cfg)
    # 逐成员注入:人格按各自 Bot,工具=任务授权∩成员 Bot 名单(report 被求交掉)
    assert [str(r["question"]) for r in reqs] == ["查收入", "查用户"]
    assert str(reqs[0]["bot_system_prompt"]).startswith("财务人格")
    assert str(reqs[1]["bot_system_prompt"]).startswith("运营人格")
    for r in reqs:
        assert cast("list[object]", r["tools"]) == [{"name": "sql_query"}]
    # 账本:逐 worker bot_key 与成员标签前缀;剔除披露进保留意见
    starts = [payload for kind, payload in api.events if kind == "worker_start"]
    assert [str(s.get("bot_key")) for s in starts] == ["fin", "yunying"]
    assert str(starts[0]["subgoal"]).startswith("[财务]")
    assert str(starts[1]["subgoal"]).startswith("[运营]")
    assert "ops(绑定解析失败)" in str(out["reservation"])
    # 第 4 条目超执行面部署上限(cap=3)→ 截断披露(先于零工具判定)
    assert "blank(超执行面部署上限)" in str(out["reservation"])
    assert str(out["verdict"]) == "ERROR"  # 运营 ERROR → 交叉裁决不通过(语义照旧)


@pytest.mark.asyncio
async def test_collab_runner_roster_all_excluded_no_start() -> None:
    """全员剔除 → 不开工(ERROR 诚实收口),零 worker 执行、零派发行。"""
    api = FakeCollabApi(topology={"max_workers": 2, "dispatch": "serial"})
    react, reqs = _make_react_recorder()
    runner = make_collab_runner(
        api=api,  # type: ignore[arg-type]
        llm=FakeLlm(["[]"]),
        settings=_SETTINGS,
        react_runner=react,
        task_id="t1",
        tenant_key="default",
        datasource_key="default",
        user_id="u1",
        trace_root="tr",
        tools=[{"name": "sql_query"}],
    )
    cfg = _roster_cfg(
        [
            {"name": "a", "bot_key": "ra", "bot": None},
            {"name": "b", "bot_key": "rb", "bot": None},
        ]
    )
    out = await runner("s1", "目标", "", cfg)
    assert str(out["verdict"]) == "ERROR"
    assert reqs == []
    assert all(kind != "worker_start" for kind, _ in api.events)
    assert "有效名册成员不足" in str(out["reservation"])
    assert "ra(绑定解析失败)" in str(out["reservation"])
    finish = [payload for kind, payload in api.events if kind == "finish"]
    assert finish and str(finish[0].get("verdict")) == "ERROR"


@pytest.mark.asyncio
async def test_collab_runner_roster_execution_cap_double_clamp() -> None:
    """执行面部署上限双钳:api 声明钳制为先,agent 面按自身 Settings 复钳,
    超限成员剔除必披露(不静默截断)。"""
    api = FakeCollabApi(topology={"max_workers": 3, "dispatch": "serial"})
    react, reqs = _make_react_recorder()
    runner = make_collab_runner(
        api=api,  # type: ignore[arg-type]
        llm=FakeLlm([json.dumps([{"subgoal": "s1", "worker": "a"}], ensure_ascii=False)]),
        settings=Settings(collab_max_workers=2, collab_max_rounds=3),
        react_runner=react,
        task_id="t1",
        tenant_key="default",
        datasource_key="default",
        user_id="u1",
        trace_root="tr",
        tools=[{"name": "sql_query"}],
    )
    cfg = _roster_cfg(
        [
            {"name": "a", "bot_key": "ra", "bot": _ok_bot("ra", "A 人格")},
            {"name": "b", "bot_key": "rb", "bot": _ok_bot("rb", "B 人格")},
            {"name": "c", "bot_key": "rc", "bot": _ok_bot("rc", "C 人格")},
        ]
    )
    out = await runner("s1", "目标", "", cfg)
    assert len(reqs) == 1  # 执行面 cap=2:仅 2 成员在册,单子目标单派发
    assert "rc(超执行面部署上限)" in str(out["reservation"])


@pytest.mark.asyncio
async def test_collab_runner_roster_zero_tool_member_excluded() -> None:
    """零工具成员(名单与任务授权求交为空集)fail-closed 剔除并披露,
    不放大为不限制;其余成员照常派发。"""
    api = FakeCollabApi(topology={"max_workers": 2, "dispatch": "serial"})
    react, reqs = _make_react_recorder()
    runner = make_collab_runner(
        api=api,  # type: ignore[arg-type]
        llm=FakeLlm([json.dumps([{"subgoal": "s1", "worker": "a"}], ensure_ascii=False)]),
        settings=_SETTINGS,
        react_runner=react,
        task_id="t1",
        tenant_key="default",
        datasource_key="default",
        user_id="u1",
        trace_root="tr",
        tools=[{"name": "sql_query"}],
    )
    cfg = _roster_cfg(
        [
            {"name": "a", "bot_key": "ra", "bot": _ok_bot("ra", "A 人格")},
            {"name": "空集", "bot_key": "blank", "bot": _ok_bot("blank", "x", tool="web_search")},
        ]
    )
    out = await runner("s1", "目标", "", cfg)
    assert len(reqs) == 1
    assert "空集" in str(out["reservation"])  # 剔除标签=身份(name 优先口径)
