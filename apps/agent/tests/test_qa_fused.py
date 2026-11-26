# pyright: reportPrivateUsage=false
"""三态意图路由 + 融合问答 agent 侧测试(nodes/build 直调)。

- 路由解析纯函数:三态判定、畸形/越界回退;
- route_intent 节点:LLM 三态、超时/失败/低置信回退(回退不写 intent 键);
- route_after_retrieve:intent 分支 + 词表回退逐字一致;
- _after_summarize:融合态收口语义(数据为刃);
- fused_answer 节点:双命中融合、净化投影、失败兜底。
"""

import asyncio
import json
from typing import cast

import pytest

from kongming_agent import deps
from kongming_agent.graph.build import _after_summarize
from kongming_agent.graph.intent_nodes import (
    fused_answer,
    parse_intent_route,
    route_intent,
    snapshot_intent_route_stats,
)
from kongming_agent.graph.nodes import route_after_retrieve
from kongming_agent.graph.state import AgentState, ExecutionResult

_ROUTE_OK = '{"intent":"data","confidence":0.99,"reason":"问的是数量"}'


class _Llm:
    """脚本替身:answers 耗尽抛 LlmError(与真实故障同路径)。"""

    def __init__(self, answers: list[str], *, sleep_sec: float = 0.0) -> None:
        self.answers = list(answers)
        self.sleep_sec = sleep_sec

    async def chat(self, system: str, user: str, **kw: object) -> str:
        if self.sleep_sec:
            await asyncio.sleep(self.sleep_sec)
        if not self.answers:
            from kongming_agent.llm import LlmError

            raise LlmError("no scripted answer")
        return self.answers.pop(0)


# ── parse_intent_route(纯函数)──


def test_parse_intent_route_accepts_three_states() -> None:
    for intent in ("data", "knowledge", "fused"):
        parsed = parse_intent_route(f'{{"intent":"{intent}","confidence":0.9,"reason":"r"}}')
        assert parsed == (intent, 0.9, "r", False, "", ())


def test_parse_intent_route_subqueries_gated_and_trimmed() -> None:
    """知识/融合保留子查询:去空白、去重、滤非字符串、上限 3;data 丢弃。"""
    raw = json.dumps(
        {
            "intent": "knowledge",
            "confidence": 0.9,
            "subqueries": [" 退货条件 ", "退款流程", "退货条件", "", 3, "适用门店"],
        }
    )
    parsed = parse_intent_route(raw)
    assert parsed is not None
    assert parsed[5] == ("退货条件", "退款流程", "适用门店")

    fused = parse_intent_route(
        '{"intent":"fused","confidence":0.9,"subqueries":["销售额","退货政策"]}'
    )
    assert fused is not None and fused[5] == ("销售额", "退货政策")

    data = parse_intent_route('{"intent":"data","confidence":0.9,"subqueries":["销售额"]}')
    assert data is not None and data[5] == ()
    # 非数组形态按无子查询处理,不阻断意图解析
    bad = parse_intent_route('{"intent":"knowledge","confidence":0.9,"subqueries":"退货条件"}')
    assert bad is not None and bad[5] == ()


def test_parse_intent_route_rejects_malformed() -> None:
    assert parse_intent_route("不是 JSON") is None
    assert parse_intent_route('{"intent":"chat","confidence":0.9}') is None  # 越域
    assert parse_intent_route('{"intent":"data"}') is None  # 缺置信
    assert parse_intent_route('{"intent":"data","confidence":"高"}') is None  # 不可解析
    assert parse_intent_route('{"intent":"data","confidence":1.5}') is None  # 越界


# ── route_intent 节点 ──


async def test_route_intent_llm_three_states(monkeypatch: pytest.MonkeyPatch) -> None:
    for intent in ("data", "knowledge", "fused"):
        monkeypatch.setattr(
            deps, "get_llm", lambda i=intent: _Llm([f'{{"intent":"{i}","confidence":0.9}}'])
        )
        out = await route_intent({"trace_id": "t", "tenant_key": "d", "question": "q"})
        assert out["intent"] == intent
        assert out["intent_source"] == "llm"
        assert isinstance(out["intent_confidence"], float)


async def test_route_intent_passes_subqueries_only_for_doc_channels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """知识/融合态写 doc_subqueries;data 态即使模型给出子查询也不透传。"""
    monkeypatch.setattr(
        deps,
        "get_llm",
        lambda: _Llm(
            ['{"intent":"knowledge","confidence":0.9,"subqueries":["退货条件","退款流程"]}']
        ),
    )
    out = await route_intent({"trace_id": "t", "tenant_key": "d", "question": "退货要满足什么"})
    assert out["doc_subqueries"] == ["退货条件", "退款流程"]

    monkeypatch.setattr(
        deps,
        "get_llm",
        lambda: _Llm(['{"intent":"data","confidence":0.9,"subqueries":["销售额"]}']),
    )
    out = await route_intent({"trace_id": "t", "tenant_key": "d", "question": "上个月销售额"})
    assert "doc_subqueries" not in out


