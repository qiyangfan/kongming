"""覆盖文本转义与 SQL 模糊模式构造。"""

from collections.abc import Callable

import pytest

from kongming.escape_util import escape_like_literal
from kongming.sql_util import (
    contains_like_pattern,
    prefix_like_pattern,
    suffix_like_pattern,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("", ""),
        ("plain", "plain"),
        ("%", r"\%"),
        ("_", r"\_"),
        ("\\", r"\\"),
        ("100%_\\", r"100\%\_\\"),
    ],
)
def test_escape_like_literal(value: str, expected: str) -> None:
    assert escape_like_literal(value) == expected


@pytest.mark.parametrize(
    ("builder", "expected"),
    [
        (prefix_like_pattern, r"a\%b\_\\c%"),
        (suffix_like_pattern, r"%a\%b\_\\c"),
        (contains_like_pattern, r"%a\%b\_\\c%"),
    ],
)
def test_like_pattern_direction_and_escaping(builder: Callable[[str], str], expected: str) -> None:
    assert builder("a%b_\\c") == expected


@pytest.mark.parametrize(
    ("builder", "expected"),
    [
        (prefix_like_pattern, "%"),
        (suffix_like_pattern, "%"),
        (contains_like_pattern, "%%"),
    ],
)
def test_like_pattern_empty_input(builder: Callable[[str], str], expected: str) -> None:
    assert builder("") == expected
