"""067 保留表清单单源化:对象恒等 + 三态拦截 + 防误伤 + 引擎窄清单降级。

安全规则类(config.yaml rules)测试先行:先红后绿。
"""

from types import SimpleNamespace

import pytest

from kongming.sqlfw import firewall, guard
from kongming.sqlfw.firewall import check, check_reserved_tables

# ============ 1.1 / 1.2 对象恒等(不是值相等) ============


def test_reserved_constants_are_liannu_objects() -> None:
    """api 防火墙与 liannu 引擎必须读到同一常量对象(第二处定义即红)。"""
    from liannu import rules as liannu_rules

    assert firewall.RESERVED_TABLE_PREFIXES is liannu_rules.RESERVED_TABLE_PREFIXES
    assert firewall.RESERVED_TABLE_NAMES is liannu_rules.RESERVED_TABLE_NAMES
    assert firewall.RESERVED_SCHEMA_NAMES is liannu_rules.RESERVED_SCHEMA_NAMES
    assert firewall._READONLY_ROOTS is liannu_rules.READONLY_ROOTS  # pyright: ignore[reportPrivateUsage]
    assert firewall._FORBIDDEN_FUNCS is liannu_rules.FORBIDDEN_FUNCS  # pyright: ignore[reportPrivateUsage]


def test_reserved_list_covers_skill_and_user_prefs() -> None:
    from liannu import rules as liannu_rules

    assert "skill_" in liannu_rules.RESERVED_TABLE_PREFIXES
    assert "user_prefs" in liannu_rules.RESERVED_TABLE_NAMES


def test_identity_assertion_has_teeth() -> None:
    """防回归:影子常量内容相等但不是同一对象时,恒等断言必须失败。"""
    from liannu import rules as liannu_rules

    # 注意:frozenset(已有 frozenset) 会返回同一对象,须走 list 强制新对象
    shadow = frozenset(list(liannu_rules.RESERVED_TABLE_NAMES))
    assert shadow == liannu_rules.RESERVED_TABLE_NAMES
    assert shadow is not liannu_rules.RESERVED_TABLE_NAMES


# ============ 1.3 / 1.4 三态拦截与防误伤 ============

RESERVED_SQLS = [
    "SELECT * FROM skill_pack",
    "SELECT glossary FROM public.skill_pack",
    "SELECT * FROM user_prefs",
    "SELECT * FROM skill_terms",  # skill_ 前缀
    "SELECT * FROM goods JOIN user_prefs u ON u.id = 1",  # JOIN 夹带
    "WITH user_prefs AS (SELECT 1 AS id) SELECT * FROM user_prefs",  # CTE 遮蔽
]


BUSINESS_SQLS = [
    "SELECT * FROM pack",
    "SELECT * FROM prefs",
    "SELECT * FROM user_preferences",
    "SELECT * FROM skill",
    "SELECT * FROM goods",
    "SELECT * FROM customer",
]


@pytest.mark.parametrize("mode", ["builtin", "shadow", "primary"])
def test_reserved_blocked_in_all_modes(mode: str) -> None:
    guard.configure(mode)
    try:
        for sql in RESERVED_SQLS:
            v = check_reserved_tables(sql)
            assert not v.passed, f"[{mode}] {sql!r} 应拦截,实际 {v!r}"
            assert v.reason is not None and v.reason.startswith("RESERVED_TABLE")
    finally:
        guard.configure("builtin")


@pytest.mark.parametrize("mode", ["builtin", "shadow", "primary"])
def test_business_tables_not_falsely_blocked(mode: str) -> None:
    guard.configure(mode)
    try:
        for sql in BUSINESS_SQLS:
            v = check_reserved_tables(sql)
            assert v.passed, f"[{mode}] {sql!r} 应放行,实际 {v!r}"
            # 只读根判定也不误伤
            v2 = check(sql)
            assert v2.passed, f"[{mode}] {sql!r} 防火墙应放行,实际 {v2!r}"
    finally:
        guard.configure("builtin")


# ============ 1.5 引擎窄清单 → 降级 ============


def test_narrow_engine_list_degrades_to_builtin() -> None:
    """引擎导出的清单窄于权威即视为不可用:请求级降级 + ENGINE_FALLBACK。"""
    from liannu import rules as liannu_rules

    # 构造「旧版引擎」:自检能过(冒烟 SQL 用不到 skill_/user_prefs),但清单窄
    narrow = SimpleNamespace(
        __version__="0.0.1-old",
        RESERVED_TABLE_PREFIXES=("sys_", "chat_"),
        RESERVED_TABLE_NAMES=frozenset({"alembic_version", "user_identities", "user_memory"}),
        RESERVED_SCHEMA_NAMES=frozenset({"rag"}),
        READONLY_ROOTS=liannu_rules.READONLY_ROOTS,
        FORBIDDEN_FUNCS=liannu_rules.FORBIDDEN_FUNCS,
    )

    assert guard._reserved_lists_match(narrow) is False  # pyright: ignore[reportPrivateUsage]

    # 宽清单(当前权威)自检通过
    assert guard._reserved_lists_match(liannu_rules) is True  # pyright: ignore[reportPrivateUsage]
