"""对话复语直通:规范化纯函数、全等判定、双入口一致性、可观测计数。

回归背景:补全提示词强制「补全为查询意图」,对"不用了/取消/好的"等不携带
取数意图的复语会臆造出上一轮问题并真的执行。本模块锁死:零模型调用、
全等匹配(取消订单等真实问法不误拦)、复语轮次按既有第四态收敛。
"""

# pyright: reportPrivateUsage=false

import pytest

from kongming_agent import deps
from kongming_agent.graph import dialogue, nodes
from tests.test_nodes import FakeApi, FakeLlm, _state

# ============ 1.1 规范化纯函数 ============


class TestNormalize:
    """去首尾与内部空白、去中英文句读、拉丁字母小写。"""

    def test_strips_surrounding_and_inner_whitespace(self) -> None:
        assert dialogue.normalize_dialogue_reply("  不用了  ") == "不用了"
        assert dialogue.normalize_dialogue_reply("never  mind") == "nevermind"

    def test_strips_chinese_and_ascii_punctuation(self) -> None:
        assert dialogue.normalize_dialogue_reply("不用了。") == "不用了"
        assert dialogue.normalize_dialogue_reply("好的,") == "好的"
        assert dialogue.normalize_dialogue_reply("谢谢!") == "谢谢"
        assert dialogue.normalize_dialogue_reply("hi~") == "hi"

    def test_lowers_latin_letters(self) -> None:
        assert dialogue.normalize_dialogue_reply("NO") == "no"
        assert dialogue.normalize_dialogue_reply("OK") == "ok"
        assert dialogue.normalize_dialogue_reply("Thanks") == "thanks"

    def test_spec_examples_normalize_to_vocabulary_shape(self) -> None:
        """「不用了。」「 NO, thanks 」「好的」均归一到词表形态(无空白/句读、拉丁小写)。"""
        assert dialogue.normalize_dialogue_reply("不用了。") == "不用了"
        assert dialogue.normalize_dialogue_reply(" NO, thanks ") == "nothanks"
        assert dialogue.normalize_dialogue_reply("好的") == "好的"

    def test_empty_and_punct_only_normalize_to_empty(self) -> None:
        assert dialogue.normalize_dialogue_reply("") == ""
        assert dialogue.normalize_dialogue_reply("。。。") == ""


# ============ 1.2 全等语义 ============


class TestEqualitySemantics:
    """全等匹配:复语起头的真实问法不得被判为复语(包含/前缀匹配即红)。"""

    @pytest.mark.parametrize(
        "question",
        [
            "不用了",
            "不用了。",
            "  不用了  ",
            "取消",
            "算了",
            "停",
            "好的",
            "好",
            "谢谢",
            "你好",
            "no",
            "NO",
            "nope",
            "cancel",
            "never mind",
            "nevermind",
            "thanks",
            "thank you",
            "thankyou",
            "got it",
            "gotit",
            "ok",
            "okay",
            "hello",
            "hi",
            "hey",
            "您好",
            "知道了",
            "明白",
            "收到",
            "多谢",
            "不需要",
            "不必",
            "终止",
        ],
    )
    def test_closed_vocabulary_members_are_dialogue_reply(self, question: str) -> None:
        assert dialogue.is_dialogue_reply(question) is True

    @pytest.mark.parametrize(
        "question",
        [
            "取消订单的门店有哪些",
            "确认切换后还能回退吗",
            "停掉库存不足的门店的促销",
            "不需要的商品有哪些",
            "好的那看看上海仓",
            "谢谢你们的库存数据",
            "你好请问有多少商品",
            "no sql please just explain",
            "cancel order status",
            "统计取消的订单数量",
            "查看终止的合同",
            "有多少商品",
            "上海仓的库存",
        ],
    )
    def test_real_queries_are_not_dialogue_reply(self, question: str) -> None:
        """包含/前缀匹配会误拦这些真实问法——必须全等。"""
        assert dialogue.is_dialogue_reply(question) is False

    def test_no_prefix_matching(self) -> None:
        """「取消」是复语,「取消订单…」不是——前缀匹配即失败。"""
        assert dialogue.is_dialogue_reply("取消") is True
        assert dialogue.is_dialogue_reply("取消订单") is False

    def test_no_substring_matching(self) -> None:
        """「好的」是复语,「好的那看看上海仓」不是——包含匹配即失败。"""
        assert dialogue.is_dialogue_reply("好的") is True
        assert dialogue.is_dialogue_reply("好的那看看上海仓") is False


