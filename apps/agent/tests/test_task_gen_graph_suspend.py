# pyright: reportArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportUnknownArgumentType=false, reportPrivateUsage=false
"""编排接线测试(add-graph-checkpoint):图内挂起上账、恢复提示透传、
恢复方式如实入步骤账。Fake 形态与 test_task_gen.py 同款。"""

# pyright: reportPrivateUsage=false

import json
from typing import Any

import pytest

from kongming_agent.settings import Settings
from kongming_agent.task_gen import run_task


def _plan_json(*goals: dict[str, object]) -> str:
    return "```json\n" + json.dumps(list(goals), ensure_ascii=False) + "\n```"


class FakeLlm:
    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)

    async def chat(self, *_a: object, **_kw: object) -> str:
        return self.replies.pop(0)


class FakeApi:
    def __init__(self, *, resume_hint: dict[str, object] | None = None) -> None:
        self.planned = False
        self.starts: list[str] = []
        self.results: list[dict[str, object]] = []
        self.suspends: list[dict[str, object]] = []
        self.finished: dict[str, object] | None = None
        self.resume_hint = resume_hint

    async def task_plan(
        self, *, task_id: str, tenant_key: str, steps: list[dict[str, object]]
    ) -> list[dict[str, object]] | None:
        if self.planned:
            return []
        self.planned = True
        return [
            {"id": f"s{i + 1}", "ord": i, "goal": str(s["goal"]), "require_confirm": False}
            for i, s in enumerate(steps)
        ]

    async def task_step_start(
        self, *, task_id: str, tenant_key: str, step_id: str, epoch: int = 0
    ) -> dict[str, object] | None:
        self.starts.append(step_id)
        out: dict[str, object] = {"decision": "execute"}
        if self.resume_hint is not None:
            out["resume_confirm"] = dict(self.resume_hint)
        return out

    async def task_step_suspended(self, **kw: object) -> dict[str, object] | None:
        self.suspends.append(dict(kw))
        return {"status": "pending"}

    async def task_step_result(self, **kw: object) -> dict[str, object] | None:
        self.results.append(dict(kw))
        return {"decision": "continue"}

    async def task_finish(self, **kw: object) -> bool:
        self.finished = dict(kw)
        return True

    async def task_steps(self, *, task_id: str, tenant_key: str) -> list[dict[str, object]] | None:
        return None


_PAYLOAD = {
    "task_id": "t-1",
    "epoch": 3,
    "tenant_key": "default",
    "trace_root": "tr",
    "datasource_key": "default",
    "user_id": "u-1",
    "goal": "发布公告",
    "budget": {},
    "tools": [],
}


@pytest.mark.asyncio
async def test_suspended_step_reports_and_awaits() -> None:
    """步骤挂起:上账回调携带线程与确认载荷;编排如实返回 AWAITING_APPROVAL,
    不回写步骤结果不收口终态(终态归属审批流)。"""
    llm = FakeLlm([_plan_json({"goal": "发布公告"})])
    api = FakeApi()

    async def runner(question: str, extra: dict[str, object]) -> dict[str, object]:
        assert extra["task_id"] == "t-1" and extra["step_id"] == "s1"
        return {
            "suspended": {
                "confirm_id": "cf-1",
                "tool_name": "create_system_announcement",
                "summary": "发布公告",
            }
        }

    out = await run_task(
        payload=dict(_PAYLOAD), llm=llm, api=api, settings=Settings(), step_runner=runner
    )
    assert out["final_status"] == "AWAITING_APPROVAL"
    assert api.finished is None  # 不收口
    assert len(api.results) == 0  # 步骤未终态
    rep = api.suspends[0]
    assert rep["confirm_id"] == "cf-1"
    assert rep["thread_id"] == "task:t-1:step:s1"
    assert rep["epoch"] == 3


@pytest.mark.asyncio
async def test_resume_hint_passes_to_runner_and_origin_recorded() -> None:
    """批准重派:恢复提示随执行器 extra 下发;恢复方式(snapshot)如实入步骤账。"""
    llm = FakeLlm([_plan_json({"goal": "发布公告"})])
    api = FakeApi(resume_hint={"decision": "approved", "confirm_id": "cf-1"})
    seen: dict[str, object] = {}

    async def runner(question: str, extra: dict[str, object]) -> dict[str, object]:
        seen.update(extra)
        return {"verdict": "PASS", "summary": "ok", "resume_origin": "snapshot"}

    out = await run_task(
        payload=dict(_PAYLOAD), llm=llm, api=api, settings=Settings(), step_runner=runner
    )
    assert out["final_status"] == "DONE"
    assert seen["resume_confirm"] == {"decision": "approved", "confirm_id": "cf-1"}
    cp = cast_any(api.results[0]["checkpoint"])
    assert cp["resume_origin"] == "snapshot"


@pytest.mark.asyncio
async def test_fresh_run_records_fresh_origin() -> None:
    """首次执行(无快照):恢复方式=fresh 如实入账,不出现谎称快照续跑。"""
    llm = FakeLlm([_plan_json({"goal": "发布公告"})])
    api = FakeApi()

    async def runner(question: str, extra: dict[str, object]) -> dict[str, object]:
        assert "resume_confirm" not in extra or extra["resume_confirm"] is None
        return {"verdict": "PASS", "summary": "ok", "resume_origin": "fresh"}

    out = await run_task(
        payload=dict(_PAYLOAD), llm=llm, api=api, settings=Settings(), step_runner=runner
    )
    assert out["final_status"] == "DONE"
    cp = cast_any(api.results[0]["checkpoint"])
    assert cp["resume_origin"] == "fresh"


def cast_any(v: object) -> dict[str, Any]:
    return cast_dict(v)


def cast_dict(v: object) -> dict[str, Any]:
    assert isinstance(v, dict)
    return v
