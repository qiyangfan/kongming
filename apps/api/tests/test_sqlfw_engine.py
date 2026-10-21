"""SQL 安全引擎接入测试:影子对拍/降级 fallback/深嵌套受控/单遍复用。

验证影子模式判定、降级、深嵌套和单遍解析行为。
引擎以 duck-typing 假件注入(仅需 .passed/.reason 与
liannu.Verdict 同构)。
"""

import logging
import uuid
from collections.abc import Generator
from typing import cast

import pytest
import sqlglot

from kongming.sqlfw import firewall, guard

GOOD = "SELECT * FROM customer"
BAD = "DELETE FROM customer WHERE id = 1"
DEEP = "SELECT *" + " FROM (" * 40 + "t" + ")" * 40  # 括号深度 40 > 引擎默认上限 32


class _FakeVerdict:
    def __init__(self, passed: bool, reason: str | None = None) -> None:
        self.passed = passed
        self.reason = reason


class _FakeArtifact:
    """可编排判定的假引擎产物(duck-typing:仅引擎 Verdict 形态)。"""

    def __init__(
        self, sql: str, *, verdicts: dict[str, _FakeVerdict] | None = None, boom: bool = False
    ) -> None:
        self.sql = sql
        self._verdicts = verdicts or {}
        self._boom = boom

    def check_firewall(self) -> _FakeVerdict:
        if self._boom:
            raise RuntimeError("engine boom")
        return self._verdicts.get("check", _FakeVerdict(True))

    def check_schema_scope(self, allowed: set[str]) -> _FakeVerdict:
        if self._boom:
            raise RuntimeError("engine boom")
        return self._verdicts.get("scope", _FakeVerdict(True))

    def check_reserved_tables(self) -> _FakeVerdict:
        if self._boom:
            raise RuntimeError("engine boom")
        return self._verdicts.get("reserved", _FakeVerdict(True))

    def main_table_ref(self) -> tuple[str, str | None] | None:
        if self._boom:
            raise RuntimeError("engine boom")
        return ("customer", None)

    def apply_scope_filter(
        self, *, filter_type: str, column: str | None, values: list[object]
    ) -> tuple[str, dict[str, object]]:
        if self._boom:
            raise RuntimeError("engine boom")
        params = {f"km_ds_{i}": v for i, v in enumerate(values)}
        return (f"-- engine {filter_type} {column}", params)


class _FakeEngine:
    """假引擎模块:按 SQL 前缀返回可编排产物。"""

    __version__ = "0.0.0-fake"

    def __init__(
        self, *, verdicts: dict[str, _FakeVerdict] | None = None, boom: bool = False
    ) -> None:
        self._verdicts = verdicts
        self._boom = boom
        self.parsed: list[str] = []

    def parse(self, sql: str, *, dialect: str = "postgres", max_depth: int = 32) -> _FakeArtifact:
        self.parsed.append(sql)
        return _FakeArtifact(sql, verdicts=self._verdicts, boom=self._boom)

    @staticmethod
    def self_check() -> tuple[bool, str]:
        return True, "0.0.0-fake"


@pytest.fixture(autouse=True)
def _reset_guard() -> Generator[None]:  # pyright: ignore[reportUnusedFunction]
    # 会话级 alembic upgrade 的 fileConfig(disable_existing_loggers)会把
    # 收集期已创建的模块 logger 置 disabled;本模块断言审计日志,先复启。
    logging.getLogger("kongming.sqlfw.guard").disabled = False
    guard.configure("builtin")
    yield  # type: ignore[misc]
    guard.configure("builtin")


# ============ 影子模式:内置权威,引擎只观测 ============


def test_shadow_builtin_authoritative_on_mismatch() -> None:
    """引擎误判 BAD 为 PASS:影子模式不得改变判定,不一致须完整记录。"""
    guard.configure("shadow", engine=_FakeEngine(verdicts={"check": _FakeVerdict(True)}))
    v = firewall.check(BAD)
    assert not v.passed, "影子模式引擎误判不得生效"
    assert v.reason is not None and v.reason.startswith("BLOCKED_ROOT")

    obs = [o for o in guard.observations() if o["check"] == "check"]
    assert obs, "不一致必须留痕"
    latest = obs[-1]
    builtin = cast("dict[str, object]", latest["builtin"])
    engine_v = cast("dict[str, object]", latest["engine"])
    # 记录结构完整:SQL + 检查名 + 双方判定
    assert latest["sql"] == BAD
    assert builtin["passed"] is False
    assert str(builtin["reason"]).startswith("BLOCKED_ROOT")
    assert engine_v["passed"] is True
    stats = guard.snapshot_stats()
    assert stats["shadow_total"] >= 1
    assert stats["mismatch_total"] >= 1


def test_shadow_engine_block_does_not_affect_pass() -> None:
    """引擎误拦 GOOD:影子模式判定不受影响。"""
    guard.configure("shadow", engine=_FakeEngine(verdicts={"check": _FakeVerdict(False, "X")}))
    assert firewall.check(GOOD).passed


def test_shadow_zero_mismatch_when_agree() -> None:
    """引擎未注入(builtin 路径即真实实现)时影子无不一致;计数口径不漂。"""
    guard.configure("shadow", engine=None)  # 引擎不可用:影子退化为纯内置,不告警不计数
    assert firewall.check(GOOD).passed
    assert guard.snapshot_stats()["fallback_total"] == 0


