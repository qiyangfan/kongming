"""使用 sqlglot AST 将数据范围谓词加入最外层 WHERE，并以主表别名限定过滤列。

无法定位主表的查询由调用方拒绝；过滤值始终作为命名参数绑定，不拼入 SQL 文本。"""

from collections.abc import Sequence
from typing import cast

import sqlglot
from sqlglot import Expr, exp
from sqlglot.dialects.postgres import Postgres
from sqlglot.errors import ParseError

from kongming.datascope.models import FilterType, ScopeFilter

_PARAM_PREFIX = "km_ds_"


class _NamedParamPG(Postgres):
    """postgres 方言变体:占位符渲 :name(SQLAlchemy text 参数风格)。"""

    # sqlglot 官方扩展点;存根把 Generator 声明为变量而非类型,关闭两码
    class Generator(Postgres.Generator):  # type: ignore[misc, valid-type]
        def placeholder_sql(self, expression: exp.Placeholder) -> str:
            return f":{expression.name}"


def extract_main_table_statement(statement: Expr | None) -> tuple[str, str | None] | None:
    """语句级主表提取(吃共享解析产物,单遍复用;语义同 extract_main_table_ref)。"""
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
    """语句级改写(吃共享解析产物,单遍复用;语义同 apply_filter)。"""
    if filter_type in (FilterType.SKIP.value, FilterType.UNSUPPORTED.value):
        return sql, {}
    if not isinstance(statement, exp.Select):
        return sql, {}
    parsed = statement
    from_ = parsed.args.get("from_")
    if from_ is None or not isinstance(from_.this, exp.Table):
        return sql, {}
    qualifier = from_.this.alias_or_name

    params, placeholders = _bind_values(values)
    predicate = _build_predicate(filter_type, column or "", qualifier, placeholders)

    existing = parsed.args.get("where")
    new_predicate = predicate
    combined = exp.And(this=existing.this, expression=new_predicate) if existing else new_predicate
    parsed.set("where", exp.Where(this=combined))
    return parsed.sql(dialect=_NamedParamPG), params


def _bind_values(values: Sequence[object]) -> tuple[dict[str, object], list[exp.Placeholder]]:
    """过滤值 → 命名占位符序列与绑定参数字典(名为 _PARAM_PREFIX + 序号)。"""
    params: dict[str, object] = {}
    placeholders: list[exp.Placeholder] = []
    for i, value in enumerate(values):
        name = f"{_PARAM_PREFIX}{i}"
        placeholders.append(exp.Placeholder(this=name))
        params[name] = value
    return params, placeholders


def _build_predicate(
    filter_type: str, column: str, qualifier: str, placeholders: list[exp.Placeholder]
) -> Expr:
    """按过滤意图构造谓词:USER_EQ 取首占位符等值,DEPT_IN 全部占位符 IN。"""
    col = exp.column(column, table=qualifier or None)
    if filter_type == FilterType.USER_EQ.value:
        return exp.EQ(this=col, expression=placeholders[0])
    return exp.In(this=col, expressions=placeholders)


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
