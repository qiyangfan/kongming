"""圆桌编排核心剧本化测试(115,安全与失控防护先行)。

覆盖:治理协议(共识判定/轮次硬顶/预算池/全员弃权提前收口)、诚实收口
(未共识分歧保留/假共识不洗白执行失败/纯文本对辩无证据不通过)、名册法定
规模不足不开桌、主席判定畸形按未共识处理、转写落账契约(判定行 0 号位)、
立场/弃权解析纯函数、黑板快照受控。参与者为可重放剧本(mock),无真实 LLM。
"""

import json
from typing import cast

import pytest

from kongming_agent.roundtable_gen import (
    ROLE_CHARTERS,
    ParticipantRunner,
    is_abstain,
    parse_stance,
    participant_system_prompt,
    run_roundtable,
)


class _FakeLlm:
    """主席判定假体:按调用次序吐预设裁决(畸形输出场景给非 JSON)。"""

    def __init__(self, replies: list[str]) -> None:
        self._replies = replies
        self.calls = 0

    async def chat(self, system: str, user: str, temperature: float = 0.2) -> str:
        reply = self._replies[min(self.calls, len(self._replies) - 1)]
        self.calls += 1
        return reply


def _chair(consensus: bool, reason: str = "判定") -> str:
    return json.dumps({"consensus": consensus, "reason": reason}, ensure_ascii=False)


def _roster(n: int = 3) -> list[dict[str, object]]:
    roles = ["analyst", "skeptic", "auditor"]
    return [
        {"no": i + 1, "role": roles[i], "label": roles[i], "persona": ROLE_CHARTERS[roles[i]]}
        for i in range(n)
    ]


def _runner_factory(
    scripts: dict[int, list[dict[str, object]]],
) -> ParticipantRunner:
    """参与者执行器假体:按参与者序号吐剧本(逐次消费,耗尽后重复末项)。"""
    calls: dict[int, int] = {}

    async def _run(no: int, sys_prompt: str) -> dict[str, object]:
        assert sys_prompt, "参与者系统提示(章程+纪律+快照)必须注入"
        seen = calls.get(no, 0)
        calls[no] = seen + 1
        seq = scripts[no]
        return seq[min(seen, len(seq) - 1)]

    return _run


def _speech(
    text: str = "结论:销售额上升。立场:支持", *, verdict: str = "PASS", sql: str = "SELECT 1"
) -> dict[str, object]:
    return {
        "verdict": verdict,
        "summary": text,
        "sql": sql,
        "has_execution": bool(sql),
        "usage": {"llm_calls": 1, "total_tokens": 100},
    }


async def test_consensus_round1_pass() -> None:
    """真共识:首轮全员实质发言 PASS + 主席判定收敛 → PASS/consensus/dissent=False。"""
    log: list[tuple[int, int, str, dict[str, object], dict[str, object], dict[str, object]]] = []

    async def rec(
        r: int, w: int, k: str, c: dict[str, object], e: dict[str, object], u: dict[str, object]
    ) -> None:
        log.append((r, w, k, c, e, u))

    result = await run_roundtable(
        payload={"subgoal": "评估异常成因"},
        llm=_FakeLlm([_chair(True, "结论一致")]),
        roster=_roster(3),
        participant_runner=_runner_factory(
            {1: [_speech()], 2: [_speech("附议,立场:支持")], 3: [_speech()]}
        ),
        turn_recorder=rec,
        max_rounds=3,
    )
    assert result["verdict"] == "PASS"
    assert result["consensus"] is True
    assert result["dissent"] is False
    assert result["rounds_used"] == 1
    # 转写契约:3 发言行(1..3 号)+ 1 判定行(0 号位);发言行带用量
    assert sorted((r, w, k) for r, w, k, *_ in log if k == "speech") == [
        (1, 1, "speech"),
        (1, 2, "speech"),
        (1, 3, "speech"),
    ]
    assert [(row[0], row[1], row[2]) for row in log if row[2] == "verdict"] == [(1, 0, "verdict")]
    assert all(row[5].get("total_tokens") is not None for row in log if row[2] == "speech")


