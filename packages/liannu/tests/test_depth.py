"""深度门测试:预扫描词法正确性 + 受控拒绝契约。"""

import pytest

from liannu import DEFAULT_MAX_DEPTH, max_paren_depth, parse


def test_max_paren_depth_basic() -> None:
    assert max_paren_depth("SELECT 1") == 0
    assert max_paren_depth("SELECT abs(1)") == 1
    assert max_paren_depth("SELECT * FROM (SELECT abs(1) FROM (SELECT 1) t) s") == 2


def test_max_paren_depth_skips_strings_and_comments() -> None:
    # 引号内的括号不是结构括号
    assert max_paren_depth("SELECT '(' FROM t") == 0
    assert max_paren_depth("SELECT ')(' FROM t") == 0
    assert max_paren_depth('SELECT "(" FROM t') == 0
    assert max_paren_depth("SELECT 'it''s (nested' FROM t") == 0
    # 注释内的括号不算
    assert max_paren_depth("SELECT 1 -- ( ( (\n, /* (( */ FROM t") == 0
    # 嵌套块注释(PG 语义)正确配对后才恢复计数
    assert max_paren_depth("SELECT 1 /* /* ( */ ( */ + (2)") == 1
    # dollar 引用内的括号不算
    assert max_paren_depth("SELECT $fn$ ( ( ( $fn$ FROM t") == 0
    # PG 函数参数占位符 $1(非 dollar quote:tag 首字符禁 digit)不吞后续括号
    assert max_paren_depth("SELECT f($1, (2)) FROM t") == 2
    # $1$ 的 tag 以 digit 开头,PG 词法非 dollar quote:两个括号都是结构括号
    assert max_paren_depth("SELECT f($1$, (3)) FROM t") == 2


def test_depth_exceeded_controlled_before_parse() -> None:
    sql = "SELECT " + "*" + " FROM (" * 40 + "t" + ")" * 40
    art = parse(sql, max_depth=8)
    assert art.depth_exceeded
    v = art.check_firewall()
    assert not v.passed
    assert v.reason is not None and v.reason.startswith("DEPTH_EXCEEDED")
    # 未过解析门:判定三闸同形态,主表提取安全返 None
    assert not art.check_reserved_tables().passed
    assert art.main_table_ref() is None


def test_depth_within_limit_parses() -> None:
    sql = "SELECT * FROM (SELECT 1) t"
    art = parse(sql, max_depth=DEFAULT_MAX_DEPTH)
    assert not art.depth_exceeded
    assert art.check_firewall().passed


def test_recursion_error_mapped_to_controlled_reject() -> None:
    """括号外深嵌套(预扫描放行)由解析期 RecursionError 兜底,收敛受控拒绝。

    显式提高 max_depth 以通过预扫描,验证解析期 RecursionError 会转为受控拒绝。
    """
    sql = "SELECT " + "abs(" * 60 + "1" + ")" * 60
    art = parse(sql, max_depth=200)
    assert art.depth_exceeded
    v = art.check_firewall()
    assert not v.passed
    assert v.reason is not None and v.reason.startswith("DEPTH_EXCEEDED")


def test_apply_scope_filter_rejected_on_bad_artifact() -> None:
    from liannu import GuardError

    with pytest.raises(GuardError):
        parse("SELECT * FROM (SELECT 1) t" * 0 + "(" * 20, max_depth=5).apply_scope_filter(
            filter_type="USER_EQ", column="create_by", values=["u"]
        )
