"""迁移 DDL 约定(ADR-004):状态列禁魔法数、时间列必须带时区、主键不用自增。

扫描 alembic 版本文件里显式 SQL 与 SQLAlchemy 列定义,拦截回归写法:
PG ENUM 类型、SMALLINT/INTEGER 状态列、无时区 TIMESTAMP(及裸 sa.DateTime())、
SERIAL 自增主键。主键由应用侧生成 UUID v7,时间列统一 TIMESTAMPTZ。
"""

import re
from pathlib import Path

VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"

CHECKS: list[tuple[str, re.Pattern[str]]] = [
    ("PG ENUM 类型", re.compile(r"CREATE\s+TYPE\s+\S+\s+AS\s+ENUM", re.IGNORECASE)),
    ("状态列数值魔法", re.compile(r"\bstatus\w*\s+(?:SMALLINT|INTEGER)\b", re.IGNORECASE)),
    (
        "状态列数值魔法(SQLAlchemy)",
        re.compile(r"sa\.Column\(\s*[\"']status\w*[\"']\s*,\s*sa\.(?:Small)?Integer"),
    ),
    ("无时区 TIMESTAMP", re.compile(r"\bTIMESTAMP\b(?!\s+WITH\s+TIME\s+ZONE)", re.IGNORECASE)),
    ("无时区 sa.TIMESTAMP()", re.compile(r"sa\.TIMESTAMP\(\s*\)")),
    ("自增 SERIAL 主键", re.compile(r"\b(?:BIG|SMALL)?SERIAL\b", re.IGNORECASE)),
    ("无时区 sa.DateTime()", re.compile(r"sa\.DateTime\(\s*\)")),
]


def test_migrations_follow_adr_004() -> None:
    """任一版本文件命中禁用写法即失败,并给出文件与行号。"""
    violations: list[str] = []
    for path in sorted(VERSIONS.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for name, pattern in CHECKS:
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                violations.append(f"{path.name}:{line} {name}: {match.group(0)}")
    assert violations == []
