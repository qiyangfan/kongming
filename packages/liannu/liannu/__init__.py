"""liannu:Kongming SQL 安全引擎(单遍解析,PG 方言,纯计算零 IO)。"""

from liannu.artifact import GuardArtifact, GuardError
from liannu.depth import DEFAULT_MAX_DEPTH, max_paren_depth
from liannu.rules import (
    FORBIDDEN_FUNCS,
    READONLY_ROOTS,
    RESERVED_SCHEMA_NAMES,
    RESERVED_TABLE_NAMES,
    RESERVED_TABLE_PREFIXES,
)
from liannu.verdict import Verdict

__version__ = "0.1.3"

__all__ = [
    "DEFAULT_MAX_DEPTH",
    "FORBIDDEN_FUNCS",
    "GuardArtifact",
    "GuardError",
    "READONLY_ROOTS",
    "RESERVED_SCHEMA_NAMES",
    "RESERVED_TABLE_NAMES",
    "RESERVED_TABLE_PREFIXES",
    "Verdict",
    "__version__",
    "max_paren_depth",
    "parse",
    "self_check",
]


def parse(
    sql: str, *, dialect: str = "postgres", max_depth: int = DEFAULT_MAX_DEPTH
) -> GuardArtifact:
    """单遍解析入口:深度预扫描 → sqlglot 一次 parse → 可复用产物。"""
    return GuardArtifact.parse(sql, dialect=dialect, max_depth=max_depth)


def self_check() -> tuple[bool, str]:
    """启动自检:版本可读 + 冒烟解析与三闸判定。失败时消费方告警降级,不阻断启动。"""
    try:
        art = parse("SELECT name FROM goods WHERE id = 1")
        if not art.check_firewall().passed:
            return False, "冒烟 SQL 防火墙判定异常(应放行)"
        if not art.check_reserved_tables().passed:
            return False, "冒烟 SQL 保留表判定异常(应放行)"
        if art.main_table_ref() != ("goods", None):
            return False, "冒烟 SQL 主表提取异常"
        blocked = parse("DELETE FROM goods")
        if blocked.check_firewall().passed:
            return False, "冒烟 SQL 写拦截异常(应拒)"
        return True, __version__
    except Exception as e:  # 自检绝不向上抛——消费方以告警降级
        return False, f"{type(e).__name__}: {e}"
