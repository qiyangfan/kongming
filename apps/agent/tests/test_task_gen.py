# pyright: reportArgumentType=false, reportPrivateUsage=false
"""任务编排测试(090 task-kernel,剧本化:mock LLM/mock ApiClient/mock step_runner)。

覆盖:规划→逐步执行→DONE 主链、证据驱动(无证据 FAILED)、审批挂起、
预算 PAUSED、plan 409 续跑。FakeApi 鸭子型注入(run_task 形参声明为具体
ApiClient,剧本化测试按同构协议假体——与 test_task.py 首行 pragma 同约定)。
"""

import json

import pytest

from kongming_agent.settings import Settings
from kongming_agent.task_gen import run_task


class FakeLlm:
    def __init__(self, replies: list[object]) -> None:
        self.replies = list(replies)
        self.calls = 0

    async def chat(self, system: str, user: str, **_kw: object) -> str:
        self.calls += 1
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return str(reply)


class FakeApi:
    def __init__(self, *, step_ids: list[str] | None = None) -> None:
        self.step_ids = step_ids or [f"s{i}" for i in range(1, 10)]
        self.planned = False
        self.starts: list[str] = []
        self.results: list[dict[str, object]] = []
        self.finished: dict[str, object] | None = None

    async def task_plan(
        self, *, task_id: str, tenant_key: str, steps: list[dict[str, object]]
    ) -> list[dict[str, object]] | None:
        if self.planned:
            return []  # 已在账 → 续跑读既有
        self.planned = True
        return [
            {
                "id": self.step_ids[i],
                "ord": i,
                "goal": str(s["goal"]),
                "require_confirm": bool(s.get("require_confirm")),
            }
            for i, s in enumerate(steps)
        ]

    async def task_step_start(
        self, *, task_id: str, tenant_key: str, step_id: str
    ) -> dict[str, object] | None:
        self.starts.append(step_id)
        return {"decision": "execute"}

    async def task_step_result(self, **kw: object) -> dict[str, object] | None:
        self.results.append(dict(kw))
        return {"decision": "continue"}

    async def task_finish(
        self, *, task_id: str, tenant_key: str, status: str, fail_reason: str | None
    ) -> bool:
        self.finished = {"status": status, "fail_reason": fail_reason}
        return True

    async def task_steps(self, *, task_id: str, tenant_key: str) -> list[dict[str, object]] | None:
        return []


def _plan_json(*steps: dict[str, object]) -> str:
    return json.dumps(
        [
            {"goal": str(s["goal"]), "require_confirm": bool(s.get("require_confirm"))}
            for s in steps
        ],
        ensure_ascii=False,
    )


_PAYLOAD: dict[str, object] = {
    "task_id": "t1",
    "trace_root": "tr",
    "tenant_key": "default",
    "datasource_key": "default",
    "user_id": "u1",
    "goal": "梳理销售异常",
    "budget": {},
    "tools": [],
}


@pytest.mark.asyncio
async def test_plan_execute_finish_done() -> None:
    """规划→逐步执行(每步一次 runner)→任务终态 DONE(编排回调完成)。"""
    llm = FakeLlm([_plan_json({"goal": "s1"}, {"goal": "s2"})])
    api = FakeApi(step_ids=["a", "b"])

    async def runner(question: str, extra: dict[str, object]) -> dict[str, object]:
        return {"verdict": "PASS", "summary": f"ok {question}", "sql": "SELECT 1"}

    out = await run_task(
        payload=dict(_PAYLOAD), llm=llm, api=api, settings=Settings(), step_runner=runner
    )
    assert out["final_status"] == "DONE"
    assert api.starts == ["a", "b"]
    assert len(api.results) == 2
    # 证据驱动:PASS → DONE 且证据含 trace + sql
    assert api.results[0]["status"] == "DONE"
    ev = api.results[0]["evidence"]
    assert isinstance(ev, dict)
    assert ev["trace_id"] == "tr" and ev["sql"] == "SELECT 1"


@pytest.mark.asyncio
async def test_no_tool_evidence_failed() -> None:
    """无工具执行证据的步骤(NO_DATA/纯文本)→ 步骤 FAILED(有证据才完成);
    任务收口同样 FAILED——全部 DONE 方可 DONE,不以全称完成措辞覆盖失败步骤。"""
    llm = FakeLlm([_plan_json({"goal": "s1"})])
    api = FakeApi(step_ids=["a"])

    async def runner(question: str, extra: dict[str, object]) -> dict[str, object]:
        return {"verdict": "NO_DATA", "summary": "查无数据"}

    out = await run_task(
        payload=dict(_PAYLOAD), llm=llm, api=api, settings=Settings(), step_runner=runner
    )
    assert api.results[0]["status"] == "FAILED"
    assert out["final_status"] == "FAILED"
    assert api.finished is not None and api.finished["status"] == "FAILED"
    assert "部分步骤未完成" in str(api.finished["fail_reason"])


