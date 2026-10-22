"""SQL 查询相关文本模式工具。"""

from kongming.escape_util import escape_like_literal


def prefix_like_pattern(value: str) -> str:
    """构造只匹配输入前缀的 LIKE 模式。"""
    return f"{escape_like_literal(value)}%"


def suffix_like_pattern(value: str) -> str:
    """构造只匹配输入后缀的 LIKE 模式。"""
    return f"%{escape_like_literal(value)}"


def contains_like_pattern(value: str) -> str:
    """构造匹配输入任意位置的 LIKE 模式。"""
    return f"%{escape_like_literal(value)}%"
