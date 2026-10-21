"""ILIKE 模式构造共享纯函数(域无关,零依赖)。

 :不转义时 %/_ 被当通配符展开,搜索语义失真(搜「_」命中全表)。
system/datasource/chat 三域仓储统一经此构造(单一真理源,上移)。
"""


def like_pattern(keyword: str) -> str:
    """ILIKE 模式构造:元字符(%/_/\\)转义为字面量,keyword 只做包含匹配。"""
    escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"