@pytest.mark.asyncio
async def test_require_confirm_awaits() -> None:
    """需确认步骤:start 返回 await_approval → 编排挂起(不执行该步)。"""
    llm = FakeLlm([_plan_json({"goal": "高危", "require_confirm": True})])

    class _AwaitApi(FakeApi):
        async def task_step_start(self, **kw: object) -> dict[str, object] | None:
            self.starts.append(str(kw.get("step_id")))
            return {"decision": "await_approval"}

    api = _AwaitApi(step_ids=["a"])

    async def runner(question: str, extra: dict[str, object]) -> dict[str, object]:
        raise AssertionError("审批挂起不应执行步骤")

    out = await run_task(
        payload=dict(_PAYLOAD), llm=llm, api=api, settings=Settings(), step_runner=runner
    )
    assert out["final_status"] == "AWAITING_APPROVAL"
    assert api.results == []


@pytest.mark.asyncio
async def test_budget_pause_stops_loop() -> None:
    """预算:start 返回 pause → 编排停止(不执行剩余步骤)。"""
    llm = FakeLlm([_plan_json({"goal": "s1"}, {"goal": "s2"}, {"goal": "s3"})])

    class _PauseApi(FakeApi):
        async def task_step_start(self, **kw: object) -> dict[str, object] | None:
            self.starts.append(str(kw.get("step_id")))
            if len(self.starts) >= 3:
                return {"decision": "pause", "reason": "步数预算耗尽"}
            return {"decision": "execute"}

    api2 = _PauseApi(step_ids=["a", "b", "c"])

    async def runner(question: str, extra: dict[str, object]) -> dict[str, object]:
        return {"verdict": "PASS", "summary": "ok"}

    out = await run_task(
        payload=dict(_PAYLOAD), llm=llm, api=api2, settings=Settings(), step_runner=runner
    )
    assert out["final_status"] == "PAUSED"
    assert len(api2.results) == 2  # 第三步未执行


@pytest.mark.asyncio
async def test_empty_plan_fails_honestly() -> None:
    """规划产出空清单 → 计划无账可续,如实 FAILED(零步骤假 DONE 已废除)。"""
    llm = FakeLlm(["[]"])
    api = FakeApi(step_ids=[])

    async def runner(question: str, extra: dict[str, object]) -> dict[str, object]:
        raise AssertionError("无步骤不应执行")

    out = await run_task(
        payload=dict(_PAYLOAD), llm=llm, api=api, settings=Settings(), step_runner=runner
    )
    assert out["final_status"] == "FAILED"
    assert api.finished is not None and api.finished["status"] == "FAILED"


@pytest.mark.asyncio
async def test_resume_409_reads_existing_steps() -> None:
    """plan 409(续跑)→ 读既有步骤账本 → 已终态步骤 skip,未决步骤执行。"""

    class _ResumeApi(FakeApi):
        async def task_plan(
            self, *, task_id: str, tenant_key: str, steps: list[dict[str, object]]
        ) -> list[dict[str, object]] | None:
            return []  # 409 续跑口径

        async def task_steps(
            self, *, task_id: str, tenant_key: str
        ) -> list[dict[str, object]] | None:
            return [
                {"id": "x1", "goal": "s1", "require_confirm": False, "status": "DONE"},
                {"id": "x2", "goal": "s2", "require_confirm": False, "status": "PENDING"},
            ]

        async def task_step_start(
            self, *, task_id: str, tenant_key: str, step_id: str
        ) -> dict[str, object] | None:
            self.starts.append(step_id)
            if step_id == "x1":
                return {"decision": "skip", "reason": "步骤状态 DONE 不可执行"}
            return {"decision": "execute"}

    llm = FakeLlm([_plan_json({"goal": "s1"}, {"goal": "s2"})])
    api = _ResumeApi()

    async def runner(question: str, extra: dict[str, object]) -> dict[str, object]:
        return {"verdict": "PASS", "summary": "ok"}

    out = await run_task(
        payload=dict(_PAYLOAD), llm=llm, api=api, settings=Settings(), step_runner=runner
    )
    assert out["final_status"] == "DONE"
    assert api.starts == ["x1", "x2"]
    assert len(api.results) == 1  # 已 DONE 步骤不重跑


