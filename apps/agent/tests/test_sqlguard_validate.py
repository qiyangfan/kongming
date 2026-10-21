"""验证 agent validate 引擎的纯解析行为。

引擎贡献单遍解析,validate 判定逻辑(MULTI_STATEMENT/只读根)本地派生;
影子:内置权威,引擎只对拍;切主:引擎权威,异常请求级降级;深嵌套受控。
"""

import logging
from collections.abc import Generator

import pytest
import sqlglot
from sqlglot import Expr

from kongming_agent import sqlguard
from kongming_agent.graph.state import ValidationResult

GOOD = "SELECT * FROM customer"
BAD_ROOT = "DELETE FROM customer"
DEEP = "SELECT *" + " FROM (" * 40 + "t" + ")" * 40


@pytest.fixture(autouse=True)
def _reset() -> Generator[None]:  # pyright: ignore[reportUnusedFunction]
    logging.getLogger("kongming_agent.sqlguard").disabled = False
    sqlguard.configure("builtin")
    yield  # type: ignore[misc]
    sqlguard.configure("builtin")


class _FakeArtifact:
    """假引擎产物:解析门三态 + 语句列表(与 liannu.GuardArtifact 同构)。"""

    def __init__(
        self,
        *,
        statements: list[Expr | None] | None = None,
        parse_error: str | None = None,
        depth_exceeded: bool = False,
        depth_reason: str | None = None,
    ) -> None:
        self.statements = statements
        self.parse_error = parse_error
        self.depth_exceeded = depth_exceeded
        self.depth_reason = depth_reason


class _FakeEngine:
    """假引擎模块:按给定 SQL 出产物(parse 期 boom 可编排)。"""

    def __init__(self, *, parse_sql: str | None = None, boom: bool = False) -> None:
        self._parse_sql = parse_sql
        self._boom = boom

    def parse(self, sql: str, *, dialect: str = "postgres", max_depth: int = 32) -> _FakeArtifact:
        if self._boom:
            raise RuntimeError("engine boom")
        target = self._parse_sql if self._parse_sql is not None else sql
        return _FakeArtifact(statements=list(sqlglot.parse(target, dialect="postgres")))

    @staticmethod
    def self_check() -> tuple[bool, str]:
        return True, "0.0.0-fake"


def test_shadow_builtin_authoritative_on_mismatch() -> None:
    """引擎误判(把 GOOD 解析成 DELETE):影子模式判定不受影响,不一致计数。"""
    sqlguard.configure("shadow", engine=_FakeEngine(parse_sql=BAD_ROOT))
    r = sqlguard.validate(GOOD)
    assert r.passed
    assert sqlguard.snapshot_stats()["mismatch_total"] == 1
    assert sqlguard.snapshot_stats()["shadow_total"] == 1


def test_primary_engine_parse_authoritative() -> None:
    """切主:引擎解析产物权威(本例引擎把 GOOD 判成写根 → 拦截)。"""
    sqlguard.configure("primary", engine=_FakeEngine(parse_sql=BAD_ROOT))
    r = sqlguard.validate(GOOD)
    assert not r.passed
    assert r.reason == "BLOCKED_ROOT: Delete"
    assert r.permission_block
    assert sqlguard.snapshot_stats()["fallback_total"] == 0


def test_primary_engine_boom_falls_back(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """引擎运行期异常:请求级降级内置,ENGINE_FALLBACK 留痕,判定不失败。"""
    sqlguard.configure("primary", engine=_FakeEngine(boom=True))
    with caplog.at_level(logging.WARNING, logger="kongming_agent.sqlguard"):
        r = sqlguard.validate(GOOD)
    assert isinstance(r, ValidationResult) and r.passed
    assert sqlguard.snapshot_stats()["fallback_total"] >= 1
    assert any("ENGINE_FALLBACK" in rec.getMessage() for rec in caplog.records)


def test_deep_nested_controlled_in_primary() -> None:
    """深嵌套受控:DEPTH_EXCEEDED 拒绝,不再 RecursionError;非权限类(可重试)。"""
    sqlguard.configure("primary", engine=None)  # 真实引擎
    r = sqlguard.validate(DEEP)
    assert not r.passed
    assert r.reason.startswith("DEPTH_EXCEEDED")
    assert not r.permission_block


def test_invalid_mode_falls_to_builtin() -> None:
    sqlguard.configure("bogus")
    assert sqlguard.effective_mode() == "builtin"
    assert sqlguard.validate(BAD_ROOT).permission_block


def test_self_check_failure_downgrades(caplog: pytest.LogCaptureFixture) -> None:
    class _Sick(_FakeEngine):
        @staticmethod
        def self_check() -> tuple[bool, str]:  # type: ignore[misc]
            return False, "boom"

    with caplog.at_level(logging.WARNING, logger="kongming_agent.sqlguard"):
        ok = sqlguard.startup_self_check("primary", engine=_Sick())
    assert not ok
    assert sqlguard.effective_mode() == "builtin"
    assert any("SQLGUARD" in rec.getMessage() for rec in caplog.records)