# ============ 切主模式:引擎权威 + 请求级降级============


def test_primary_engine_verdict_authoritative() -> None:
    guard.configure("primary", engine=_FakeEngine(verdicts={"check": _FakeVerdict(False, "ENG")}))
    v = firewall.check(GOOD)
    assert not v.passed and v.reason == "ENG"
    assert guard.snapshot_stats()["fallback_total"] == 0


def test_primary_engine_exception_request_level_fallback(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """引擎运行期异常:请求级回落内置,判定语义不变,ENGINE_FALLBACK 留痕计数。"""
    guard.configure("primary", engine=_FakeEngine(boom=True))
    with caplog.at_level(logging.WARNING, logger="kongming.sqlfw.guard"):
        v = firewall.check(GOOD)
    assert v.passed, "降级后判定应与内置路径一致(不失败)"
    stats = guard.snapshot_stats()
    assert stats["fallback_total"] >= 1
    assert any("ENGINE_FALLBACK" in r.getMessage() for r in caplog.records)


def test_primary_engine_parse_boom_falls_back(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """引擎解析期抛异常:同样请求级降级,BAD 由内置兜底拦截而非异常逃逸成 500。"""

    class _ParseBoom(_FakeEngine):
        def parse(
            self, sql: str, *, dialect: str = "postgres", max_depth: int = 32
        ) -> _FakeArtifact:
            raise RuntimeError("parse boom")

    guard.configure("primary", engine=_ParseBoom())
    with caplog.at_level(logging.WARNING, logger="kongming.sqlfw.guard"):
        v = firewall.check(BAD)
    assert not v.passed
    assert v.reason is not None and v.reason.startswith("BLOCKED_ROOT")
    assert guard.snapshot_stats()["fallback_total"] >= 1
    assert any("ENGINE_FALLBACK" in r.getMessage() for r in caplog.records)


# ============ 深嵌套受控(引擎模式)============


def test_deep_nested_controlled_error_in_primary() -> None:
    """超限深嵌套:受控 DEPTH_EXCEEDED 拒绝,而非 RecursionError 栈溢出。"""
    guard.configure("primary", engine=None)  # 真实引擎(liannu 已安装时)
    v = firewall.check(DEEP)
    assert not v.passed
    assert v.reason is not None and v.reason.startswith("DEPTH_EXCEEDED")


# ============ 单遍解析复用============


def test_single_parse_shared_in_builtin(monkeypatch: pytest.MonkeyPatch) -> None:
    """内置路径:同一 SQL 的三闸 + 改写结构共享一次解析(解析计数探针)。"""
    count = {"n": 0}
    real_parse = sqlglot.parse

    def counting_parse(sql: str, *a: object, **kw: object) -> object:
        count["n"] += 1
        return real_parse(sql, *a, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(sqlglot, "parse", counting_parse)
    guard.configure("builtin")
    g = guard.parse_guarded("SELECT * FROM customer")
    g.check()
    g.check_reserved_tables()
    g.check_schema_scope({"public"})
    g.main_table_ref()
    g.apply_filter("USER_EQ", "create_by", ["u-1"])
    assert count["n"] == 1, f"五次消费应共享一次解析,实际 parse {count['n']} 次"


def test_engine_mode_matches_builtin_rewrite_byte_equal() -> None:
    """主模式改写产物与内置路径逐字节一致。"""
    from kongming.datascope import rewrite
    from kongming.datascope.models import FilterType, ScopeFilter

    flt = ScopeFilter(type=FilterType.USER_EQ, column="create_by", values=["u-1"])
    sql = "SELECT c.name FROM customer c WHERE c.id > 1"
    builtin_sql, builtin_params = rewrite.apply_filter(sql, flt)

    guard.configure("primary", engine=None)
    g = guard.parse_guarded(sql)
    engine_sql, engine_params = g.apply_filter(flt.type.value, flt.column, flt.values)
    assert engine_sql == builtin_sql
    assert engine_params == builtin_params


# ============ 模式解析与启动自检============


def test_invalid_mode_falls_back_to_builtin(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="kongming.sqlfw.guard"):
        guard.configure("bogus")
    assert guard.effective_mode() == "builtin"
    assert any("SQLGUARD" in r.getMessage() for r in caplog.records)


def test_self_check_failure_downgrades(caplog: pytest.LogCaptureFixture) -> None:
    """引擎自检失败:告警降级内置,不阻断启动。"""

    class _SickEngine(_FakeEngine):
        @staticmethod
        def self_check() -> tuple[bool, str]:  # type: ignore[misc]
            return False, "冒烟解析异常"

    guard.configure("primary", engine=_SickEngine())
    with caplog.at_level(logging.WARNING, logger="kongming.sqlfw.guard"):
        ok = guard.startup_self_check()
    assert not ok
    assert guard.effective_mode() == "builtin"
    assert any("SQLGUARD" in r.getMessage() for r in caplog.records)


def test_guarded_identity_isolated() -> None:
    """不同 SQL 的 Guarded 互不串产物;params 键稳定。"""
    guard.configure("builtin")
    g = guard.parse_guarded(GOOD)
    sql, params = g.apply_filter("USER_EQ", "create_by", [str(uuid.uuid4())])
    assert "create_by = :km_ds_0" in sql
    assert set(params) == {"km_ds_0"}
