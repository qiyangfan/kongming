"""单遍解析产物:一次 parse 承载全部判定与改写操作。

parse 门序:深度预扫描(超限不进 sqlglot)→ sqlglot.parse 一次
(SqlglotError→PARSE_ERROR;RecursionError→受控 DEPTH_EXCEEDED)。
判定/改写方法全部复用本次产物,零重复解析。
"""

from typing import cast

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from liannu import depth as depth_mod
from liannu import rewrite as rewrite_mod
from liannu import rules
from liannu.verdict import Verdict

# sqlglot 存根的 parse 返回类型与 exp.Expression 命名不一致,统一在此收窄
Statements = list[exp.Expression | None]

_DEPTH_PRESCAN_REASON = "DEPTH_EXCEEDED: paren nesting depth {n} > max_depth {m}"
_DEPTH_RECURSION_REASON = "DEPTH_EXCEEDED: parse recursion limit exceeded (sqlglot deep nesting)"


class GuardError(Exception):
    """改写操作遇上未通过解析门的产物(内置路径对应 ParseError 上抛形态)。"""


class GuardArtifact:
    """一次解析的全部产物;判定方法只读,不改写语句树(apply 除外,终态操作)。"""

    def __init__(
        self,
        sql: str,
        dialect: str,
        *,
        statements: Statements | None,
        parse_error: str | None,
        depth_exceeded: bool,
        depth_reason: str | None,
        max_depth: int,
    ) -> None:
        self.sql = sql
        self.dialect = dialect
        self.statements = statements
        self.parse_error = parse_error
        self.depth_exceeded = depth_exceeded
        self.depth_reason = depth_reason
        self.max_depth = max_depth

    @classmethod
    def parse(
        cls, sql: str, *, dialect: str = "postgres", max_depth: int = depth_mod.DEFAULT_MAX_DEPTH
    ) -> "GuardArtifact":
        """单遍解析入口:深度预扫描 → sqlglot.parse 一次。"""
        paren_depth = depth_mod.max_paren_depth(sql)
        if paren_depth > max_depth:
            return cls(
                sql,
                dialect,
                statements=None,
                parse_error=None,
                depth_exceeded=True,
                depth_reason=_DEPTH_PRESCAN_REASON.format(n=paren_depth, m=max_depth),
                max_depth=max_depth,
            )
        try:
            statements: Statements = cast(
                "list[exp.Expression | None]", list(sqlglot.parse(sql, dialect=dialect))
            )
        except RecursionError:
            return cls(
                sql,
                dialect,
                statements=None,
                parse_error=None,
                depth_exceeded=True,
                depth_reason=_DEPTH_RECURSION_REASON,
                max_depth=max_depth,
            )
        except SqlglotError as e:
            # ParseError 与 TokenError(未闭合引号/非法 token)同族,均为语法类拒绝
            return cls(
                sql,
                dialect,
                statements=None,
                parse_error=str(e),
                depth_exceeded=False,
                depth_reason=None,
                max_depth=max_depth,
            )
        return cls(
            sql,
            dialect,
            statements=statements,
            parse_error=None,
            depth_exceeded=False,
            depth_reason=None,
            max_depth=max_depth,
        )

    # ── 判定(复用同一产物)───────────────────────────────────

    def _gate(self) -> Verdict | None:
        """解析门前置:超限/语法错误直接出 verdict,不进规则层。"""
        if self.depth_exceeded:
            return Verdict(False, self.depth_reason)
        if self.parse_error is not None:
            return Verdict(False, f"PARSE_ERROR: {self.parse_error}")
        return None

    def check_firewall(self) -> Verdict:
        """防火墙判定:只读根白名单 + SELECT INTO + 写/DDL 节点 + 危险函数。"""
        if (gate := self._gate()) is not None:
            return gate
        return rules.check_firewall(cast("Statements", self.statements))

    def check_schema_scope(self, allowed_schemas: set[str]) -> Verdict:
        """schema 白名单:显式限定引用仅允许落在 allowed_schemas 内。"""
        if (gate := self._gate()) is not None:
            return gate
        return rules.check_schema_scope(cast("Statements", self.statements), allowed_schemas)

    def check_reserved_tables(self) -> Verdict:
        """保留表拒读:保留前缀/名 + CTE 遮蔽防护 + 保留 schema。"""
        if (gate := self._gate()) is not None:
            return gate
        return rules.check_reserved_tables(cast("Statements", self.statements))

    def first_statement(self) -> exp.Expression | None:
        """首语句(单遍复用;空语句形态为 None,与内置 parse_one 语义对齐)。"""
        if self.statements:
            return self.statements[0]
        return None

    # ── 行级改写结构(复用同一产物)─────────────────────────────

    def main_table_ref(self) -> tuple[str, str | None] | None:
        """最外层裸表引用;解析门未过/取不到均返 None(内置 extract 语义)。"""
        if self.depth_exceeded or self.parse_error is not None:
            return None
        return rewrite_mod.main_table_ref(self.first_statement())

    def apply_scope_filter(
        self, *, filter_type: str, column: str | None, values: list[object]
    ) -> tuple[str, dict[str, object]]:
        """谓词 AND 进最外层 WHERE;解析门未过时抛 GuardError(对应内置 ParseError 上抛)。"""
        if self.depth_exceeded:
            raise GuardError(self.depth_reason or "DEPTH_EXCEEDED")
        if self.parse_error is not None:
            raise GuardError(f"PARSE_ERROR: {self.parse_error}")
        return rewrite_mod.apply_scope_filter(
            self.first_statement(),
            self.sql,
            filter_type=filter_type,
            column=column,
            values=values,
        )
