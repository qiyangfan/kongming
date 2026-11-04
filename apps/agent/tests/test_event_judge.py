"""事件立项判读测试:降级契约/输出清洗/端点行为/prompt 全角。

prompt 为全新撰写(非上游资产);遵守全角标点纪律(本文件断言锚定)。
降级契约:任何失败 → JudgeError/judged=False,api 侧按不可用口径 SKIPPED。
"""

# pyright: reportPrivateUsage=false
# 测试惯例:prompt 资产为模块私有常量,直引断言防手滑删规则(仓库约定,不改名)

import asyncio
from collections.abc import Coroutine
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from kongming_agent import deps
from kongming_agent.event_judge import JudgeError, judge_event
from kongming_agent.llm import LlmClient, LlmError
from kongming_agent.main import app

INTERNAL = {"X-Internal-Token": "test-internal-token"}

_SUMMARY = '{"event": "issues", "repo": "a/b", "ref": "7", "summary": "issue #7 数据异常"}'


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
        raise AssertionError("事件判读链路不应调用 embed")


def _run(coro: Coroutine[Any, Any, dict[str, object]]) -> dict[str, object]:
    return asyncio.run(coro)


def test_empty_summary_raises() -> None:
    with pytest.raises(JudgeError):
        _run(
            judge_event(
                source="github", summary="  ", goal_preview="x", llm=cast("LlmClient", StubLlm())
            )
        )


def test_llm_failure_raises_judge_error() -> None:
    with pytest.raises(JudgeError):
        _run(
            judge_event(
                source="github",
                summary=_SUMMARY,
                goal_preview="x",
                llm=cast("LlmClient", StubLlm(boom=True)),
            )
        )


def test_unparseable_or_incomplete_output_raises() -> None:
    bad = [
        "无法判断",
        '{"verdict": }',
        '{"reason":"无内容"}',
        '{"verdict":"defer","reason":"再议"}',
        '{"verdict":"skip"}',
    ]
    for reply in bad:
        with pytest.raises(JudgeError):
            _run(
                judge_event(
                    source="github",
                    summary=_SUMMARY,
                    goal_preview="x",
                    llm=cast("LlmClient", StubLlm(reply)),
                )
            )


def test_success_returns_verdict_and_material_flows_in() -> None:
    llm = StubLlm('{"verdict": "skip", "reason": "无实质内容"}')
    out = _run(
        judge_event(
            source="github", summary=_SUMMARY, goal_preview="核查异常", llm=cast("LlmClient", llm)
        )
    )
    assert out == {"verdict": "skip", "reason": "无实质内容"}
    assert llm.task_seen == "WATCHDOG"  # 同族单次判读共用性价比档路由
    assert "issue #7" in llm.calls[0][1]  # 事件摘要进入 prompt
    assert "核查异常" in llm.calls[0][1]  # 立项预览进入 prompt


# ============ prompt 全角纪律(与 test_prompts 同款锚定) ============


def _is_han(ch: str) -> bool:
    return "\u4e00" <= ch <= "\u9fff"


def test_prompt_full_width_punctuation() -> None:
    from kongming_agent import event_judge

    for prompt in (event_judge._SYSTEM_PROMPT, event_judge._USER_PROMPT_TEMPLATE):
        for i, ch in enumerate(prompt):
            if ch in (",", ".") and 0 < i < len(prompt) - 1:
                if _is_han(prompt[i - 1]) and _is_han(prompt[i + 1]):
                    pytest.fail(
                        f"prompt 出现汉字间半角标点 {ch!r}: {prompt[max(0, i - 6) : i + 6]}"
                    )


# ============ 端点行为 ============


def test_endpoint_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        deps, "get_llm", lambda: StubLlm('{"verdict": "create", "reason": "数据异常值得核查"}')
    )
    client = TestClient(app)
    r = client.post(
        "/internal/judge-event",
        json={"source": "github", "summary": _SUMMARY, "goal_preview": "核查异常"},
        headers=INTERNAL,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["judged"] is True
    assert body["verdict"] == "create"


def test_endpoint_llm_failure_degrades(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deps, "get_llm", lambda: StubLlm(boom=True))
    client = TestClient(app)
    r = client.post(
        "/internal/judge-event",
        json={"source": "github", "summary": _SUMMARY},
        headers=INTERNAL,
    )
    assert r.status_code == 200  # 降级不 5xx
    assert r.json() == {"judged": False, "verdict": "", "reason": ""}


def test_endpoint_requires_internal_token() -> None:
    client = TestClient(app)
    r = client.post("/internal/judge-event", json={"source": "github", "summary": _SUMMARY})
    assert r.status_code == 403
