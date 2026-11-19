"""限制 SQL 解析的嵌套深度，避免深层表达式或子查询触发递归溢出。默认上限为 32；
超限在调用 sqlglot 前拒绝，解析期间出现 `RecursionError` 也转换为受控拒绝。"""

DEFAULT_MAX_DEPTH = 32


def _skip_quoted(sql: str, i: int, quote: str) -> int:
    """跳过引号字符串/标识符,返回结束引号下标;未闭合返回末尾下标。"""
    n = len(sql)
    i += 1
    while i < n:
        if sql[i] == quote:
            if i + 1 < n and sql[i + 1] == quote:
                i += 2
                continue
            return i
        i += 1
    return i


def _skip_dollar_quote(sql: str, i: int) -> int | None:
    """跳过 Dollar 引用,返回结束 tag 的末字符下标;未闭合返回 None。

    无有效 tag 时原样返回,交由主循环当作普通字符处理。
    """
    n = len(sql)
    j = i + 1
    while j < n and (sql[j].isalnum() or sql[j] == "_"):
        j += 1
    valid_tag = j < n and sql[j] == "$" and (j == i + 1 or not sql[i + 1].isdigit())
    if not valid_tag:
        return i
    tag = sql[i : j + 1]
    end = sql.find(tag, j + 1)
    if end == -1:
        return None
    return end + len(tag) - 1


def _skip_line_comment(sql: str, i: int) -> int | None:
    """跳过 -- 行注释,返回换行符下标;无换行返回 None。"""
    nl = sql.find("\n", i)
    return None if nl == -1 else nl


def _skip_block_comment(sql: str, i: int) -> int:
    """跳过可嵌套的 /* */ 块注释,返回闭合后的下标。"""
    n = len(sql)
    nest = 1
    i += 2
    while i < n and nest > 0:
        if sql[i : i + 2] == "/*":
            nest += 1
            i += 2
        elif sql[i : i + 2] == "*/":
            nest -= 1
            i += 2
        else:
            i += 1
    return i


def _skip_nonparen_token(sql: str, i: int) -> int | None:
    """跳过引号/注释/Dollar 引用,返回 token 末字符下标;未闭合返回 None。

    普通字符原样返回,由主循环按单字符步进。
    """
    ch = sql[i]
    if ch == "'":
        return _skip_quoted(sql, i, "'")
    if ch == '"':
        return _skip_quoted(sql, i, '"')
    if ch == "$":
        return _skip_dollar_quote(sql, i)
    if sql.startswith("--", i):  # 行注释
        return _skip_line_comment(sql, i)
    if sql.startswith("/*", i):  # 块注释(PG 可嵌套)
        return _skip_block_comment(sql, i)
    return i


def max_paren_depth(sql: str) -> int:
    """轻量词法扫描:返回圆括号最大嵌套深度(不计引号内/注释内括号)。

    状态机跳过:单引号字符串('' 转义)、双引号标识符、PG  Dollar 引用
    ($tag$…$tag$)、行注释 --、块注释 /* */(PG 支持嵌套,计层配对)。
    """
    depth = 0
    max_depth = 0
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        if ch == "(":
            depth += 1
            max_depth = max(max_depth, depth)
        elif ch == ")":
            depth = max(0, depth - 1)
        else:
            end = _skip_nonparen_token(sql, i)
            if end is None:
                return max_depth  # 未闭合:不再有可判定的括号语义
            i = end
        i += 1
    return max_depth
