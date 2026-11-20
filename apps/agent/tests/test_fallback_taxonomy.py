# pyright: reportPrivateUsage=false
"""兜底分类与澄清引导:直通族别文案、语义闲聊兜底、澄清早退、防循环、
fail-open、互斥优先级、可观测计数(spec「闲聊语义兜底判定」
「歧义问句澄清引导」「对话复语不进取数补全」行为锚)。"""

import pytest

from kongming_agent import deps
from kongming_agent.graph import build, dialogue, intent_nodes, nodes
from kongming_agent.graph.state import FALLBACK_CHITCHAT, FALLBACK_CLARIFY
from tests.test_nodes import FakeApi, FakeLlm, _state

_ROUTE_JSON = '{"intent":"%s","ambiguous":%s,"clarify_question":"%s","confidence":0.9,"reason":"r"}'


# ============ 直通族别文案(词表修正:SHALL NOT 数据缺失话术) ============


class TestPassthroughReply:
    async def test_greeting_gets_family_reply_not_data_miss(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeLlm(["不应被调用"])
        monkeypatch.setattr(deps, "get_llm", lambda: fake)
        monkeypatch.setattr(deps, "get_api", FakeApi)
        out = await nodes.understand(_state(question="你好"))
        assert out["end_reason"] == "no_data"
        assert out["fallback_type"] == FALLBACK_CHITCHAT
        assert "你好" in str(out["error"])
        assert "未找到" not in str(out["error"])  # 数据缺失话术根除
        assert fake.calls == []

    async def test_stop_word_gets_stop_reply(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(deps, "get_llm", FakeLlm)
        monkeypatch.setattr(deps, "get_api", FakeApi)
        out = await nodes.understand(_state(question="不用了"))
        assert out["fallback_type"] == FALLBACK_CHITCHAT
        assert "已停止" in str(out["error"])

    async def test_family_mapping_is_total_over_vocabulary(self) -> None:
        for word in dialogue.DIALOGUE_REPLY_WORDS:
            assert dialogue.reply_family(word) in dialogue.CHITCHAT_REPLIES


# ============ 意图路由帧扩展解析 ============


class TestParseIntentRoute:
    def test_extended_fields_parsed(self) -> None:
        parsed = intent_nodes.parse_intent_route(
            '{"intent":"data","ambiguous":true,"clarify_question":"哪个时间范围?",'
            '"confidence":0.9,"reason":"r"}'
        )
        assert parsed is not None
        intent, confidence, reason, ambiguous, clarify = parsed
        assert (intent, confidence, reason) == ("data", 0.9, "r")
        assert ambiguous is True and clarify == "哪个时间范围?"

    def test_missing_fields_default_to_legacy_shape(self) -> None:
        """旧形态(无 ambiguous/clarify)照常解析——链路脚本兼容。"""
        parsed = intent_nodes.parse_intent_route('{"intent":"data","confidence":0.99}')
        assert parsed is not None
        assert parsed[0] == "data" and parsed[3] is False and parsed[4] == ""

    def test_ambiguous_without_clarify_falls_open(self) -> None:
        """歧义判定无澄清问句 → 不可执行,收 false(fail-open)。"""
        parsed = intent_nodes.parse_intent_route(
            '{"intent":"data","ambiguous":true,"confidence":0.9}'
        )
        assert parsed is not None and parsed[3] is False

    def test_chitchat_is_valid_intent(self) -> None:
        parsed = intent_nodes.parse_intent_route(
            '{"intent":"chitchat","confidence":0.95,"ambiguous":false}'
        )
        assert parsed is not None and parsed[0] == "chitchat"

    def test_invalid_still_rejected(self) -> None:
        assert intent_nodes.parse_intent_route('{"intent":"bogus","confidence":0.9}') is None
        assert intent_nodes.parse_intent_route('{"intent":"data","confidence":2.0}') is None


# ============ route_intent 双兜底出口 ============


class TestRouteIntentFallbacks:
    async def test_semantic_chitchat_exits_early(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """词表漏网闲聊(「太感谢了」)被语义层接住:零检索零执行。"""
        fake = FakeLlm([_ROUTE_JSON % ("chitchat", "false", "")])
        monkeypatch.setattr(deps, "get_llm", lambda: fake)
        monkeypatch.setattr(deps, "get_api", FakeApi)
        before = intent_nodes.snapshot_intent_route_stats()["chitchat_semantic_total"]
        out = await intent_nodes.route_intent(_state(question="太感谢了"))
        assert out["end_reason"] == "no_data"
        assert out["fallback_type"] == FALLBACK_CHITCHAT
        assert out["error"] == dialogue.CHITCHAT_SEMANTIC_REPLY
        after = intent_nodes.snapshot_intent_route_stats()["chitchat_semantic_total"]
        assert after == before + 1
        assert len(fake.calls) == 1  # 单次模型调用(搭意图路由帧)

    async def test_ambiguous_question_clarifies(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeLlm([_ROUTE_JSON % ("data", "true", "想看哪个时间范围的销售数据?")])
        monkeypatch.setattr(deps, "get_llm", lambda: fake)
        monkeypatch.setattr(deps, "get_api", FakeApi)
        out = await intent_nodes.route_intent(_state(question="帮我看看销售情况"))
        assert out["end_reason"] == "no_data"
        assert out["fallback_type"] == FALLBACK_CLARIFY
        assert out["error"] == "想看哪个时间范围的销售数据?"

    async def test_clarify_loop_guard(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """上一轮已澄清 → 本轮不再命中(照常链路,防循环硬门)。"""
        fake = FakeLlm([_ROUTE_JSON % ("data", "true", "哪个范围?")])
        monkeypatch.setattr(deps, "get_llm", lambda: fake)
        monkeypatch.setattr(deps, "get_api", FakeApi)
        out = await intent_nodes.route_intent(
            _state(question="上个月的", last_fallback=FALLBACK_CLARIFY)
        )
        assert out.get("end_reason") is None
        assert out.get("intent") == "data"  # 正常路由产物

    async def test_chitchat_priority_over_ambiguous(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """同帧冲突:闲聊优先(无取数意图的输入无从澄清口径)。"""
        fake = FakeLlm([_ROUTE_JSON % ("chitchat", "true", "哪个范围?")])
        monkeypatch.setattr(deps, "get_llm", lambda: fake)
        monkeypatch.setattr(deps, "get_api", FakeApi)
        out = await intent_nodes.route_intent(_state(question="你好呀"))
        assert out["fallback_type"] == FALLBACK_CHITCHAT

    async def test_llm_failure_fails_open(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """模型失败 → 不澄清不判闲聊,照常链路(fail-open 与现态一致)。"""
        fake = FakeLlm(boom=True)
        monkeypatch.setattr(deps, "get_llm", lambda: fake)
        monkeypatch.setattr(deps, "get_api", FakeApi)
        before = intent_nodes.snapshot_intent_route_stats()["intent_route_fallback_total"]
        out = await intent_nodes.route_intent(_state(question="帮我看看销售情况"))
        assert out == {}
        after = intent_nodes.snapshot_intent_route_stats()["intent_route_fallback_total"]
        assert after == before + 1

    async def test_low_confidence_fails_open(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeLlm(
            ['{"intent":"data","ambiguous":true,"clarify_question":"?","confidence":0.3}']
        )
        monkeypatch.setattr(deps, "get_llm", lambda: fake)
        monkeypatch.setattr(deps, "get_api", FakeApi)
        out = await intent_nodes.route_intent(_state(question="帮我看看销售情况"))
        assert out == {}  # 低置信不命中澄清,照常链路


# ============ 图拓扑:早退边与数据路径不变 ============


class TestTopology:
    def test_route_exit_edges(self) -> None:
        assert build._after_route({"end_reason": "no_data"}) == "fail"  # type: ignore[arg-type]
        assert build._after_route({}) == "retrieve"  # type: ignore[arg-type]

    def test_data_path_router_unchanged(self) -> None:
        assert build._after_understand({}) == "route_intent"  # type: ignore[arg-type]
        assert build._after_understand({"end_reason": "no_data"}) == "fail"  # type: ignore[arg-type]
