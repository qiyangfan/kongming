"""行级改写结构:最外层 WHERE 注入(与消费方 kongming datascope.rewrite 一比一移植)。

设计契约(与内置实现逐字一致):
- 过滤谓词 AND 进最外层查询的 WHERE,列引用限定主表别名(JOIN 防歧义);
- UNION / FROM 为子查询等取不到主表的形态,由调用方上游安全降级;
- 谓词值一律命名绑定参数(:km_ds_N),SQL 文本不含任何过滤值(防注入);
- 命名参数经方言子类保留 :name 风格(postgres 方言默认渲 pyformat)。
"""

from collections.abc import Sequence

from sqlglot import Expr, exp
from sqlglot.dialects.postgres import Postgres

PARAM_PREFIX = "km_ds_"

# 过滤意图(与消费方 FilterType 同值集):SKIP/UNSUPPORTED 原样返回;
# USER_EQ 等值;其余(DEPT_IN)IN 多值——else 归 IN 与内置实现一致
FILTER_SKIP = ("SKIP", "UNSUPPORTED")
FILTER_USER_EQ = "USER_EQ"


class _NamedParamPG(Postgres):
    """postgres 方言变体:占位符渲 :name(SQLAlchemy text() 参数风格)。"""

    # sqlglot 官方扩展点;存根把 Generator 声明为变量而非类型,关闭两码
    class Generator(Postgres.Generator):  # type: ignore[misc, valid-type]
        def placeholder_sql(self, expression: exp.Placeholder) -> str:
            return f":{expression.name}"


def main_table_ref(statement: exp.Expression | None) -> tuple[str, str | None] | None:
    """最外层 Select 的 FROM 为裸表时返回 (表名, 显式 schema 或 None);

    UNION/子查询 FROM 等取不到返 None。显式 schema 供调用方收窄列探测范围。
    """
    if not isinstance(statement, exp.Select):
        return None
    from_ = statement.args.get("from_")
    if from_ is None or not isinstance(from_.this, exp.Table):
        return None
    table = from_.this
    return table.name, (table.db or None)


def apply_scope_filter(
    statement: exp.Expression | None,
    original_sql: str,
    *,
    filter_type: str,
    column: str | None,
    values: Sequence[object],
) -> tuple[str, dict[str, object]]:
    """按意图改写:谓词 AND 进最外层 WHERE。SKIP 原样返回;产物 = (SQL, 绑定参数)。

    改写前对语句树 copy(copy-on-apply):与内置路径「每次独立 parse 再改写」
    的行为逐字节对齐,同一产物多次 apply 不叠加(防消费方误复用叠写)。
    """
    if filter_type in FILTER_SKIP:
        return original_sql, {}
    stmt = statement.copy() if statement is not None else None
    if not isinstance(stmt, exp.Select):
        return original_sql, {}
    from_ = stmt.args.get("from_")
    if from_ is None or not isinstance(from_.this, exp.Table):
        return original_sql, {}
    qualifier = from_.this.alias_or_name

    params: dict[str, object] = {}
    placeholders: list[exp.Placeholder] = []
    for i, value in enumerate(values):
        name = f"{PARAM_PREFIX}{i}"
        placeholders.append(exp.Placeholder(this=name))
        params[name] = value
    col = exp.column(column or "", table=qualifier or None)
    predicate: Expr
    if filter_type == FILTER_USER_EQ:
        predicate = exp.EQ(this=col, expression=placeholders[0])
    else:  # DEPT_IN
        predicate = exp.In(this=col, expressions=placeholders)

    existing = stmt.args.get("where")
    combined = exp.And(this=existing.this, expression=predicate) if existing else predicate
    stmt.set("where", exp.Where(this=combined))
    return stmt.sql(dialect=_NamedParamPG), params
