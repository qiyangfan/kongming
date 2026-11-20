"""feedback 领域模型与纯函数,不依赖 Web 框架或数据库。

覆盖三态合并/处置状态枚举/涉及表抽取(sqlglot 解析库,非框架依赖)。
"""

from typing import TypedDict

import sqlglot
from sqlglot import exp

VALID_RATINGS = ("UP", "DOWN")
VALID_HANDLE_STATUS = ("PENDING", "ADOPTED", "REJECTED", "DISMISSED")


class TrendItem(TypedDict):
    """trend 逐天项(Asia/Shanghai 日历日,无反馈补零)。"""

    day: str
    up: int
    down: int


class OverviewData(TypedDict):
    """统计概览返回形态(字段名沿 spec 立约口径,传输层 snake 化在 router)。"""

    total: int
    upCount: int
    downCount: int
    correctedCount: int
    downRate: float
    trend: list[TrendItem]


def merge_text(current: str | None, incoming: str | None) -> str | None:
    """三态合并(feedback spec 幂等覆盖):None=保持现值;""=清空;非空=覆盖。"""
    if incoming is None:
        return current
    if incoming == "":
        return None
    return incoming


def extract_table_names(*sql_texts: str) -> tuple[set[str], bool]:
    """从 SQL 文本集抽取表名(末段名小写折叠,PG 未加引号标识符语义;
    CTE 名排除——与防火墙同一豁免口径)。返回 (表名集, 是否有解析失败)。
    空串/全空白跳过;任一解析失败不阻断其余(parseError 兜底语义,spec)。
    """
    tables: set[str] = set()
    parse_error = False
    for sql in sql_texts:
        if not sql or not sql.strip():
            continue
        try:
            statements = list(sqlglot.parse(sql, read="postgres"))
        except Exception:  # noqa: BLE001  # sqlglot 异常族(SqlglotError)兜底,解析失败不炸定位
            parse_error = True
            continue
        for stmt in statements:
            if stmt is None:
                continue
            cte_names = {c.alias_or_name.lower() for c in stmt.find_all(exp.CTE) if c.alias_or_name}
            for tbl in stmt.find_all(exp.Table):
                name = tbl.name
                if not name:
                    continue
                lowered = name.lower()
                if lowered in cte_names:
                    continue
                tables.add(lowered)
    return tables, parse_error