async def test_round_cap_preserves_dissent() -> None:
    """漏收敛触顶兜底:主席恒不收敛 → 轮次上限触发,分歧如实保留(不编造共识)。"""
    result = await run_roundtable(
        payload={"subgoal": "q"},
        llm=_FakeLlm([_chair(False, "口径分歧")]),
        roster=_roster(2),
        participant_runner=_runner_factory(
            {1: [_speech("A:总量 100,立场:支持")], 2: [_speech("B:总量 200,立场:反对")]}
        ),
        max_rounds=2,
    )
    assert result["verdict"] == "PASS"  # 执行证据全 PASS,裁决看证据不看附和
    assert result["consensus"] is False
    assert result["dissent"] is True
    assert "触顶" in str(result["reservation"])
    assert result["rounds_used"] == 2


async def test_budget_exhaustion_honest_close() -> None:
    """预算池中途耗尽:发言与查证同池,未完整轮如实披露后收口。"""
    result = await run_roundtable(
        payload={"subgoal": "q"},
        llm=_FakeLlm([_chair(False)]),
        roster=_roster(3),
        participant_runner=_runner_factory({i: [_speech()] for i in (1, 2, 3)}),
        max_rounds=3,
        execution_budget=4,  # 首轮 3 次后仅剩 1 次:第 2 轮中途截断
    )
    assert result["consensus"] is False
    assert "预算" in str(result["reservation"])
    assert result["rounds_used"] == 2
    # 截断收口的裁决语义钉死:截断轮前全体实质发言 PASS → 按证据口径 PASS
    # (dissent/reservation 双披露,非谎称完成)
    assert result["verdict"] == "PASS"


async def test_budget_truncation_with_failed_speech_is_error() -> None:
    """截断轮中已有失败发言:按证据口径 ERROR(截断不洗白执行失败)。"""
    result = await run_roundtable(
        payload={"subgoal": "q"},
        llm=_FakeLlm([_chair(False)]),
        roster=_roster(3),
        participant_runner=_runner_factory(
            {
                1: [_speech()],
                2: [_speech(verdict="ERROR", sql="", text="查询失败")],
                3: [_speech()],
            }
        ),
        max_rounds=3,
        execution_budget=3,  # 恰好一轮,主席判不共识后预算尽
    )
    assert result["verdict"] == "ERROR"
    assert result["consensus"] is False


async def test_all_abstain_early_close() -> None:
    """连续一轮全员弃权 → 提前收口,不改标共识(首轮有实质发言与证据)。"""
    result = await run_roundtable(
        payload={"subgoal": "q"},
        llm=_FakeLlm([_chair(False), _chair(True)]),  # 第 2 轮弃权即收口,主席未再判定
        roster=_roster(2),
        participant_runner=_runner_factory(
            {
                1: [_speech("A:结论,立场:支持"), {"verdict": "PASS", "summary": "弃权"}],
                2: [_speech("B:附议,立场:支持"), {"verdict": "PASS", "summary": "弃权"}],
            }
        ),
        max_rounds=3,
    )
    assert result["rounds_used"] == 2  # 次轮全员弃权提前收口
    assert result["consensus"] is False
    assert "弃权" in str(result["reservation"])
    assert result["verdict"] == "PASS"  # 首轮发言带执行证据,执行裁决 PASS


async def test_all_abstain_round1_closes_without_evidence() -> None:
    """首轮即全员弃权:无实质发言 → ERROR 诚实收口。"""

    async def runner(no: int, sys_prompt: str) -> dict[str, object]:
        return {"verdict": "PASS", "summary": "弃权", "sql": "", "has_execution": False}

    result = await run_roundtable(
        payload={"subgoal": "q"},
        llm=_FakeLlm([_chair(True)]),
        roster=_roster(2),
        participant_runner=runner,
        max_rounds=3,
    )
    # 注:runner 恒弃权,第一轮即提前收口
    assert result["verdict"] == "ERROR"
    assert "无任何实质发言" in str(result["reservation"])


