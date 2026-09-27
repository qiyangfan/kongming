"""agent 面 validate 预检的引擎适配(053):单遍解析 + 模式化 + 请求级降级。

引擎(liannu,进程内纯计算,零凭证零 IO——ADR-002 不受影响)贡献
单遍解析;validate 判定逻辑(EMPTY_SQL/MULTI_STATEMENT/只读根)在本模块
本地派生,语义与切换前逐字一致。纯解析无 IO 的节点契约不变。

模式(env KM_AGENT_SQLGUARD_MODE,默认影子):
- builtin:纯内置路径;shadow:内置权威,引擎解析只对拍(不一致仅记录);
- primary:引擎解析权威,引擎异常时请求级降级内置,记 ENGINE_FALLBACK。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from typing import Any, cast

import sqlglot
from liannu.rules import READONLY_ROOTS
from sqlglot.errors import SqlglotError

from kongming_agent.graph.state import ValidationResult

log = logging.getLogger(__name__)

MODE_BUILTIN = "builtin"
MODE_SHADOW = "shadow"
MODE_PRIMARY = "primary"
_MODES = (MODE_BUILTIN, MODE_SHADOW, MODE_PRIMARY)
DEFAULT_MODE = MODE_SHADOW

# 067:只读根清单唯一定义在 liannu.rules(上方 import);公开 re-export 供恒等断言
_READONLY_ROOTS = READONLY_ROOTS

_lock = threading.Lock()
_mode = DEFAULT_MODE
_engine: object | None = None
_engine_loaded = False
_stats = {"shadow_total": 0, "mismatch_total": 0, "fallback_total": 0}


def configure(mode: str, engine: object | None = None) -> None:
    """设置预检模式(进程级;非法值安全回落 builtin)。"""
    global _mode, _engine, _engine_loaded
    if mode not in _MODES:
        log.warning("SQLGUARD 非法模式 %r,回落 builtin", mode)
        mode = MODE_BUILTIN
    with _lock:
        _mode = mode
        _engine = engine
        _engine_loaded = engine is not None


def effective_mode() -> str:
    return _mode


def snapshot_stats() -> dict[str, int]:
    with _lock:
        return dict(_stats)


def _load_engine() -> Any:
    global _engine_loaded
    if _engine is not None:
        return _engine
    if _engine_loaded:
        return None
    _engine_loaded = True
    try:
        import liannu

        log.info("SQLGUARD 引擎已加载 version=%s", liannu.__version__)
        return cast("object", liannu)
    except ImportError as e:
        log.warning("SQLGUARD 引擎不可用,validate 以内置路径运行: %s", e)
        return None


def startup_self_check(mode: str = DEFAULT_MODE, engine: object | None = None) -> bool:
    """启动自检(任务 3.1):失败告警降级 builtin,不阻断启动。"""
    configure(mode, engine)
    if _mode == MODE_BUILTIN:
        log.info("SQLGUARD validate 以内置路径运行 mode=builtin")
        return True
    eng = _load_engine()
    if eng is None:
        log.warning("SQLGUARD 引擎缺失,启动自检降级 builtin(不阻断)")
        configure(MODE_BUILTIN)
        return False
    ok, detail = cast("tuple[bool, str]", eng.self_check())
    if not ok:
        log.warning("SQLGUARD 引擎自检失败(%s),降级 builtin(不阻断)", detail)
        configure(MODE_BUILTIN)
        _fallback_count()
        return False
    # 灰度态显性可见(默认 WARNING 级根日志可读;builtin 稳态无此行)
    log.warning(
        "SQLGUARD 管线 mode=%s,活跃引擎=liannu version=%s,启动自检通过",
        _mode,
        detail,
    )
    return True


def _fallback_count() -> None:
    with _lock:
        _stats["fallback_total"] += 1


def validate(sql: str) -> ValidationResult:
    """validate 节点预检:模式化解析 + 本地判定(语义与切换前一致)。"""
    if _mode == MODE_BUILTIN:
        return _builtin(sql)
    engine = _load_engine()
    if engine is None:
        return _builtin(sql)
    try:
        artifact = engine.parse(sql)
        engine_result = _from_artifact(artifact)
    except Exception as e:
        with _lock:
            _stats["fallback_total"] += 1
        log.warning(
            "ENGINE_FALLBACK check=validate category=%s:%s sql=%r",
            type(e).__name__,
            e,
            sql[:200],
        )
        return _builtin(sql)
    if _mode == MODE_PRIMARY:
        return engine_result
    # shadow:内置权威,引擎解析只对拍
    builtin_result = _builtin(sql)
    engine_tuple = (engine_result.passed, engine_result.permission_block, engine_result.reason)
    builtin_tuple = (builtin_result.passed, builtin_result.permission_block, builtin_result.reason)
    with _lock:
        _stats["shadow_total"] += 1
        same = engine_tuple == builtin_tuple
        if not same:
            _stats["mismatch_total"] += 1
    if not same:
        log.warning(
            "SQLGUARD 影子不一致 check=validate builtin=%r engine=%r sql=%r",
            builtin_tuple,
            engine_tuple,
            sql[:200],
        )
    return builtin_result


def _from_artifact(artifact: Any) -> ValidationResult:
    if getattr(artifact, "depth_exceeded", False):
        reason = getattr(artifact, "depth_reason", None) or "DEPTH_EXCEEDED"
        # 深嵌套受控拒绝(053):语法形态类,非权限问题 → 可重试
        return ValidationResult(passed=False, reason=reason)
    parse_error = getattr(artifact, "parse_error", None)
    if parse_error is not None:
        return ValidationResult(passed=False, reason=f"PARSE_ERROR: {parse_error}")
    statements = cast("list[object]", getattr(artifact, "statements", None) or [])
    return _from_statements([st for st in statements if st is not None])


def _builtin(sql: str) -> ValidationResult:
    try:
        statements = [st for st in sqlglot.parse(sql, dialect="postgres") if st is not None]
    except SqlglotError as e:
        # ParseError 与 TokenError(未闭合引号等)同族,均为可重试的语法类失败
        return ValidationResult(passed=False, reason=f"PARSE_ERROR: {e}")
    return _from_statements(statements)


def _from_statements(statements: Sequence[object]) -> ValidationResult:
    if not statements:
        return ValidationResult(passed=False, reason="EMPTY_SQL")
    if len(statements) > 1:
        # LLM 输出形态问题,值得 selfheal 重试
        return ValidationResult(passed=False, reason="MULTI_STATEMENT")
    first = statements[0]
    if not isinstance(first, _READONLY_ROOTS):
        # 写操作/DDL:权限类,prompt 已明令禁止,重试只会再犯——直接终态
        return ValidationResult(
            passed=False,
            permission_block=True,
            reason=f"BLOCKED_ROOT: {type(first).__name__}",
        )
    return ValidationResult(passed=True)
