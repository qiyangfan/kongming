"""SQL 安全引擎接入层(053):模式解析、单遍解析产物、影子对拍、请求级降级。

模式(默认影子;env KM_SQLGUARD_MODE):
- builtin:纯内置路径(sqlglot 现行实现),引擎不参与;
- shadow:内置权威(行为与切换前逐字一致),引擎并行判定,不一致仅记录
  (结构完整观测 + 计数),绝不影响 verdict;
- primary:引擎权威;引擎加载失败/运行期异常时请求级降级内置,记
  ENGINE_FALLBACK 审计事件与计数,服务不中断、请求不失败。

单遍纪律:一次 parse_guarded 产出的 Guarded 对同一 SQL 只解析一次
(内置路径共享语句列表;引擎路径共享 GuardArtifact),三闸判定与
行级改写结构全部复用。
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import sqlglot
from sqlglot import Expr
from sqlglot.errors import SqlglotError

if TYPE_CHECKING:
    from kongming.sqlfw.firewall import Verdict

log = logging.getLogger(__name__)

MODE_BUILTIN = "builtin"
MODE_SHADOW = "shadow"
MODE_PRIMARY = "primary"
_MODES = (MODE_BUILTIN, MODE_SHADOW, MODE_PRIMARY)

DEFAULT_MODE = MODE_SHADOW
_OBSERVATION_CAP = 100

_lock = threading.Lock()
_mode: str = DEFAULT_MODE
_engine: object | None = None  # 注入的引擎模块(测试);None = 惰性加载真实引擎
_engine_loaded = False
_stats = {"shadow_total": 0, "mismatch_total": 0, "fallback_total": 0}
_observations: deque[dict[str, object]] = deque(maxlen=_OBSERVATION_CAP)


def configure(mode: str, engine: object | None = None) -> None:
    """设置管线模式(进程级;启动自检或测试调用)。非法值安全回落 builtin。"""
    global _mode, _engine, _engine_loaded
    if mode not in _MODES:
        log.warning("SQLGUARD 非法模式 %r,回落 builtin", mode)
        mode = MODE_BUILTIN
    with _lock:
        _mode = mode
        _engine = engine
        _engine_loaded = engine is not None


# 测试别名(与语义等价;configure 为公开 API)
set_mode = configure


def effective_mode() -> str:
    return _mode


def _reserved_lists_match(engine_rules: object) -> bool:
    """引擎清单成员集合与权威(liannu.rules)一致?

    067:切主前必过。mismatch 归零不构成清单一致的证据(评估集不保证触达
    skill_/user_prefs),必须显式比成员。窄于权威 → 视为引擎不可用。
    """
    from liannu import rules as authority

    def _members(obj: object, name: str) -> frozenset[str] | tuple[str, ...] | None:
        return getattr(obj, name, None)

    try:
        for attr in (
            "RESERVED_TABLE_PREFIXES",
            "RESERVED_TABLE_NAMES",
            "RESERVED_SCHEMA_NAMES",
            "READONLY_ROOTS",
            "FORBIDDEN_FUNCS",
        ):
            a = _members(authority, attr)
            b = _members(engine_rules, attr)
            if b is None:
                return False
            if isinstance(a, frozenset):
                if frozenset(b) != a:
                    return False
            elif tuple(b) != tuple(a or ()):
                return False
    except Exception:
        return False
    return True


def snapshot_stats() -> dict[str, int]:
    with _lock:
        return dict(_stats)


def observations() -> list[dict[str, object]]:
    """影子不一致观测(结构完整:sql/check/builtin/engine),最新在后。"""
    with _lock:
        return list(_observations)


def _load_engine() -> Any:  # noqa: ANN401
    """惰性加载 liannu;缺失只告警一次,不阻断(消费方降级内置)。"""
    global _engine_loaded
    if _engine is not None:
        return _engine
    if _engine_loaded:
        return None
    _engine_loaded = True
    try:
        import liannu

        log.info("SQLGUARD 引擎已加载 version=%s", liannu.__version__)
        return liannu
    except ImportError as e:
        log.warning("SQLGUARD 引擎不可用,管线以内置路径运行: %s", e)
        return None


def startup_self_check() -> bool:
    """启动自检(任务 3.1):版本 + 冒烟解析 + 保留清单成员校验(067);
    失败告警降级 builtin,不阻断启动。"""
    if _mode == MODE_BUILTIN:
        # builtin 模式引擎不参与:不触发引擎加载(缺失也不该有告警噪音)
        log.info("SQLGUARD 管线以内置路径运行 mode=builtin")
        return True
    engine = _load_engine()
    if engine is None:
        log.warning("SQLGUARD 引擎缺失,启动自检降级 builtin(不阻断)")
        _downgrade("engine missing")
        return False
    ok, detail = cast("tuple[bool, str]", engine.self_check())
    if not ok:
        log.warning("SQLGUARD 引擎自检失败(%s),降级 builtin(不阻断)", detail)
        _downgrade(f"self-check failed: {detail}")
        return False
    # 067:清单窄于权威 → 引擎不可用(mismatch 归零不是清单一致的证据)
    if not _reserved_lists_match(engine):
        log.warning("SQLGUARD 引擎保留清单窄于权威,降级 builtin(067)")
        _downgrade("reserved lists narrower than authority")
        return False
    # 灰度态显性可见(默认 WARNING 级根日志可读;builtin 稳态无此行)
    log.warning(
        "SQLGUARD 管线 mode=%s,活跃引擎=liannu version=%s,启动自检通过",
        _mode,
        detail,
    )
    return True


def _downgrade(reason: str) -> None:
    global _mode
    with _lock:
        _mode = MODE_BUILTIN
        _stats["fallback_total"] += 1
    log.warning("ENGINE_FALLBACK reason=%s scope=mode_downgrade", reason)


def _record_fallback(sql: str, check_name: str, exc: Exception) -> None:
    with _lock:
        _stats["fallback_total"] += 1
    log.warning(
        "ENGINE_FALLBACK check=%s category=%s:%s sql=%r",
        check_name,
        type(exc).__name__,
        exc,
        sql[:200],
    )


def _record_shadow(sql: str, check_name: str, builtin: object, engine: object) -> None:
    with _lock:
        _stats["shadow_total"] += 1
        same = builtin == engine
        if not same:
            _stats["mismatch_total"] += 1
    if not same:
        obs = {
            "sql": sql,
            "check": check_name,
            "builtin": builtin,
            "engine": engine,
        }
        with _lock:
            _observations.append(obs)
        log.warning(
            "SQLGUARD 影子不一致 check=%s builtin=%r engine=%r sql=%r",
            check_name,
            builtin,
            engine,
            sql[:200],
        )


def _verdict_tuple(v: Verdict) -> dict[str, object]:
    return {"passed": v.passed, "reason": v.reason}


def parse_guarded(sql: str) -> Guarded:
    return Guarded(sql)


@dataclass(frozen=True)
class _BuiltinParse:
    statements: list[Expr | None]
    parse_error: str | None


class Guarded:
    """单遍解析产物 + 模式化判定门(三闸 + 行级改写结构)。"""

    def __init__(self, sql: str) -> None:
        self.sql = sql
        self._builtin_cache: _BuiltinParse | None = None
        self._engine_artifact: Any = None
        self._engine_failed = False

    # ── 单遍解析(惰性,各自最多一次)────────────────────────────

    def _builtin(self) -> _BuiltinParse:
        if self._builtin_cache is None:
            try:
                sts: list[Expr | None] = list(sqlglot.parse(self.sql, dialect="postgres"))
                self._builtin_cache = _BuiltinParse(sts, None)
            except SqlglotError as e:
                # ParseError/TokenError 同族语法类拒绝;RecursionError 忠实透传
                # (内置路径行为契约与切换前逐字一致,含深嵌套栈溢出形态)
                self._builtin_cache = _BuiltinParse([], str(e))
        return self._builtin_cache

    def _engine_ok(self) -> Any:
        """可用引擎产物;引擎异常时请求级降级并记 ENGINE_FALLBACK。"""
        if self._engine_failed:
            return None
        if self._engine_artifact is None:
            engine = _load_engine()
            if engine is None:
                return None
            try:
                self._engine_artifact = engine.parse(self.sql)
            except Exception as e:  # 引擎任何异常都不放行到管线外
                self._engine_failed = True
                _record_fallback(self.sql, "parse", e)
                return None
        return self._engine_artifact

    # ── 判定门(模式化)────────────────────────────────────────

    def check(self) -> Verdict:
        return self._resolve("check", self._builtin_check, lambda a: a.check_firewall())

    def check_schema_scope(self, allowed_schemas: set[str]) -> Verdict:
        return self._resolve(
            "check_schema_scope",
            lambda: self._builtin_scope(allowed_schemas),
            lambda a: a.check_schema_scope(allowed_schemas),
        )

    def check_reserved_tables(self) -> Verdict:
        return self._resolve(
            "check_reserved_tables", self._builtin_reserved, lambda a: a.check_reserved_tables()
        )

    def _resolve(
        self,
        check_name: str,
        builtin_fn: Callable[[], Verdict],
        engine_fn: Callable[[Any], Verdict],
    ) -> Verdict:
        if _mode == MODE_BUILTIN:
            return builtin_fn()
        engine = self._engine_ok()
        if engine is None:
            return builtin_fn()
        try:
            engine_verdict: Verdict = engine_fn(engine)
        except Exception as e:
            self._engine_failed = True
            _record_fallback(self.sql, check_name, e)
            return builtin_fn()
        if _mode == MODE_PRIMARY:
            return engine_verdict
        # shadow:内置权威,引擎只观测
        builtin_verdict: Verdict = builtin_fn()
        _record_shadow(
            self.sql, check_name, _verdict_tuple(builtin_verdict), _verdict_tuple(engine_verdict)
        )
        return builtin_verdict

    # ── 内置路径判定(共享单次解析;规则体在 firewall 一比一保留)──────

    def _builtin_check(self) -> Verdict:
        from kongming.sqlfw import firewall
        from kongming.sqlfw.firewall import Verdict

        bp = self._builtin()
        if bp.parse_error is not None:
            return Verdict(False, f"PARSE_ERROR: {bp.parse_error}")
        return firewall.check_statements(bp.statements)

    def _builtin_scope(self, allowed: set[str]) -> Verdict:
        from kongming.sqlfw import firewall
        from kongming.sqlfw.firewall import Verdict

        bp = self._builtin()
        if bp.parse_error is not None:
            return Verdict(False, f"PARSE_ERROR: {bp.parse_error}")
        return firewall.check_schema_scope_statements(bp.statements, allowed)

    def _builtin_reserved(self) -> Verdict:
        from kongming.sqlfw import firewall
        from kongming.sqlfw.firewall import Verdict

        bp = self._builtin()
        if bp.parse_error is not None:
            return Verdict(False, f"PARSE_ERROR: {bp.parse_error}")
        return firewall.check_reserved_tables_statements(bp.statements)

    # ── 行级改写结构(datascope 复用同一产物)─────────────────────

    def main_table_ref(self) -> tuple[str, str | None] | None:
        from kongming.datascope import rewrite

        if _mode == MODE_PRIMARY:
            art = self._engine_ok()
            if art is not None:
                try:
                    ref = art.main_table_ref()
                    return None if ref is None else (str(ref[0]), cast("str | None", ref[1]))
                except Exception as e:
                    self._engine_failed = True
                    _record_fallback(self.sql, "main_table_ref", e)
        bp = self._builtin()
        if bp.parse_error is not None:
            return None
        return rewrite.extract_main_table_statement(bp.statements[0] if bp.statements else None)

    def apply_filter(
        self, filter_type: str, column: str | None, values: Sequence[object]
    ) -> tuple[str, dict[str, object]]:
        from kongming.datascope import rewrite

        if _mode == MODE_PRIMARY:
            art = self._engine_ok()
            if art is not None:
                try:
                    sql_out, params_out = art.apply_scope_filter(
                        filter_type=filter_type, column=column, values=values
                    )
                    return str(sql_out), cast("dict[str, object]", params_out)
                except Exception as e:
                    self._engine_failed = True
                    _record_fallback(self.sql, "apply_filter", e)
        bp = self._builtin()
        if bp.parse_error is not None:
            raise ValueError(f"PARSE_ERROR: {bp.parse_error}")
        return rewrite.apply_filter_statement(
            bp.statements[0] if bp.statements else None,
            self.sql,
            filter_type=filter_type,
            column=column,
            values=values,
        )
