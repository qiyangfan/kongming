"""056 skillpack 安全面测试(防火墙保留前缀 + 管理鉴权)。"""

from __future__ import annotations

from kongming.skillpack import models as m
from kongming.sqlfw.firewall import check_reserved_tables


class TestReservedSkillPrefix:
    def test_skill_tables_blocked(self) -> None:
        for sql in (
            "SELECT * FROM skill_pack",
            "SELECT glossary FROM public.skill_pack",
            "WITH s AS (SELECT * FROM public.skill_pack) SELECT * FROM s",
            "SELECT * FROM tenant_default.goods g JOIN skill_pack p ON true",
            "SELECT * FROM skill_pack_binding",
        ):
            v = check_reserved_tables(sql)
            assert not v.passed, sql
            assert v.reason is not None and v.reason.startswith("RESERVED_TABLE")

    def test_business_tables_not_overblocked(self) -> None:
        for sql in (
            "SELECT id FROM goods",
            "SELECT * FROM tenant_default.skills",  # 非 skill_ 前缀
            "SELECT * FROM skillmaster",  # skill 开头但非 skill_
        ):
            v = check_reserved_tables(sql)
            assert v.passed, sql


class TestProjectionZeroRegression:
    def test_empty_pack_projects_empty(self) -> None:
        p = m.build_projection([m.SkillPackContent()])
        assert p.skill_terms == "" and p.skill_examples == "" and p.skill_persona == ""
