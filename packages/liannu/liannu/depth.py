"""限制 SQL 解析的嵌套深度，避免深层表达式或子查询触发递归溢出。默认上限为 32；
超限在调用 sqlglot 前拒绝，解析期间出现 `RecursionError` 也转换为受控拒绝。"""

DEFAULT_MAX_DEPTH = 32


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
        if ch == "'":  # 单引号字符串,'' 转义
            i += 1
            while i < n:
                if sql[i] == "'":
                    if i + 1 < n and sql[i + 1] == "'":
                        i += 2
                        continue
                    break
                i += 1
        elif ch == '"':  # 双引号标识符
            i += 1
            while i < n:
                if sql[i] == '"':
                    if i + 1 < n and sql[i + 1] == '"':
                        i += 2
                        continue
                    break
                i += 1
        elif ch == "$":  # Dollar 引用($tag$…$tag$;$$…$$ 为空 tag;tag 首字符禁 digit)
            j = i + 1
            while j < n and (sql[j].isalnum() or sql[j] == "_"):
                j += 1
            valid_tag = j < n and sql[j] == "$" and (j == i + 1 or not sql[i + 1].isdigit())
            if valid_tag:
                tag = sql[i : j + 1]
                end = sql.find(tag, j + 1)
                if end == -1:
                    return max_depth  # 未闭合:不再有可判定的括号语义
                i = end + len(tag) - 1
        elif ch == "-" and sql[i : i + 2] == "--":  # 行注释
            nl = sql.find("\n", i)
            if nl == -1:
                return max_depth
            i = nl
        elif ch == "/" and sql[i : i + 2] == "/*":  # 块注释(PG 可嵌套)
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
        elif ch == "(":
            depth += 1
            if depth > max_depth:
                max_depth = depth
        elif ch == ")":
            depth = max(0, depth - 1)
        i += 1
    return max_depth