async def test_quorum_fail_no_opening() -> None:
    """名册有效规模不足(<2):不开桌,部分完成语义(pending 非空)。"""
    result = await run_roundtable(
        payload={"subgoal": "q"},
        llm=_FakeLlm([]),
        roster=_roster(1),
        participant_runner=_runner_factory({}),
        max_rounds=3,
    )
    assert result["verdict"] == "ERROR"
    assert result["pending_subgoals"] == ["q"]
    assert result["rounds_used"] == 0
    assert "未开桌" in str(result["reservation"])


async def test_budget_zero_no_opening() -> None:
    """预算继承不足:不开桌并如实披露。"""
    result = await run_roundtable(
        payload={"subgoal": "q"},
        llm=_FakeLlm([]),
        roster=_roster(3),
        participant_runner=_runner_factory({}),
        max_rounds=3,
        execution_budget=0,
    )
    assert result["verdict"] == "ERROR"
    assert "预算" in str(result["reservation"])


async def test_fake_consensus_not_whitewashed() -> None:
    """假共识不洗白:主席判收敛但参与者执行失败 → 按证据口径收口 ERROR。"""
    result = await run_roundtable(
        payload={"subgoal": "q"},
        llm=_FakeLlm([_chair(True, "全体一致")]),
        roster=_roster(2),
        participant_runner=_runner_factory(
            {1: [_speech()], 2: [_speech(verdict="ERROR", sql="", text="我同意 A,立场:支持")]}
        ),
        max_rounds=3,
    )
    assert result["consensus"] is True  # 主席确曾判共识(判定行如实落账)
    assert result["verdict"] == "ERROR"  # 但执行失败不被共识洗白
    assert "冲突" in str(result["reservation"])


async def test_pure_text_deliberation_fails() -> None:
    """全程无数据查证:纯文本对辩不构成完成证据(090 证据口径圆桌投影)。"""
    result = await run_roundtable(
        payload={"subgoal": "q"},
        llm=_FakeLlm([_chair(True)]),
        roster=_roster(2),
        participant_runner=_runner_factory({i: [_speech(sql="", verdict="PASS")] for i in (1, 2)}),
        max_rounds=3,
    )
    assert result["verdict"] == "ERROR"
    assert "纯文本" in str(result["reservation"])


async def test_chair_malformed_output_treated_as_no_consensus() -> None:
    """主席判定畸形/异常:按未共识处理,畸形原因落判定行披露(披露面=轮次账;
    reservation 只承载终止通道注记),轮次硬顶兜底收口。"""
    log: list[tuple[int, int, str, dict[str, object], dict[str, object], dict[str, object]]] = []

    async def rec(
        r: int, w: int, k: str, c: dict[str, object], e: dict[str, object], u: dict[str, object]
    ) -> None:
        log.append((r, w, k, c, e, u))

    result = await run_roundtable(
        payload={"subgoal": "q"},
        llm=_FakeLlm(["这不是 JSON"]),
        roster=_roster(2),
        participant_runner=_runner_factory({i: [_speech()] for i in (1, 2)}),
        turn_recorder=rec,
        max_rounds=2,
    )
    assert result["consensus"] is False
    verdict_reasons = [str(c.get("reason")) for _, _, k, c, _, _ in log if k == "verdict"]
    assert verdict_reasons and all("畸形" in r for r in verdict_reasons)
    assert "触顶" in str(result["reservation"])


async def test_abstain_participant_excluded_from_cross() -> None:
    """全程弃权参与者:结论记 ABSTAIN 条目,不拖垮执行裁决(诚实弃权非失败)。"""
    result = await run_roundtable(
        payload={"subgoal": "q"},
        llm=_FakeLlm([_chair(False)]),
        roster=_roster(3),
        participant_runner=_runner_factory(
            {1: [_speech()], 2: [_speech()], 3: [{"verdict": "PASS", "summary": "弃权"}]}
        ),
        max_rounds=1,
    )
    conclusions = cast("list[dict[str, object]]", result["conclusions"])
    abstained = [c for c in conclusions if c.get("verdict") == "ABSTAIN"]
    assert len(abstained) == 1
    assert abstained[0]["stance"] == "弃权"
    assert result["verdict"] == "PASS"


