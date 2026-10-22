"""文本转义工具。"""


def escape_like_literal(value: str) -> str:
    """转义 LIKE 模式中的字面量字符。"""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
