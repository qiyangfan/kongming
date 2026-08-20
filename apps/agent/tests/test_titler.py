"""会话标题改写测试(010):净化防御/降级契约/端点行为。

prompt 为 010 全新撰写(上游无 LLM 标题资产,非逐字移植文件);
降级契约:任何失败 → TitleError/rewritten=False,api 侧保持截断标题。
"""

import pytest
from fastapi.testclient import TestClient

from kongming_agent import deps
from kongming_agent.llm import LlmError
from kongming_agent.main import app
from kongming_agent.titler import TitleError, rewrite_title, sanitize_title

INTERNAL = {"X-Internal-Token": "test-internal-token"}


class StubLlm:
    """固定应答/故障替身;记录 prompt 供断言。"""

    def __init__(self, reply: str = "", *, boom: bool = False) -> None:
        self.reply = reply
        self.boom = boom
        self.calls: list[tuple[str, str]] = []
        self.max_tokens_seen: int | None = None

    async def chat(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> str:
        self.calls.append((system, user))
        self.max_tokens_seen = max_tokens
        if self.boom:
            raise LlmError("connect failed")
        return self.reply

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise AssertionError("标题链路不应调用 embed")


# ============ sanitize_title 净化 ============


def test_sanitize_strips_wrapping_quotes_and_newlines() -> None:
    assert sanitize_title('  「月度销售复盘」\n') == "月度销售复盘"
    assert sanitize_title('"库存分析"') == "库存分析"
    assert sanitize_title("《季度财报》") == "季度财报"
    assert sanitize_title("第一行\n第二行") == "第一行 第二行"  # 换行折叠为单空格


def test_sanitize_strips_label_prefix() -> None:
    assert sanitize_title("标题:商品查询") == "商品查询"
    assert sanitize_title("标题： 用户分析") == "用户分析"


def test_sanitize_caps_length() -> None:
    assert len(sanitize_title("标" * 80)) == 50


def test_sanitize_empty() -> None:
    assert sanitize_title("  \n ") == ""
    assert sanitize_title("「」") == ""


# ============ rewrite_title 链路 ============


async def test_rewrite_ok_and_prompt_shape() -> None:
    llm = StubLlm("「各商品库存对比」")
    title = await rewrite_title("各商品库存分别是多少?", llm)  # type: ignore[arg-type]
    assert title == "各商品库存对比"
    system, user = llm.calls[0]
    assert "标题" in system
    assert "各商品库存分别是多少?" in user
    assert llm.max_tokens_seen == 60  # 轻量端点封顶(design D1,R2-S1)


async def test_rewrite_llm_error_raises() -> None:
    with pytest.raises(TitleError):
        await rewrite_title("问句", StubLlm(boom=True))  # type: ignore[arg-type]


async def test_rewrite_empty_output_raises() -> None:
    with pytest.raises(TitleError):
        await rewrite_title("问句", StubLlm("  "))  # type: ignore[arg-type]


async def test_rewrite_blank_question_raises() -> None:
    with pytest.raises(TitleError):
        await rewrite_title("   ", StubLlm("x"))  # type: ignore[arg-type]


# ============ 端点面 ============


def test_endpoint_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deps, "get_llm", lambda: StubLlm("商品库存总览"))
    r = TestClient(app).post(
        "/internal/rewrite-title", json={"question": "有多少商品"}, headers=INTERNAL
    )
    assert r.status_code == 200
    assert r.json() == {"rewritten": True, "title": "商品库存总览"}


def test_endpoint_llm_failure_degrades(monkeypatch: pytest.MonkeyPatch) -> None:
    """失败不抛 5xx:rewritten=False,api 侧保持截断标题(与 describe-table 同构)。"""
    monkeypatch.setattr(deps, "get_llm", lambda: StubLlm(boom=True))
    r = TestClient(app).post(
        "/internal/rewrite-title", json={"question": "有多少商品"}, headers=INTERNAL
    )
    assert r.status_code == 200
    assert r.json() == {"rewritten": False, "title": ""}


def test_endpoint_requires_internal_token() -> None:
    r = TestClient(app).post("/internal/rewrite-title", json={"question": "q"})
    assert r.status_code == 403


def test_endpoint_blank_question_422(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deps, "get_llm", lambda: StubLlm("x"))
    r = TestClient(app).post("/internal/rewrite-title", json={"question": ""}, headers=INTERNAL)
    assert r.status_code == 422
