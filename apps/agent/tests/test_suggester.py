"""对话冷启动建议问题测试:产出清洗/降级契约/端点行为/prompt 全角。

prompt 为 全新撰写(非上游资产);遵守全角标点纪律(本文件断言锚定)。
降级契约:任何失败 → SuggestError/generated=False,api 侧收敛空清单不落缓存。
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
from kongming_agent.suggester import (
    SuggestedQuestions,
    SuggestError,
    suggest_questions,
)

INTERNAL = {"X-Internal-Token": "test-internal-token"}

_DIGEST = "demo.goods —— 商品表\n  - id uuid\n  - name text"


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
        raise AssertionError("建议生成链路不应调用 embed")


def _run(coro: Coroutine[Any, Any, list[str]]) -> list[str]:
    return asyncio.run(coro)


# ============ 产出清洗 ============


def test_clean_folds_newlines_dedupes_and_caps() -> None:
    out = SuggestedQuestions.model_validate(
        {
            "questions": [
                "库存里\n有多少种商品?",
                "  库存里 有多少种商品?  ",  # 折叠后与前句重复
                "x" * 80,  # 超长丢弃
                "销量前五的类别",
                "",
                "   ",
            ]
        }
    )
    assert out.questions == ["库存里 有多少种商品?", "销量前五的类别"]


def test_clean_caps_count() -> None:
    out = SuggestedQuestions.model_validate({"questions": [f"问题{i}" for i in range(10)]})
    assert len(out.questions) == 5


# ============ suggest_questions 降级契约 ============


def test_empty_digest_raises() -> None:
    with pytest.raises(SuggestError):
        _run(suggest_questions("   ", cast("LlmClient", StubLlm())))


def test_llm_failure_raises_suggest_error() -> None:
    with pytest.raises(SuggestError):
        _run(suggest_questions(_DIGEST, cast("LlmClient", StubLlm(boom=True))))


def test_unparseable_output_raises() -> None:
    with pytest.raises(SuggestError):
        _run(suggest_questions(_DIGEST, cast("LlmClient", StubLlm("抱歉，我无法回答"))))
    with pytest.raises(SuggestError):
        _run(suggest_questions(_DIGEST, cast("LlmClient", StubLlm('{"questions": }'))))


def test_all_invalid_items_raises() -> None:
    with pytest.raises(SuggestError):
        _run(suggest_questions(_DIGEST, cast("LlmClient", StubLlm('{"questions": ["", "   "]}'))))


def test_success_returns_cleaned_and_uses_suggest_task() -> None:
    llm = StubLlm('```json\n{"questions": ["共有多少商品？", "销量 Top5 是哪些？"]}\n```')
    out = _run(suggest_questions(_DIGEST, cast("LlmClient", llm)))
    assert out == ["共有多少商品？", "销量 Top5 是哪些？"]
    assert llm.task_seen == "SUGGEST"  # 任务路由(可定向小模型)
    assert "goods" in llm.calls[0][1]  # 摘要进入 prompt


# ============ prompt 全角纪律(与 test_prompts 同款锚定) ============


def _is_han(ch: str) -> bool:
    return "\u4e00" <= ch <= "\u9fff"


def test_prompt_full_width_punctuation() -> None:
    from kongming_agent import suggester

    for prompt in (suggester._SYSTEM_PROMPT, suggester._USER_PROMPT_TEMPLATE):
        for i, ch in enumerate(prompt):
            # 汉字间不得出现半角逗号/句号(全角纪律,test_prompts 同款形态检查)
            if ch in (",", ".") and 0 < i < len(prompt) - 1:
                if _is_han(prompt[i - 1]) and _is_han(prompt[i + 1]):
                    pytest.fail(
                        f"prompt 出现汉字间半角标点 {ch!r}: {prompt[max(0, i - 6) : i + 6]}"
                    )


def test_prompt_quality_rules_in_place() -> None:
    """检查建议提示禁止拼接无关表和生成占位问句。"""
    from kongming_agent import suggester

    template = suggester._USER_PROMPT_TEMPLATE
    assert "互不相关" in template  # 禁不相关表拼接(跨表强join缺陷)
    assert "系统类表与业务表" in template
    assert "占位表述" in template  # 禁「指定值」类占位问句


# ============ 端点行为 ============


def test_endpoint_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        deps, "get_llm", lambda: StubLlm('{"questions": ["共有多少商品？", "最近趋势如何？"]}')
    )
    client = TestClient(app)
    r = client.post("/internal/suggest-questions", json={"digest": _DIGEST}, headers=INTERNAL)
    assert r.status_code == 200
    body = r.json()
    assert body["generated"] is True
    assert body["questions"] == ["共有多少商品？", "最近趋势如何？"]


def test_endpoint_llm_failure_degrades(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deps, "get_llm", lambda: StubLlm(boom=True))
    client = TestClient(app)
    r = client.post("/internal/suggest-questions", json={"digest": _DIGEST}, headers=INTERNAL)
    assert r.status_code == 200  # 降级不 5xx
    assert r.json() == {"questions": [], "generated": False}


def test_endpoint_requires_internal_token() -> None:
    client = TestClient(app)
    r = client.post("/internal/suggest-questions", json={"digest": _DIGEST})
    assert r.status_code == 403


def test_endpoint_blank_digest_422(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deps, "get_llm", lambda: StubLlm("x"))
    client = TestClient(app)
    r = client.post("/internal/suggest-questions", json={"digest": ""}, headers=INTERNAL)
    assert r.status_code == 422
