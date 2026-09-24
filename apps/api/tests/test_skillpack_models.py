"""056 skillpack 领域纯函数测试(测试先行)。"""

from __future__ import annotations

from kongming.skillpack import models as m


class TestParseAndValidate:
    def test_validate_manifest(self) -> None:
        assert m.validate_manifest("finance_v1", "金融包", "1.0") is None
        assert m.validate_manifest("", "x", "1") is not None
        assert m.validate_manifest("bad key!", "x", "1") is not None

    def test_parse_four_sections(self) -> None:
        c = m.parse_content(
            {
                "glossary": [{"term": "GMV", "aliases": ["成交额"], "definition": "成交金额"}],
                "fewshots": [{"question": "上月 GMV", "sql": "SELECT sum(amount) FROM t"}],
                "persona": "结论先行",
                "metrics": [{"metric_name": "gmv", "sql_template": "sum(amount)"}],
                "evil": 1,
            }
        )
        assert len(c.glossary) == 1 and c.glossary[0].term == "GMV"
        assert len(c.fewshots) == 1
        assert c.persona == "结论先行"
        assert len(c.metrics) == 1

    def test_parse_drops_malformed(self) -> None:
        c = m.parse_content({"glossary": [{"definition": "无词"}], "fewshots": [{"question": "q"}]})
        assert c.glossary == [] and c.fewshots == []


class TestMergeAndCap:
    def test_multi_pack_caps(self) -> None:
        packs = [
            m.SkillPackContent(
                glossary=[m.GlossaryItem(term=f"t{i}") for i in range(40)],
                fewshots=[m.Fewshot(question=f"q{i}", sql="SELECT 1") for i in range(8)],
                persona="甲" * 500,
            ),
            m.SkillPackContent(
                glossary=[m.GlossaryItem(term=f"u{i}") for i in range(40)],
                fewshots=[m.Fewshot(question=f"v{i}", sql="SELECT 2") for i in range(8)],
                persona="乙" * 500,
            ),
        ]
        merged = m.merge_packs(packs)
        assert len(merged.glossary) == 50
        assert len(merged.fewshots) == 10
        assert len(merged.persona) == 800

    def test_zero_pack_empty_projection(self) -> None:
        p = m.build_projection([])
        assert p.skill_terms == "" and p.skill_examples == "" and p.skill_persona == ""


class TestProjection:
    def test_examples_have_ban_prefix(self) -> None:
        p = m.build_projection(
            [
                m.SkillPackContent(
                    fewshots=[m.Fewshot(question="上月 GMV", sql="SELECT 1 FROM goods")]
                )
            ]
        )
        assert "表名列名不可照抄" in p.skill_examples
        assert "上月 GMV" in p.skill_examples

    def test_metric_conflict(self) -> None:
        conflicts = m.metric_name_conflicts(
            [m.MetricSpec(metric_name="gmv", sql_template="x")],
            ["GMV", "aov"],
        )
        assert conflicts == ["gmv"]
        assert (
            m.metric_name_conflicts([m.MetricSpec(metric_name="new", sql_template="x")], ["gmv"])
            == []
        )

    def test_metric_reenable_same_definition_idempotent(self) -> None:
        """同名同定义=幂等重启用放行;定义不一致才冲突(禁用不删指标后重启用)。"""
        incoming = [m.MetricSpec(metric_name="gmv", sql_template="sum(a)", business_terms=["成交"])]
        same = [
            {
                "metric_name": "GMV",
                "sql_template": "sum(a)",
                "business_terms": ["成交"],
            }
        ]
        assert m.metric_name_conflicts(incoming, same) == []
        other = [
            {
                "metric_name": "gmv",
                "sql_template": "sum(b)",
                "business_terms": ["成交"],
            }
        ]
        assert m.metric_name_conflicts(incoming, other) == ["gmv"]

    def test_parse_content_accepts_json_string(self) -> None:
        """asyncpg jsonb 常回 str,parse_content 须能吃 str(content)。"""
        c = m.parse_content(
            '{"glossary":[{"term":"GMV","definition":"成交金额"}],"persona":"结论先行"}'
        )
        assert len(c.glossary) == 1 and c.glossary[0].term == "GMV"
        assert c.persona == "结论先行"