async def test_stance_parsing_and_abstain_detection() -> None:
    """立场解析(末行优先/叙述句不算标注/句读截断)与弃权精确/宽松形态边界。"""
    assert parse_stance("分析..\n立场:反对") == "反对"
    assert parse_stance("立场:支持\n后记\n立场:中性") == "中性"
    assert parse_stance("没有立场行") == "未标注"
    assert parse_stance("立场鲜明的反对这种口径") == "未标注"  # 无分隔符=叙述句
    assert parse_stance("立场:支持。补充说明若干") == "支持"  # 首个句读截断
    assert parse_stance("立场:支持. 补充说明") == "支持"  # ASCII 句点同截
    assert is_abstain("弃权")
    assert is_abstain(" 弃权(无新增论据) ")
    assert not is_abstain("我不弃权,立场:支持")
    # 收紧后的负向边界(防宽松前缀回归):超长括注/换行附理由/前缀形态
    assert not is_abstain("弃权(这是一个超过十二个字符的括注说明)")
    assert not is_abstain("弃权\n理由:无新增论据")
    assert not is_abstain("本轮弃权")


async def test_participant_prompt_composition_and_snapshot_cap() -> None:
    """系统提示组装:章程/纪律/轮次信息/快照齐全;快照体积受控。"""
    prompt = participant_system_prompt("人格A", "上下文", "转写快照" * 1000, 2, 3)
    assert "人格A" in prompt
    assert "圆桌纪律" in prompt
    assert "第 2/3 轮" in prompt
    assert "黑板转写" in prompt
    # 快照段受控(2000 截断 + 固定开销)
    snapshot_part = prompt.split("黑板转写", 1)[1]
    assert len(snapshot_part) <= 2100
    first = participant_system_prompt("人格B", "", "", 1, 3)
    assert "黑板转写" not in first  # 首轮无转写省略黑板段


async def test_recorder_receives_structured_turns() -> None:
    """转写行结构:发言行带 stance/verdict/usage,判定行 consensus/reason。"""
    log: list[tuple[int, int, str, dict[str, object], dict[str, object], dict[str, object]]] = []

    async def rec(
        r: int, w: int, k: str, c: dict[str, object], e: dict[str, object], u: dict[str, object]
    ) -> None:
        log.append((r, w, k, c, e, u))

    await run_roundtable(
        payload={"subgoal": "q"},
        llm=_FakeLlm([_chair(True, "一致")]),
        roster=_roster(2),
        participant_runner=_runner_factory(
            {1: [_speech("结论 X。立场:支持")], 2: [_speech("质疑口径。立场:反对")]}
        ),
        turn_recorder=rec,
        max_rounds=2,
    )
    speech_rows = [row for row in log if row[2] == "speech"]
    assert all("stance" in c for _, _, _, c, _, _ in speech_rows)
    assert all(e.get("sql") for _, _, _, _, e, _ in speech_rows)
    assert all(u.get("total_tokens") == 100 for _, _, _, _, _, u in speech_rows)
    verdict_rows = [(row[1], row[3]) for row in log if row[2] == "verdict"]
    assert verdict_rows and verdict_rows[0][0] == 0
    assert verdict_rows[0][1]["consensus"] is True


async def test_structural_no_api_handle() -> None:
    """结构断言:编排核心零 api 句柄(无 apiclient/ApiClient 引用,ADR-002)。"""
    import inspect

    import kongming_agent.roundtable_gen as mod

    src = inspect.getsource(mod)
    assert "apiclient" not in src
    assert "ApiClient" not in src
    assert "router" not in src


@pytest.mark.parametrize(
    ("budget", "rounds", "expected_rounds"),
    [(4, 2, 2), (3, 2, 2), (1, 2, 1)],
)
async def test_budget_shapes(budget: int, rounds: int, expected_rounds: int) -> None:
    """预算形态:满额跑满轮;不足时按池截断(2 人 × 轮次池;
    被截断轮计入 rounds_used,收口如实披露)。"""
    result = await run_roundtable(
        payload={"subgoal": "q"},
        llm=_FakeLlm([_chair(False)]),
        roster=_roster(2),
        participant_runner=_runner_factory({i: [_speech()] for i in (1, 2)}),
        max_rounds=rounds,
        execution_budget=budget,
    )
    assert result["rounds_used"] == expected_rounds