@pytest.mark.asyncio
async def test_abort_decision_stops_without_finish() -> None:
    """控制面 abort(任务已被用户取消/终态)→ 如实返回现态,不再回写终态。"""
    llm = FakeLlm([_plan_json({"goal": "s1"}, {"goal": "s2"})])

    class _AbortApi(FakeApi):
        async def task_step_start(
            self, *, task_id: str, tenant_key: str, step_id: str
        ) -> dict[str, object] | None:
            self.starts.append(step_id)
            if len(self.starts) >= 2:
                return {
                    "decision": "abort",
                    "task_status": "CANCELLED",
                    "reason": "任务已 CANCELLED,不再执行剩余步骤",
                }
            return {"decision": "execute"}

    api = _AbortApi(step_ids=["a", "b"])

    async def runner(question: str, extra: dict[str, object]) -> dict[str, object]:
        return {"verdict": "PASS", "summary": "ok"}

    out = await run_task(
        payload=dict(_PAYLOAD), llm=llm, api=api, settings=Settings(), step_runner=runner
    )
    assert out["final_status"] == "CANCELLED"
    assert api.finished is None  # 终态归属控制面,编排不得再回写


@pytest.mark.asyncio
async def test_finish_rejection_reports_failed_honestly() -> None:
    """终态回写被控制面拒绝 → 不宣称完成,如实上报 FAILED(未确认)。"""
    llm = FakeLlm([_plan_json({"goal": "s1"})])

    class _RejectApi(FakeApi):
        async def task_finish(
            self, *, task_id: str, tenant_key: str, status: str, fail_reason: str | None
        ) -> bool:
            self.finished = {"status": status, "fail_reason": fail_reason}
            return False  # 控制面 409(状态冲突)

    api = _RejectApi(step_ids=["a"])

    async def runner(question: str, extra: dict[str, object]) -> dict[str, object]:
        return {"verdict": "PASS", "summary": "ok"}

    out = await run_task(
        payload=dict(_PAYLOAD), llm=llm, api=api, settings=Settings(), step_runner=runner
    )
    assert out["final_status"] == "FAILED"
    assert "未确认" in str(out.get("error"))


@pytest.mark.asyncio
async def test_tokens_reported_per_step_increment() -> None:
    """token 上报=每步增量(控制面负责累加;上报累计值会 O(n²) 膨胀账本)。"""
    from kongming_agent.llm import _USAGE_COLLECTOR

    llm = FakeLlm([_plan_json({"goal": "s1"}, {"goal": "s2"})])
    api = FakeApi(step_ids=["a", "b"])

    async def runner(question: str, extra: dict[str, object]) -> dict[str, object]:
        collector = _USAGE_COLLECTOR.get()
        if collector is not None:
            collector.append({"total_tokens": 10})
        return {"verdict": "PASS", "summary": "ok"}

    out = await run_task(
        payload=dict(_PAYLOAD), llm=llm, api=api, settings=Settings(), step_runner=runner
    )
    assert out["final_status"] == "DONE"
    assert [r["tokens_used"] for r in api.results] == [10, 10]


@pytest.mark.asyncio
async def test_llm_error_fails_task() -> None:
    """规划 LLM 异常 → FAILED 终态回调。"""
    from kongming_agent.llm import LlmError

    llm = FakeLlm([LlmError("down")])
    api = FakeApi(step_ids=["a"])

    async def runner(question: str, extra: dict[str, object]) -> dict[str, object]:
        raise AssertionError("不应执行")

    out = await run_task(
        payload=dict(_PAYLOAD), llm=llm, api=api, settings=Settings(), step_runner=runner
    )
    assert out["final_status"] == "FAILED"
    assert api.finished is not None and api.finished["status"] == "FAILED"


@pytest.mark.asyncio
async def test_reject_decision_carries_reason() -> None:
    """证据拒绝是决策(200 携带):拒绝原因进任务 fail_reason,分支可达。"""

    class _RejectStepApi(FakeApi):
        async def task_step_result(self, **kw: object) -> dict[str, object] | None:
            self.results.append(dict(kw))
            return {"decision": "reject:DONE 缺证据引用(有证据才完成)"}

    llm = FakeLlm([_plan_json({"goal": "s1"})])
    api = _RejectStepApi(step_ids=["a"])

    async def runner(question: str, extra: dict[str, object]) -> dict[str, object]:
        return {"verdict": "PASS", "summary": "ok"}

    out = await run_task(
        payload=dict(_PAYLOAD), llm=llm, api=api, settings=Settings(), step_runner=runner
    )
    assert out["final_status"] == "FAILED"
    assert api.finished is not None
    assert str(api.finished["fail_reason"]).startswith("reject:")


@pytest.mark.asyncio
async def test_malformed_plan_fails_honestly() -> None:
    """规划输出非 JSON(畸形)→ 如实 FAILED 附原因,不裸 500 劣化归因。"""
    llm = FakeLlm(["模型跑题了,这不是 JSON"])
    api = FakeApi(step_ids=["a"])

    async def runner(question: str, extra: dict[str, object]) -> dict[str, object]:
        raise AssertionError("规划畸形不应执行步骤")

    out = await run_task(
        payload=dict(_PAYLOAD), llm=llm, api=api, settings=Settings(), step_runner=runner
    )
    assert out["final_status"] == "FAILED"
    assert api.finished is not None
    assert "规划输出畸形" in str(api.finished["fail_reason"])
    assert api.results == []