# ============ 1.5 单轮/多轮入口判定一致 ============


class TestSingleAndMultiTurnConsistency:
    """判定只看当前问句,与 history 无关;understand 节点两种入口行为一致。"""

    def test_pure_function_ignores_history(self) -> None:
        assert dialogue.is_dialogue_reply("不用了") is True
        # 纯函数不接 history——判定与入口形态无关

    async def test_understand_short_circuits_without_history(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeLlm(["这不应该被调用"])
        api = FakeApi()
        monkeypatch.setattr(deps, "get_llm", lambda: fake)
        monkeypatch.setattr(deps, "get_api", lambda: api)
        out = await nodes.understand(_state(question="你好", history=[]))
        assert out["end_reason"] == "no_data"
        assert "resolved_question" not in out
        assert fake.calls == []  # 零模型调用

    async def test_understand_short_circuits_with_history(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeLlm(["这不应该被调用"])
        api = FakeApi()
        monkeypatch.setattr(deps, "get_llm", lambda: fake)
        monkeypatch.setattr(deps, "get_api", lambda: api)
        out = await nodes.understand(_state(question="不用了", history=["有多少商品"]))
        assert out["end_reason"] == "no_data"
        assert "resolved_question" not in out
        assert fake.calls == []


# ============ 1.3 零模型调用 / 零检索 / 零执行(节点层) ============


class TestZeroSideEffects:
    async def test_no_llm_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeLlm(["不应该被消费"])
        monkeypatch.setattr(deps, "get_llm", lambda: fake)
        monkeypatch.setattr(deps, "get_api", FakeApi)
        await nodes.understand(_state(question="谢谢"))
        assert fake.calls == []  # UNDERSTAND 任务未被发起

    async def test_sets_fourth_state_end_reason(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(deps, "get_llm", FakeLlm)
        monkeypatch.setattr(deps, "get_api", FakeApi)
        out = await nodes.understand(_state(question="取消"))
        assert out["end_reason"] == "no_data"  # 既有第四态,不新增终态取值
        # 兜底分类修正(spec「对话复语不进取数补全」):族别中性文案替代
        # 数据缺失话术,并携带 CHITCHAT 诊断标记
        assert out["error"] == dialogue.CHITCHAT_REPLIES["stop"]
        assert out["fallback_type"] == "CHITCHAT"


# ============ 1.6 可观测计数 ============


class TestObservableCounter:
    async def test_passthrough_increments_counter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(deps, "get_llm", FakeLlm)
        monkeypatch.setattr(deps, "get_api", FakeApi)
        before = dialogue.snapshot_passthrough_stats()["dialogue_reply_passthrough_total"]
        await nodes.understand(_state(question="好的"))
        after = dialogue.snapshot_passthrough_stats()["dialogue_reply_passthrough_total"]
        assert after == before + 1

    async def test_non_passthrough_does_not_increment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeLlm(["有多少商品"])
        monkeypatch.setattr(deps, "get_llm", lambda: fake)
        monkeypatch.setattr(deps, "get_api", FakeApi)
        before = dialogue.snapshot_passthrough_stats()["dialogue_reply_passthrough_total"]
        await nodes.understand(_state(question="有多少商品"))
        after = dialogue.snapshot_passthrough_stats()["dialogue_reply_passthrough_total"]
        assert after == before
        assert len(fake.calls) == 1  # 正常问句照常改写

    async def test_counter_delivered_with_trace_stats(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """直通计数随 trace 下发:命中信号进 stats(复用 QueryOut.stats 既有通道)。"""
        monkeypatch.setattr(deps, "get_llm", FakeLlm)
        monkeypatch.setattr(deps, "get_api", FakeApi)
        out = await nodes.understand(_state(question="hello", trace_id="tr-pt"))
        assert out["stats"] == {"dialogue_reply_passthrough": 1}
