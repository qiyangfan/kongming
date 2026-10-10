"""报告生成编排测试(088,剧本化:mock LLM/mock ApiClient,零真实外呼)。

覆盖:全管线 READY 形态、数据真实原则(失败章节如实标注、防御拒绝不绕道
不修复)、预算耗尽 PARTIAL 诚实收口、畸形 plan 拒绝。
"""
# pyright: reportPrivateUsage=false

import json
from typing import Any, cast

import pytest

from kongming_agent.apiclient import SchemaMeta, ToolExecuteOutcome
from kongming_agent.report_gen import _extract_json, run_report_generation
from kongming_agent.settings import Settings

# ── fakes(与 test_react 同款剧本形态)──


class FakeLlm:
    """按队列脚本应答 chat;记录调用(system/user)供断言。"""

    def __init__(self, replies: list[object]) -> None:
        self.replies = list(replies)
        self.calls: list[dict[str, str]] = []

    async def chat(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        task: str | None = None,
        trace_id: str | None = None,
        tenant_key: str | None = None,
    ) -> str:
        self.calls.append({"system": system, "user": user})
        if not self.replies:
            raise AssertionError("FakeLlm.replies 已空,LLM 不应再被调用")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return cast("str", reply)


class FakeApi:
    def __init__(
        self,
        *,
        schema: SchemaMeta | None = None,
        outcomes: list[ToolExecuteOutcome] | None = None,
    ) -> None:
        self._schema = schema
        self._outcomes = list(outcomes or [])
        self.tool_calls: list[dict[str, object]] = []
        self.phases: list[str] = []

    async def fetch_schema(
        self, *, tenant_key: str, datasource_key: str = "default"
    ) -> SchemaMeta | None:
        return self._schema

    async def tool_execute(self, **kw: object) -> ToolExecuteOutcome:
        self.tool_calls.append(kw)
        if not self._outcomes:
            raise AssertionError("FakeApi.outcomes 已空,tool_execute 不应再被调用")
        return self._outcomes.pop(0)

    async def report_progress(
        self, *, report_id: str, phase: str, tenant_key: str = "default"
    ) -> None:
        self.phases.append(phase)


def _plan_json() -> str:
    return json.dumps(
        {
            "title": "销售分析报告",
            "sections": [
                {
                    "id": "c1",
                    "title": "销售总览",
                    "children": [
                        {
                            "id": "c1-1",
                            "title": "销售总额",
                            "sql": "SELECT count(*) AS cnt FROM film",
                            "chart": {"type": "bar", "x": "cnt", "y": "cnt"},
                        }
                    ],
                }
            ],
        },
        ensure_ascii=False,
    )


def _pass_outcome(rows: list[list[object]] | None = None) -> ToolExecuteOutcome:
    return ToolExecuteOutcome(
        status="executed",
        result={
            "verdict": "PASS",
            "columns": ["cnt"],
            "rows": rows if rows is not None else [[1000]],
        },
    )


def _blocked_outcome(reason: str = "保留表拒绝") -> ToolExecuteOutcome:
    return ToolExecuteOutcome(status="executed", result={"verdict": "BLOCKED", "reason": reason})


_PAYLOAD: dict[str, object] = {
    "report_id": "rpt-1",
    "trace_id": "tr-1",
    "tenant_key": "default",
    "datasource_key": "default",
    "user_id": "u-1",
    "instruction": "出一份销售分析报告",
    "title": "",
    "context_digest": "",
}


def _sections(out: dict[str, object]) -> list[dict[str, object]]:
    return cast("list[dict[str, object]]", out["sections"])


def _binding(leaf: dict[str, object]) -> dict[str, object]:
    return cast("list[dict[str, object]]", leaf["bindings"])[0]


def _tool_args(call: dict[str, object]) -> dict[str, object]:
    return cast("dict[str, object]", call["args"])


async def test_full_pipeline_ready_shape() -> None:
    """规划→取数→撰写→组装:sections 形态、进度相位、报告通道标记、用量汇总。"""
    llm = FakeLlm([_plan_json(), "本章结论:共 1000 部影片。(数值来自快照)"])
    api = FakeApi(schema=None, outcomes=[_pass_outcome()])
    out = await run_report_generation(payload=dict(_PAYLOAD), llm=llm, api=api, settings=Settings())
    assert out["success"] is True
    assert out["partial"] is False
    assert out["title"] == "销售分析报告"
    sections = _sections(out)
    assert [s["section_id"] for s in sections] == ["c1", "c1-1"]  # 父先于子
    assert str(sections[0]["narrative_md"]).startswith("本章结论")
    leaf = sections[1]
    assert leaf["parent_id"] == "c1"
    assert leaf["data_status"] == "OK"
    b = _binding(leaf)
    assert b["status"] == "OK" and b["rows"] == [[1000]]
    assert leaf["chart"] == {"type": "bar", "x": "cnt", "y": "cnt"}
    # 取数走通用回调且带 report 渠道标记
    assert api.tool_calls[0]["tool_name"] == "execute_sql"
    assert api.tool_calls[0]["channel"] == "report"
    # 进度相位全序
    assert api.phases == ["PLANNING", "FETCHING", "WRITING", "ASSEMBLING"]
    # LLM 调用=规划+撰写 2 次;用量汇总按收集器记录(假体无记录→0 token,但 elapsed 有值)
    assert len(llm.calls) == 2
    usage = cast("dict[str, object]", out["usage"])
    assert usage["llm_calls"] == 0  # 假体不产 usage 记录,汇总如实为 0
    assert int(cast("int", usage["elapsed_ms"])) >= 0


