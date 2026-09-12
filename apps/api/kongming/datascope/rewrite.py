"""data_scope AST 改写:sqlglot 最外层 WHERE 注入(spec 立约:AST 非字符串拼接)。

设计(D1,004 实测修正):
- 过滤谓词 AND 进最外层查询的 WHERE,列引用限定主表别名(JOIN 防歧义)——
  过滤列属于 FROM 主表必然可见,与原查询投影解耦;
  (首版「包外层子查询」被集成测试证伪:原查询不投影过滤列时外层不可见)
- UNION / FROM 为子查询等取不到主表的形态,由 applier 上游安全降级,不到此;
- 谓词值一律命名绑定参数(:km_ds_N),SQL 文本不含任何过滤值(防注入);
- 命名参数经方言子类保留 :name 风格(postgres 方言默认渲 pyformat)。
"""

from collections.abc import Sequence
from typing import cast

import sqlglot
from sqlglot import Expr, exp
from sqlglot.dialects.postgres import Postgres
from sqlglot.errors import ParseError

from kongming.datascope.models import FilterType, ScopeFilter

_PARAM_PREFIX = "km_ds_"


class _NamedParamPG(Postgres):
    """postgres 方言变体:占位符渲 :name(SQLAlchemy text() 参数风格)。"""

    # sqlglot 官方扩展点;存根把 Generator 声明为变量而非类型,关闭两码
    class Generator(Postgres.Generator):  # type: ignore[misc, valid-type]
        def placeholder_sql(self, expression: exp.Placeholder) -> str:
            return f":{expression.name}"


def extract_main_table_statement(statement: Expr | None) -> tuple[str, str | None] | None:
    """语句级主表提取(吃共享解析产物,053 单遍复用;语义同 extract_main_table_ref)。"""
    if not isinstance(statement, exp.Select):
        return None
    from_ = statement.args.get("from_")
    if from_ is None or not isinstance(from_.this, exp.Table):
        return None
    table = from_.this
    return table.name, (table.db or None)


def extract_main_table_ref(sql: str) -> tuple[str, str | None] | None:
    """最外层 Select 的 FROM 为裸表时返回 (表名, 显式 schema 或 None);

    UNION/子查询 FROM 等取不到返 None。显式 schema(public.customer)供调用方
    收窄列探测范围,防跨 schema 同名表误判。
    """
    try:
        parsed = sqlglot.parse_one(sql, dialect="postgres")
    except ParseError:
        return None
    return extract_main_table_statement(cast("Expr | None", parsed))


def extract_main_table(sql: str) -> str | None:
    """最外层裸表表名(不含 schema);取不到返 None。"""
    ref = extract_main_table_ref(sql)
    return None if ref is None else ref[0]


def apply_filter_statement(
    statement: Expr | None,
    sql: str,
    *,
    filter_type: str,
    column: str | None,
    values: Sequence[object],
) -> tuple[str, dict[str, object]]:
    """语句级改写(吃共享解析产物,053 单遍复用;语义同 apply_filter)。"""
    if filter_type in (FilterType.SKIP.value, FilterType.UNSUPPORTED.value):
        return sql, {}
    if not isinstance(statement, exp.Select):
        return sql, {}
    parsed = statement
    from_ = parsed.args.get("from_")
    if from_ is None or not isinstance(from_.this, exp.Table):
        return sql, {}
    qualifier = from_.this.alias_or_name

    params: dict[str, object] = {}
    placeholders: list[exp.Placeholder] = []
    for i, value in enumerate(values):
        name = f"{_PARAM_PREFIX}{i}"
        placeholders.append(exp.Placeholder(this=name))
        params[name] = value
    col = exp.column(column or "", table=qualifier or None)
    predicate: Expr
    if filter_type == FilterType.USER_EQ.value:
        predicate = exp.EQ(this=col, expression=placeholders[0])
    else:  # DEPT_IN
        predicate = exp.In(this=col, expressions=placeholders)

    existing = parsed.args.get("where")
    combined = exp.And(this=existing.this, expression=predicate) if existing else predicate
    parsed.set("where", exp.Where(this=combined))
    return parsed.sql(dialect=_NamedParamPG), params


def apply_filter(sql: str, flt: ScopeFilter) -> tuple[str, dict[str, object]]:
    """按意图改写:谓词 AND 进最外层 WHERE。SKIP 原样返回;产物 = (SQL, 绑定参数)。"""
    if flt.type in (FilterType.SKIP, FilterType.UNSUPPORTED):
        return sql, {}
    parsed = sqlglot.parse_one(sql, dialect="postgres")
    return apply_filter_statement(
        cast("Expr | None", parsed),
        sql,
        filter_type=flt.type.value,
        column=flt.column,
        values=flt.values,
    )
