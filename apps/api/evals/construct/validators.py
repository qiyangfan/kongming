"""gold SQL 规范的纯函数校验器，供自动构造和手工新增用例共用。"""

import re

MAX_GOLD_ROWS = 1000

_ORDER_BY_RE = re.compile(r"\border\s+by\b", re.IGNORECASE)
_LIMIT_RE = re.compile(r"\blimit\s+(\d+)\b", re.IGNORECASE)
_SELECT_RE = re.compile(r"\bselect\s+(.*?)\s+from\b", re.IGNORECASE | re.DOTALL)


def check_tiebreaker(sql: str) -> str | None:
    """规范 1/5:ORDER BY ... LIMIT 必须带第二排序键;任何位置含 ORDER BY
    则外层必须保序。返回失败原因或 None。"""
    # 字面量先掩码:字面量内 'order by'/'limit' 字样会产出伪段/截断真段
    # (纯结构判定只认语法结构,不认数据内容)
    stripped = _mask_string_literals(sql.strip().rstrip(";"))
    if not _ORDER_BY_RE.search(stripped):
        return None
    # tiebreaker:每个「ORDER BY … LIMIT n」子句至少两个排序键。段提取用
    # 括号深度扫描(正则 [^()] 跨不过函数调用/子查询——ORDER BY length(name)
    # LIMIT 5 会整段失配漏拦);键切分同样括号感知(COALESCE(a,b) 内逗号
    # 不是键分隔,误计数会漏拦)
    for segment in _order_by_limit_segments(stripped):
        keys = _split_top_level_commas(segment)
        if len(keys) < 2:
            return "ORDER BY ... LIMIT 缺 tiebreaker(须追加唯一列作第二排序键,保证行序确定)"
    # 外层保序(规范 5 保守口径):含 ORDER BY 的 SQL,顶层查询也须保序。
    # 判定=是否存在括号深度 0 处的 ORDER BY(深度扫描;「最后一个 ) 之后」
    # 的旧尾段启发式会把函数调用的右括号误当子查询边界——ORDER BY
    # lower(name), id LIMIT 5 这类合法形态被误拦)
    if not _has_outer_order_by(stripped):
        return "SQL 内部含 ORDER BY 但外层不保序(子查询排序不保证外层行序)"
    return None


_LITERAL_RE = re.compile(r"'(?:[^']|'')*'")  # 单引号字面量(含 '' 转义)


def _mask_string_literals(sql: str) -> str:
    """字符串字面量内容掩码为空格(保留长度与引号,纯函数)——字面量里的
    'order by'/'limit' 字样不参与结构判定。"""
    return _LITERAL_RE.sub(lambda m: "'" + " " * (len(m.group(0)) - 2) + "'", sql)


def _has_outer_order_by(sql: str) -> bool:
    """顶层(括号深度 0)是否存在 ORDER BY(纯函数)。"""
    for m in _ORDER_BY_RE.finditer(sql):
        depth = 0
        for ch in sql[: m.start()]:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
        if depth == 0:
            return True
    return False


def _order_by_limit_segments(sql: str) -> list[str]:
    """按括号作用域拆分每个 ORDER BY 与其最近的 LIMIT 之间的片段。"""
    lowered = sql.lower()
    segments: list[str] = []
    for m in re.finditer(r"order\s+by", lowered):
        depth = 0
        for i in range(m.end(), len(sql)):
            ch = sql[i]
            if ch == "(":
                depth += 1
            elif ch == ")":
                if depth == 0:
                    break  # 子查询边界:该 ORDER BY 不带 LIMIT
                depth -= 1
            elif depth == 0 and re.match(r"limit\s+\d+", lowered[i:]):
                segments.append(sql[m.end() : i])
                break
    return segments


def _split_top_level_commas(segment: str) -> list[str]:
    """按顶层逗号切段(括号内逗号不算键分隔;空段丢弃)。"""
    parts: list[str] = []
    depth = 0
    cur: list[str] = []
    for ch in segment:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return [p for p in parts if p.strip()]


def check_schema_qualified(sql: str, schema: str) -> str | None:
    """规范 7:所有 FROM/JOIN 表名显式 schema 限定(`schema.table`)。"""
    tables = re.findall(
        r"\b(?:from|join)\s+([a-zA-Z_][\w]*(?:\.[a-zA-Z_][\w]*)?)", sql, re.IGNORECASE
    )
    for table in tables:
        if "." not in table:
            return f"表 {table} 未显式 schema 限定(gold 直连不设 search_path)"
        if table.split(".")[0].lower() != schema.lower():
            return f"表 {table} schema 与目标({schema})不一致"
    if not tables:
        return "未识别到 FROM/JOIN 表(非查询语句?)"
    return None


def check_projection_readable(sql: str) -> str | None:
    """规范 2 保守口径:拦「SELECT <单 ID 形态列> FROM」(裸投影 ID)。"""
    m = _SELECT_RE.search(sql)
    if not m:
        return None
    projection = m.group(1).strip()
    if re.fullmatch(
        r"(?:\w+\.)?(?:\w+_)?(?:id|no|code)\s*(?:as\s+\w+)?", projection, re.IGNORECASE
    ):
        return "投影裸 ID 列(须 JOIN 出业务可读名称列)"
    if re.fullmatch(r"\*", projection):
        return "gold 禁 SELECT *(列集不确定,匹配口径不稳)"
    return None


def check_row_cap(row_count: int) -> str | None:
    """校验 gold 结果集行数不超过 1000，避免与生成端上限不一致。"""
    if row_count == 0:
        return "gold 结果集为空(0 行)——问题在数据集上不可判定,换题或改问法"
    if row_count > MAX_GOLD_ROWS:
        return f"gold 结果集 {row_count} 行 >{MAX_GOLD_ROWS}(改 top-N 或聚合小集)"
    return None


def validate_candidate(*, sql: str, schema: str, row_count: int | None) -> list[str]:
    """静态规范校验全集(执行行数可选,None=执行验证未跑)。返回失败原因列表。"""
    failures: list[str] = []
    checks = (
        check_tiebreaker(sql),
        check_schema_qualified(sql, schema),
        check_projection_readable(sql),
        check_row_cap(row_count) if row_count is not None else None,
    )
    failures.extend(check for check in checks if check)
    return failures