async def test_semantic_failure_repaired_then_missing() -> None:
    """语义失败(BLOCKED):至多一次修复,修正稿重走同一防御通道;仍败如实 MISSING。"""
    llm = FakeLlm([_plan_json(), "SELECT cnt FROM v_film", "结论:数据缺失。"])
    api = FakeApi(
        schema=None, outcomes=[_blocked_outcome("表不存在"), _blocked_outcome("仍不存在")]
    )
    out = await run_report_generation(payload=dict(_PAYLOAD), llm=llm, api=api, settings=Settings())
    leaf = _sections(out)[1]
    assert leaf["data_status"] == "MISSING"
    assert "仍不存在" in str(leaf["fail_reason"])
    assert _binding(leaf)["status"] == "MISSING"
    assert len(api.tool_calls) == 2  # 首次 + 修正稿(同一防御通道)
    assert _tool_args(api.tool_calls[1])["sql"] == "SELECT cnt FROM v_film"
    assert len(llm.calls) == 2  # 规划 + 修复;全缺数章走确定性标注,不调撰写 LLM
    # 缺数章确定性如实标注(结构上不给 LLM 编造机会)
    assert "数据缺失" in str(_sections(out)[0]["narrative_md"])


async def test_defense_rejection_no_repair_no_bypass() -> None:
    """防御层直接拒绝(rejected):不修复、不绕道,MISSING 收口。"""
    llm = FakeLlm([_plan_json(), "结论:数据缺失。"])
    api = FakeApi(
        schema=None,
        outcomes=[ToolExecuteOutcome(status="rejected", reason="权限拒绝")],
    )
    out = await run_report_generation(payload=dict(_PAYLOAD), llm=llm, api=api, settings=Settings())
    leaf = _sections(out)[1]
    assert leaf["data_status"] == "MISSING"
    assert "权限拒绝" in str(leaf["fail_reason"])
    assert len(api.tool_calls) == 1  # 无第二次尝试
    assert len(llm.calls) == 1  # 仅规划:无修复调用,全缺数章走确定性标注


async def test_budget_exhausted_honest_partial() -> None:
    """预算耗尽:未取数叶 PENDING 如实标注、撰写停、partial=True,不谎称完成。"""
    llm = FakeLlm([_plan_json()])
    api = FakeApi(schema=None, outcomes=[_pass_outcome()])
    settings = Settings(report_max_llm_calls=1)  # 规划即用尽
    out = await run_report_generation(payload=dict(_PAYLOAD), llm=llm, api=api, settings=settings)
    assert out["partial"] is True
    leaf = _sections(out)[1]
    assert leaf["data_status"] == "PENDING"
    assert "预算耗尽" in str(leaf["fail_reason"])
    assert _sections(out)[0]["narrative_md"] == ""  # 未撰写,不虚构
    assert len(api.tool_calls) == 0  # 预算停未取数
    assert len(llm.calls) == 1  # 不再发起 LLM


async def test_write_phase_llm_death_keeps_sections() -> None:
    """撰写段 LLM 死亡:已取数章节保留(PENDING 未撰写如实),partial+error——
    不把已产出章节丢进空错误载荷(spec「失败保留已产出章节」)。"""
    from kongming_agent.llm import LlmError

    llm = FakeLlm([_plan_json(), LlmError("LLM 不可用")])
    api = FakeApi(schema=None, outcomes=[_pass_outcome()])
    out = await run_report_generation(payload=dict(_PAYLOAD), llm=llm, api=api, settings=Settings())
    assert out["success"] is True
    assert out["partial"] is True
    assert out["error"] == "LLM 不可用"
    sections = _sections(out)
    assert [s["section_id"] for s in sections] == ["c1", "c1-1"]
    assert sections[0]["narrative_md"] == ""  # 未撰写不虚构
    assert _binding(sections[1])["rows"] == [[1000]]  # 已取数快照保留


async def test_malformed_plan_error_payload() -> None:
    """畸形 plan → 错误载荷(success=False,空 sections),不裸抛。"""
    llm = FakeLlm(["这不是 JSON"])
    api = FakeApi(schema=None)
    out = await run_report_generation(payload=dict(_PAYLOAD), llm=llm, api=api, settings=Settings())
    assert out["success"] is False
    assert _sections(out) == []
    assert out["error"]


def test_extract_json_fenced() -> None:
    fenced = "```json\n" + json.dumps({"a": 1}) + "\n```"
    assert _extract_json(fenced) == {"a": 1}
    with_text = '结果如下: {"a": 2} 完毕'
    assert _extract_json(with_text) == {"a": 2}
    with pytest.raises(ValueError):
        _extract_json("完全没有大括号")


_ = Any  # 类型位保留
