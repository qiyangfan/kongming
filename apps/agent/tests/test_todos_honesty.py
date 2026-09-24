"""058 多步 Todo 诚实账本纯函数测试(先红后绿)。"""

from __future__ import annotations

from kongming_agent import todos as td


class TestCreateAndSplit:
    def test_create_todos(self) -> None:
        items = td.create_todos(["查华东销售额", "算环比", ""])
        assert [t.id for t in items] == ["t1", "t2"]
        assert items[0].status == "PENDING"
        assert items[0].evidence == ""

    def test_split_goals_multi_question(self) -> None:
        goals = td.split_goals("华东销售额是多少？华北呢？")
        assert goals is not None and len(goals) == 2

    def test_split_goals_single_returns_none(self) -> None:
        assert td.split_goals("商品总共有多少个") is None

    def test_split_goals_and_connector(self) -> None:
        goals = td.split_goals("查一下华东销售额以及华北销售额")
        assert goals is not None and len(goals) == 2


class TestApplyToolResult:
    def test_done_requires_success_evidence(self) -> None:
        items = td.create_todos(["查A", "查B"])
        out = td.apply_tool_result(items, success=True, evidence="c1:execute_sql:ok")
        assert out[0].status == "DONE"
        assert out[0].evidence == "c1:execute_sql:ok"
        assert out[1].status == "PENDING"

    def test_failure_not_done(self) -> None:
        items = td.create_todos(["查A"])
        out = td.apply_tool_result(items, success=False, evidence="", reason="BLOCKED")
        assert out[0].status == "FAILED"
        assert out[0].reason == "BLOCKED"
        assert out[0].evidence == ""

    def test_empty_or_na_not_done(self) -> None:
        items = td.create_todos(["查A", "查B"])
        out = td.apply_tool_result(items, success=True, evidence="c1", empty=True)
        assert out[0].status == "SKIPPED"
        out2 = td.apply_tool_result(
            out, success=True, evidence="c2", not_applicable=True, reason="无此能力"
        )
        assert out2[1].status == "SKIPPED"
        assert out2[1].reason == "无此能力"

    def test_budget_exit_keeps_pending(self) -> None:
        items = td.create_todos(["查A", "查B", "查C"])
        items = td.apply_tool_result(items, success=True, evidence="c1:ok")
        items = td.apply_tool_result(items, success=True, evidence="c2:ok")
        after = td.mark_pending_on_budget_exit(items)
        assert after[2].status == "PENDING"
        assert not td.all_done(after)
        assert td.has_open_items(after)


class TestFinalizeHonesty:
    def test_all_done_passthrough(self) -> None:
        items = td.create_todos(["查A"])
        items = td.apply_tool_result(items, success=True, evidence="c1:ok")
        text = "查A结果为 10。"
        out, viol = td.assert_finalize_honest(items, text)
        assert out == text
        assert viol is None

    def test_no_todos_zero_regression(self) -> None:
        text = "已全部完成。"
        out, viol = td.assert_finalize_honest([], text)
        assert out == text
        assert viol is None

    def test_open_item_blocks_all_done_claim(self) -> None:
        items = td.create_todos(["查A", "查B"])
        items = td.apply_tool_result(items, success=True, evidence="c1:ok")
        items = td.apply_tool_result(items, success=False, evidence="", reason="工具失败")
        out, viol = td.assert_finalize_honest(items, "以上已全部完成。查A结果 10。")
        assert viol is not None
        assert "已全部完成" not in out
        assert "查B" in out
        assert "FAILED" in out or "工具失败" in out

    def test_open_disclosure_lists_pending(self) -> None:
        items = td.create_todos(["查A", "查B"])
        items = td.apply_tool_result(items, success=True, evidence="c1:ok")
        out, _ = td.assert_finalize_honest(items, "查A完成。")
        assert "未完成事项" in out
        assert "查B" in out
