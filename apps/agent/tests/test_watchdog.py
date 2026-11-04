"""任务停滞判读测试:降级契约/输出清洗/截断/端点行为/prompt 全角。

prompt 为全新撰写(非上游资产);遵守全角标点纪律(本文件断言锚定)。
降级契约:任何失败 → JudgeError/judged=False,api 侧落标记事件走机械兜底。
"""

# pyright: reportPrivateUsage=false
# 测试惯例:prompt 资产为模块私有常量,直引断言防手滑删规则(仓库约定,不改名)

import asyncio
from collections.abc import Coroutine
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from kongming_agent import deps
from kongming_agent.llm import LlmClient, LlmError
from kongming_agent.main import app
from kongming_agent.watchdog import JudgeError, judge_stagnation

INTERNAL = {"X-Internal-Token": "test-internal-token"}

_GOAL = "梳理华东区近 30 天销售异常并给出归因"
_TAIL: list[dict[str, object]] = [{"at": "2026-01-01T00:00:00+00:00", "kind": "step_started"}]


class StubLlm:
    """固定应答/故障替身;记录调用供断言。"""

    def __init__(self, reply: str = "", *, boom: bool = False) -> None:
        self.reply = reply
        self.boom = boom
        self.calls: list[tuple[str, str]] = []
        self.task_seen: object = None

    async def chat(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        **kw: object,
    ) -> str:
        self.calls.append((system, user))
        self.task_seen = kw.get("task")
        if self.boom:
            raise LlmError("connect failed")
        return self.reply

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise AssertionError("停滞判读链路不应调用 embed")


def _run(coro: Coroutine[Any, Any, dict[str, object]]) -> dict[str, object]:
    return asyncio.run(coro)


# ============ judge_stagnation 契约 ============


def test_empty_goal_raises() -> None:
    with pytest.raises(JudgeError):
        _run(
            judge_stagnation(
                goal="   ", events_tail=_TAIL, stale_sec=300, llm=cast("LlmClient", StubLlm())
            )
        )


def test_llm_failure_raises_judge_error() -> None:
    with pytest.raises(JudgeError):
        _run(
            judge_stagnation(
                goal=_GOAL,
                events_tail=_TAIL,
                stale_sec=300,
                llm=cast("LlmClient", StubLlm(boom=True)),
            )
        )


def test_unparseable_or_incomplete_output_raises() -> None:
    bad = [
        "抱歉,我无法判断",  # 非 JSON
        '{"verdict": }',  # 坏 JSON
        '{"reason":"无产出"}',  # 缺 verdict
        '{"verdict":"paused","reason":"挂起了"}',  # verdict 越界
        '{"verdict":"stagnant"}',  # 缺 reason
        '{"verdict":"stagnant","reason":"   "}',  # 空 reason
    ]
    for reply in bad:
        with pytest.raises(JudgeError):
            _run(
                judge_stagnation(
                    goal=_GOAL,
                    events_tail=_TAIL,
                    stale_sec=300,
                    llm=cast("LlmClient", StubLlm(reply)),
                )
            )


def test_success_returns_verdict_and_uses_watchdog_task() -> None:
    llm = StubLlm('```json\n{"verdict": "stagnant", "reason": "事件线 5 分钟无新进展"}\n```')
    out = _run(
        judge_stagnation(goal=_GOAL, events_tail=_TAIL, stale_sec=300, llm=cast("LlmClient", llm))
    )
    assert out == {"verdict": "stagnant", "reason": "事件线 5 分钟无新进展"}
    assert llm.task_seen == "WATCHDOG"  # 任务路由(可定向性价比档)
    assert _GOAL in llm.calls[0][1]  # 目标进入 prompt
    assert "step_started" in llm.calls[0][1]  # 事件尾进入 prompt


def test_tail_capped_at_eight_events() -> None:
    tail: list[dict[str, object]] = [
        {"at": f"2026-01-01T00:0{i}:00+00:00", "kind": f"ev{i}"} for i in range(12)
    ]
    llm = StubLlm('{"verdict":"progressing","reason":"按序推进"}')
    _run(judge_stagnation(goal=_GOAL, events_tail=tail, stale_sec=300, llm=cast("LlmClient", llm)))
    assert "ev4" in llm.calls[0][1]  # 只保留最近 8 条(第 5 条起)
    assert "ev3" not in llm.calls[0][1]  # 更早事件被截断
    assert "ev11" in llm.calls[0][1]  # 最新事件保留


# ============ prompt 全角纪律(与 test_prompts 同款锚定) ============


def _is_han(ch: str) -> bool:
    return "\u4e00" <= ch <= "\u9fff"


def test_prompt_full_width_punctuation() -> None:
    from kongming_agent import watchdog

    for prompt in (watchdog._SYSTEM_PROMPT, watchdog._USER_PROMPT_TEMPLATE):
        for i, ch in enumerate(prompt):
            # 汉字间不得出现半角逗号/句号(全角纪律,test_prompts 同款形态检查)
            if ch in (",", ".") and 0 < i < len(prompt) - 1:
                if _is_han(prompt[i - 1]) and _is_han(prompt[i + 1]):
                    pytest.fail(
                        f"prompt 出现汉字间半角标点 {ch!r}: {prompt[max(0, i - 6) : i + 6]}"
                    )


# ============ 端点行为 ============


def test_endpoint_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        deps, "get_llm", lambda: StubLlm('{"verdict": "progressing", "reason": "按序推进"}')
    )
    client = TestClient(app)
    r = client.post(
        "/internal/judge-stagnation",
        json={"goal": _GOAL, "events": _TAIL, "stale_sec": 300},
        headers=INTERNAL,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["judged"] is True
    assert body["verdict"] == "progressing"
    assert body["reason"] == "按序推进"


def test_endpoint_llm_failure_degrades(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deps, "get_llm", lambda: StubLlm(boom=True))
    client = TestClient(app)
    r = client.post(
        "/internal/judge-stagnation",
        json={"goal": _GOAL, "events": _TAIL, "stale_sec": 300},
        headers=INTERNAL,
    )
    assert r.status_code == 200  # 降级不 5xx
    assert r.json() == {"judged": False, "verdict": "", "reason": ""}


def test_endpoint_requires_internal_token() -> None:
    client = TestClient(app)
    r = client.post(
        "/internal/judge-stagnation",
        json={"goal": _GOAL, "events": _TAIL, "stale_sec": 300},
    )
    assert r.status_code == 403