async def test_route_intent_llm_error_falls_back_without_intent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM 失败回退:不写 intent 键(route_after_retrieve 走 前词表分支),
    回退计数 +1(可观测)。"""
    before = snapshot_intent_route_stats()["intent_route_fallback_total"]
    monkeypatch.setattr(deps, "get_llm", lambda: _Llm([]))
    out = await route_intent({"trace_id": "t", "tenant_key": "d", "question": "q"})
    assert out == {}
    assert snapshot_intent_route_stats()["intent_route_fallback_total"] == before + 1


async def test_route_intent_timeout_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """意图判断超时后回退到词表规则，不能阻断问答。"""
    monkeypatch.setenv("KM_AGENT_INTENT_ROUTE_TIMEOUT_SEC", "0.05")
    monkeypatch.setattr(deps, "get_llm", lambda: _Llm([_ROUTE_OK], sleep_sec=0.5))
    out = await route_intent({"trace_id": "t", "tenant_key": "d", "question": "q"})
    assert out == {}


async def test_route_intent_low_confidence_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """置信低于阈值(缺省 0.7)回退:拿不准不选最贵的融合路径(D2)。"""
    monkeypatch.setattr(deps, "get_llm", lambda: _Llm(['{"intent":"fused","confidence":0.4}']))
    out = await route_intent({"trace_id": "t", "tenant_key": "d", "question": "q"})
    assert out == {}


async def test_route_intent_config_defaults() -> None:
    """检查意图路由的默认超时和置信度阈值。"""
    from kongming_agent.settings import Settings

    settings = Settings()
    assert settings.intent_route_timeout_sec == 3.0
    assert settings.intent_route_min_confidence == 0.7


# ── route_after_retrieve(intent 分支 + 回退逐字一致)──


def _state(**kw: object) -> AgentState:
    """AgentState 动态装配(测试边界一次收窄,与 _invoke 同款手法)。"""
    base: dict[str, object] = {
        "trace_id": "t",
        "tenant_key": "default",
        "question": "上月退货率为什么升高",
        "history": [],
    }
    base.update(kw)
    return cast("AgentState", base)


def test_route_fused_goes_sql_channel_first() -> None:
    """融合态先执行 SQL，再进入融合回答节点。"""
    assert route_after_retrieve(_state(intent="fused")) == "generate"


def test_route_knowledge_channel_confirm_by_retrieval() -> None:
    """knowledge 走通道确认(D1):意图定通道、材料定可用。"""
    with_doc = _state(intent="knowledge", doc_hints=[{"source_ref": "a.md", "content": "x"}])
    assert route_after_retrieve(with_doc) == "doc_answer"
    assert route_after_retrieve(_state(intent="knowledge")) == "generate"


def test_route_data_attachment_rule_kept() -> None:
    """意图模型判为 data 时，含图片且无 SQL 意图的问题仍走图片问答。"""
    attached = _state(
        intent="data",
        has_attachments=True,
        question="这张图说明了什么",
        doc_hints=[{"source_ref": "a.md", "content": "x"}],
    )
    assert route_after_retrieve(attached) == "doc_answer"
    assert route_after_retrieve(_state(intent="data")) == "generate"


def test_route_fallback_wordlist_verbatim() -> None:
    """意图判断失败时沿用规则词表，多轮问句仍需满足强文档意图。"""
    knowledge = _state(
        question="退货政策是什么",
        doc_hints=[{"source_ref": "a.md", "content": "x"}],
    )
    assert route_after_retrieve(knowledge) == "doc_answer"
    multi = _state(
        question="这些影片各自的分类是什么",
        history=["查询租金最高的5部影片"],
        doc_hints=[{"source_ref": "a.md", "content": "x"}],
    )
    assert route_after_retrieve(multi) == "generate"
    assert route_after_retrieve(_state(question="退货政策是什么")) == "generate"


# ── _after_summarize(融合收口语义)──


def test_after_summarize_non_fused_ends() -> None:
    assert _after_summarize({}) == "end"
    assert _after_summarize({"intent": "data"}) == "end"
    assert _after_summarize({"intent": "knowledge"}) == "end"


def test_after_summarize_fused_requires_success_and_docs() -> None:
    """SQL 未通过、结果为空或文档未命中时，保持为纯数据态终态。"""
    ok = ExecutionResult(verdict="PASS", columns=["rate"], rows=[[0.05]])
    full: AgentState = {
        "intent": "fused",
        "execution": ok,
        "doc_hints": [{"citation_key": "p-A1"}],
    }
    assert _after_summarize(full) == "fused_answer"
    assert _after_summarize({"intent": "fused"}) == "end"
    blocked: AgentState = {
        "intent": "fused",
        "execution": ExecutionResult(verdict="BLOCKED"),
    }
    assert _after_summarize(blocked) == "end"
    empty: AgentState = {
        "intent": "fused",
        "execution": ExecutionResult(verdict="PASS", columns=["rate"], rows=[]),
        "doc_hints": [{"citation_key": "p-A1"}],
    }
    assert _after_summarize(empty) == "end"
    no_doc: AgentState = {"intent": "fused", "execution": ok}
    assert _after_summarize(no_doc) == "end"


# ── fused_answer 节点 ──

_DOC: dict[str, str] = {
    "citation_key": "policy-A1B2",
    "source_ref": "退货政策.md §2",
    "content": "7 天内可退",
}


def _fused_state(**kw: object) -> AgentState:
    base: dict[str, object] = {
        "trace_id": "t",
        "tenant_key": "default",
        "question": "上月退货率为什么升高",
        "intent": "fused",
        "execution": ExecutionResult(verdict="PASS", columns=["rate"], rows=[[0.05]]),
        "stats": {"rows": 1, "rate_avg": 0.05},
        "doc_hints": [_DOC],
    }
    base.update(kw)
    return cast("AgentState", base)


async def test_fused_answer_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """双命中融合:正文含数据结论+口径引用键,sources 投影,insights 清空
    (融合正文即洞察位正文);数据数字经槽位渲染,文档口径数字(7 天)为原文逐字片段。"""
    monkeypatch.setattr(
        deps,
        "get_llm",
        lambda: _Llm(
            [
                "上月退货率为 {{stats.rate_avg}}，共 {{rows}} 行。"
                "政策口径:7 天内可退【policy-A1B2】。"
            ]
        ),
    )
    out = await fused_answer(_fused_state())
    summary = str(out["summary"])
    assert "0.05" in summary
    assert "【policy-A1B2】" in summary
    assert out["insights"] == []
    assert out["sources"] == [
        {"citation_key": "policy-A1B2", "source_ref": "退货政策.md §2", "doc_name": "退货政策.md"}
    ]


async def test_fused_answer_strips_fabricated_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """编造键净化:键形态(4 位十六进制尾)但未下发 → 降级纯文本
    (剥括号留文字),sources 无幽灵条目。"""
    monkeypatch.setattr(
        deps,
        "get_llm",
        lambda: _Llm(["退货率为 {{stats.rate_avg}}。口径见【fake-DEAD】说明。"]),
    )
    out = await fused_answer(_fused_state())
    summary = str(out["summary"])
    assert "【fake-DEAD】" not in summary
    assert "fake-DEAD" in summary  # 括号剥除,文字保留
    assert out["sources"] == []


async def test_fused_answer_suffix_normalization(monkeypatch: pytest.MonkeyPatch) -> None:
    """后缀命中归一:改写前缀的引用归一为权威键,正文与 sources 一一对应。"""
    monkeypatch.setattr(
        deps,
        "get_llm",
        lambda: _Llm(["口径:7 天内可退【其他前缀-A1B2】。"]),
    )
    out = await fused_answer(_fused_state())
    summary = str(out["summary"])
    assert "【policy-A1B2】" in summary
    sources = cast("list[dict[str, object]]", out["sources"])
    assert [s["citation_key"] for s in sources] == ["policy-A1B2"]


async def test_fused_answer_unquoted_doc_number_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """文档引用数字非原文逐字片段:正文整体弃用(空返回),保留 summarize 纯数据答,
    sources 随融合正文一并作废。"""
    # 「500 元」不在下发文档(7 天)原文中 → doc_number_unquoted
    monkeypatch.setattr(
        deps,
        "get_llm",
        lambda: _Llm(
            ["上月退货率为 {{stats.rate_avg}}。政策要求订单金额 500 元以上才可退【policy-A1B2】。"]
        ),
    )
    out = await fused_answer(_fused_state())
    assert out == {}


async def test_fused_answer_llm_error_falls_back_to_data_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2.3 失败兜底:回退纯数据答正文(summarize 产物在 state),不使消息失败;
    兜底计数入可观测。与 doc_answer 的 LlmError 上抛语义刻意不同——此处
    结果面已就绪。"""
    before = snapshot_intent_route_stats()["fused_answer_fallback_total"]
    monkeypatch.setattr(deps, "get_llm", lambda: _Llm([]))
    out = await fused_answer(_fused_state(summary="共 1 行,退货率 0.05。"))
    assert out == {}
    assert snapshot_intent_route_stats()["fused_answer_fallback_total"] == before + 1


async def test_fused_answer_empty_text_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deps, "get_llm", lambda: _Llm(["   "]))
    assert await fused_answer(_fused_state()) == {}


async def test_fused_answer_precondition_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """前置复核(直调兜底):无 execution/非 PASS/无文档命中/空结果 → 原样放行
    纯数据答,零 LLM 调用。"""

    class _Boom:
        async def chat(self, *a: object, **kw: object) -> str:
            raise AssertionError("前置不满足时不得发起 LLM 调用")

    monkeypatch.setattr(deps, "get_llm", _Boom)
    assert await fused_answer(_fused_state(execution=None)) == {}
    assert (
        await fused_answer(_fused_state(execution=ExecutionResult(verdict="ERROR", reason="x")))
        == {}
    )
    assert await fused_answer(_fused_state(doc_hints=[])) == {}
    assert (
        await fused_answer(
            _fused_state(execution=ExecutionResult(verdict="PASS", columns=["r"], rows=[]))
        )
        == {}
    )
