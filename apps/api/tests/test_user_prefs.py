"""059 用户偏好闭合槽位纯函数测试(安全规则测试先行)。"""

from __future__ import annotations

from kongming.memory import prefs as p


class TestNormalizePatch:
    def test_legal_slots_kept_unknown_dropped(self) -> None:
        patch = p.normalize_patch(
            {
                "preferred_datasource": "sakila",
                "evil_key": "x",
                "metric_aliases": ["GMV=成交金额"],
                "display_style": "结论先行",
            }
        )
        assert patch.values["preferred_datasource"] == "sakila"
        assert "evil_key" not in patch.values
        assert patch.values["metric_aliases"] == ["GMV=成交金额"]

    def test_empty_patch_not_written(self) -> None:
        assert p.normalize_patch({"evil": 1, "preferred_datasource": ""}).is_empty()

    def test_merge_overwrite_semantics(self) -> None:
        cur = {"preferred_datasource": "a", "display_style": "全表"}
        merged = p.merge_prefs(cur, {"preferred_datasource": "b"})
        assert merged["preferred_datasource"] == "b"
        assert merged["display_style"] == "全表"

    def test_reject_unknown_keys(self) -> None:
        assert p.reject_unknown_keys({"preferred_datasource": "x"}) is None
        assert "未知" in str(p.reject_unknown_keys({"foo": 1}))


class TestImplicitExtraction:
    def test_free_text_fact_rejected(self) -> None:
        patch = p.extract_slot_patch_from_implicit(
            {
                "display_style": "上月销售额下降 20%",
                "preferred_datasource": "sakila",
                "note": "用户喜欢猫",
            }
        )
        assert "display_style" not in patch.values
        assert patch.values.get("preferred_datasource") == "sakila"

    def test_all_empty_no_write(self) -> None:
        assert p.extract_slot_patch_from_implicit({"foo": "bar"}).is_empty()


class TestPreferenceHint:
    def test_zero_slots_zero_inject(self) -> None:
        assert p.build_preference_hint({}) == ""

    def test_inject_has_non_constraint_prefix(self) -> None:
        hint = p.build_preference_hint(
            {"preferred_datasource": "sakila", "default_time_range": "近7天"}
        )
        assert "可忽略" in hint
        assert "不得作为 WHERE/数据范围约束" in hint
        assert "sakila" in hint
        assert "近7天" in hint


class TestExplicitRouting:
    def test_slot_intent_to_prefs(self) -> None:
        route, slot = p.route_explicit_intent("记住默认用 sakila 数据源")
        assert route == "prefs" and slot == "preferred_datasource"

    def test_free_text_to_note(self) -> None:
        route, slot = p.route_explicit_intent("记住:我喜欢结论先行")
        assert route == "prefs" and slot == "display_style"
        route2, slot2 = p.route_explicit_intent("记住:我家有只猫")
        assert route2 == "note" and slot2 is None

    def test_parse_explicit_slot_with_value(self) -> None:
        assert p.parse_explicit_slot("默认用 sakila 数据源") == (
            "preferred_datasource",
            "sakila",
        )
        assert p.parse_explicit_slot("我喜欢结论先行") == ("display_style", "结论先行")
        assert p.parse_explicit_slot("我家有只猫") is None

    def test_list_slot_accepts_json_string(self) -> None:
        patch = p.normalize_patch({"metric_aliases": '["GMV=成交额","AOV=客单价"]'})
        assert patch.values["metric_aliases"] == ["GMV=成交额", "AOV=客单价"]
        patch2 = p.normalize_patch({"metric_aliases": "GMV=成交额、AOV=客单价"})
        assert patch2.values["metric_aliases"] == ["GMV=成交额", "AOV=客单价"]
