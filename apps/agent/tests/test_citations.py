"""068-doc-answer-citations agent 面测试:键派生/两级净化/降级/sources 投影。

宪法 Article III(先红后绿):键派生恒定性与两级匹配契约由本文件钉死。
"""

import pytest

from kongming_agent.graph.citations import (
    is_citation_key_shape,
    sanitize_citations,
    surviving_markers,
)


class TestShape:
    """任务 1.4:键形态判定(非键形态不被剥离)。"""

    @pytest.mark.parametrize("token", ["员工手册-A7F2", "a-B3F2", "手册-A7F2"])
    def test_key_shapes(self, token: str) -> None:
        assert is_citation_key_shape(token) is True

    @pytest.mark.parametrize("token", ["普通文本", "3 库存政策", "abc-xyz", "A7F2"])
    def test_non_key_shapes(self, token: str) -> None:
        assert is_citation_key_shape(token) is False


class TestSanitize:
    """任务 1.2:两级匹配(全等保留/后缀命中保留/皆不命中降级)。"""

    LEGAL = ["员工手册-A7F2", "运维手册-9F2E"]

    def test_exact_match_kept(self) -> None:
        r = sanitize_citations("答案一句【员工手册-A7F2】。", self.LEGAL)
        assert r.text == "答案一句【员工手册-A7F2】。"
        assert r.stripped == 0
        assert r.surviving_keys == ("员工手册-A7F2",)

    def test_suffix_match_canonicalized(self) -> None:
        """模型改写人读前缀(手册-A7F2),末段哈希后缀合法 → 归一为权威键
        (chat spec:正文与 sources 双向无幽灵——surviving 必须是合法键,
        doc_answer 的 by_key 精取才能命中,否则正文有标注而列表无条目)。"""
        r = sanitize_citations("答案一句【手册-A7F2】。", self.LEGAL)
        assert r.text == "答案一句【员工手册-A7F2】。"
        assert r.stripped == 0
        assert r.surviving_keys == ("员工手册-A7F2",)

    def test_no_ghost_between_text_and_surviving(self) -> None:
        """不变式:正文中每个键形态标记都必须是 surviving 中的合法键
        (投影单一来源——doc_answer 的 sources 由 surviving_keys 派生)。"""
        src = "全等【员工手册-A7F2】;改写【手册-A7F2】;编造【假键-0BAD】;普通【备注】。"
        r = sanitize_citations(src, self.LEGAL)
        assert r.surviving_keys == ("员工手册-A7F2",)
        for token in surviving_markers(r.text):
            if is_citation_key_shape(token):
                assert token in self.LEGAL, f"正文残留非权威键 {token}(幽灵条目)"

    def test_no_match_downgraded(self) -> None:
        """编造键降级为纯文本:剥括号、保留括号内文字、句子其余部分逐字不变。"""
        src = "依据【HR制度-DEAD】执行。"
        r = sanitize_citations(src, self.LEGAL)
        assert r.text == "依据HR制度-DEAD执行。"
        assert r.stripped == 1
        assert r.surviving_keys == ()

    def test_mixed(self) -> None:
        src = "一【员工手册-A7F2】二【手册-A7F2】三【假键-0BAD】四"
        r = sanitize_citations(src, self.LEGAL)
        # 二处后缀命中归一为权威键(068 修订:正文与 sources 双向无幽灵)
        assert r.text == "一【员工手册-A7F2】二【员工手册-A7F2】三假键-0BAD四"
        assert r.stripped == 1
        # 全等与后缀命中同归一 → 去重为单一权威键(同一文档一项来源,不重复)
        assert r.surviving_keys == ("员工手册-A7F2",)

    def test_empty_legal_skips_all(self) -> None:
        """任务 1.4:合法键为空整体跳过(零标记误伤)。"""
        src = "任何【员工手册-A7F2】都原样"
        r = sanitize_citations(src, [])
        assert r.text == src
        assert r.stripped == 0

    def test_plain_brackets_kept(self) -> None:
        """普通中括号文本(非键形态)不被降级(任务 1.4 第二条)。"""
        src = "价格【含税】与否【待确认】"
        r = sanitize_citations(src, self.LEGAL)
        assert r.text == src
        assert r.stripped == 0

    def test_empty_text(self) -> None:
        r = sanitize_citations("", self.LEGAL)
        assert r.text == "" and r.stripped == 0


class TestSurvivingProjection:
    """任务 1.5:sources 投影与正文存活标记一一对应(不重不漏)。"""

    def test_projection_matches_surviving(self) -> None:
        src = "一【员工手册-A7F2】二【员工手册-A7F2】三【运维手册-9F2E】"
        r = sanitize_citations(src, ["员工手册-A7F2", "运维手册-9F2E"])
        assert list(r.surviving_keys) == surviving_markers(r.text)
        # 去重:同一键多处标注只一项(不重);无幽灵(不漏)
        assert r.surviving_keys == ("员工手册-A7F2", "运维手册-9F2E")

    def test_downgraded_not_in_projection(self) -> None:
        src = "真【员工手册-A7F2】假【假键-0BAD】"
        r = sanitize_citations(src, ["员工手册-A7F2"])
        # 存活键只含真引用;编造键被剥括号降级(文字保留但非标记)
        assert r.surviving_keys == ("员工手册-A7F2",)
        assert r.text == "真【员工手册-A7F2】假假键-0BAD"
        assert "假键-0BAD" not in r.surviving_keys
        assert "假键-0BAD" not in surviving_markers(r.text)
