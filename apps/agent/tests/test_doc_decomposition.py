"""文档多查询分解:retrieve 并行补检合并、失败降级与提示注入上限。

覆盖 nl2sql-agent 规格「文档多查询分解召回」:原问优先、按引用键去重、
合并上限 8、无子查询单次调用与既有 doc_hints 逐项一致。
"""

# pyright: reportPrivateUsage=false

from typing import cast

import pytest

from kongming_agent import deps
from kongming_agent.apiclient import RagContext, SchemaMeta
from kongming_agent.graph import nodes
from kongming_agent.graph.intent_nodes import _fused_doc_parts
from kongming_agent.graph.state import AgentState

QUESTION = "退货条件与退款流程分别是什么"


def _doc(key: str) -> dict[str, str]:
    return {
        "citation_key": key,
        "source_ref": f"{key}.md §1",
        "content": f"内容-{key}",
    }


class FakeApi:
    def __init__(self, answers: dict[str, RagContext | Exception | None]) -> None:
        self.answers = answers
        self.calls: list[dict[str, object]] = []

    async def rag_retrieve(self, **kw: object) -> RagContext | None:
        self.calls.append(kw)
        answer = self.answers.get(str(kw.get("question")))
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def fetch_schema(self, **kw: object) -> SchemaMeta:
        return SchemaMeta(tables=[], truncated=False)


def _state(**kw: object) -> AgentState:
    base: dict[str, object] = {
        "trace_id": "t",
        "tenant_key": "default",
        "question": QUESTION,
        "history": [],
    }
    base.update(kw)
    return cast("AgentState", base)


def _install(monkeypatch: pytest.MonkeyPatch, api: FakeApi) -> None:
    monkeypatch.setattr(deps, "get_api", lambda: api)


async def test_retrieve_without_subqueries_single_call_verbatim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """无子查询:只调用一次,doc_hints 顺序与内容逐项一致(链路等价)。"""
    original = [_doc(k) for k in "ABCDE"]
    api = FakeApi({QUESTION: RagContext(schema_hint="tenant.x", doc_hints=original)})
    _install(monkeypatch, api)
    out = await nodes.retrieve(_state())
    assert out["doc_hints"] == original
    assert len(api.calls) == 1
    assert api.calls[0]["question"] == QUESTION


async def test_retrieve_merges_subqueries_original_first_deduped_and_capped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """原问命中在前,子查询按序补检;引用键去重,总量上限 8。"""
    api = FakeApi(
        {
            QUESTION: RagContext(schema_hint="tenant.x", doc_hints=[_doc(k) for k in "ABCDE"]),
            "退货条件": RagContext(schema_hint="", doc_hints=[_doc("F"), _doc("A"), _doc("G")]),
            "退款流程": RagContext(schema_hint="", doc_hints=[_doc("H"), _doc("I")]),
        }
    )
    _install(monkeypatch, api)
    out = await nodes.retrieve(_state(doc_subqueries=["退货条件", "退款流程"]))
    keys = [h["citation_key"] for h in cast("list[dict[str, str]]", out["doc_hints"])]
    assert keys == ["A", "B", "C", "D", "E", "F", "G", "H"]
    assert [c["question"] for c in api.calls] == [QUESTION, "退货条件", "退款流程"]
    # 子查询自含,不继承多轮历史
    assert api.calls[0]["history"] == []
    assert api.calls[1]["history"] is None


async def test_retrieve_subquery_failure_keeps_original_and_siblings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """单条子查询异常只丢该路,原问与其他子查询命中保留。"""
    api = FakeApi(
        {
            QUESTION: RagContext(schema_hint="tenant.x", doc_hints=[_doc("A")]),
            "坏查询": RuntimeError("boom"),
            "好查询": RagContext(schema_hint="", doc_hints=[_doc("B")]),
        }
    )
    _install(monkeypatch, api)
    out = await nodes.retrieve(_state(doc_subqueries=["坏查询", "好查询"]))
    keys = [h["citation_key"] for h in cast("list[dict[str, str]]", out["doc_hints"])]
    assert keys == ["A", "B"]


async def test_retrieve_none_or_empty_subquery_results_keep_original(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """子查询返回 None 或零命中:原问命中原样保留。"""
    original = [_doc("A"), _doc("B")]
    api = FakeApi(
        {
            QUESTION: RagContext(schema_hint="tenant.x", doc_hints=original),
            "服务失败": None,
            "零命中": RagContext(schema_hint=""),
        }
    )
    _install(monkeypatch, api)
    out = await nodes.retrieve(_state(doc_subqueries=["服务失败", "零命中"]))
    assert out["doc_hints"] == original


def test_doc_and_fused_prompts_inject_up_to_eight_hints() -> None:
    """合并后最多注入 8 个父块(单查询仍受召回 5 条约束)。"""
    hints = [_doc(str(i)) for i in range(8)]
    _, user = nodes._doc_prompt(_state(), hints)
    for i in range(8):
        assert f"内容-{i}" in user
    parts = _fused_doc_parts(hints)
    assert len(parts) == 8
