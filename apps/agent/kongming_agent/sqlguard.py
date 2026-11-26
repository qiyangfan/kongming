"""Agent SQL 预检适配层:支持 builtin/shadow/primary 三种模式。

primary 异常时回退内置校验并记录事件;预检不访问外部服务。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from typing import Any, cast

import sqlglot
from liannu.rules import READONLY_ROOTS
from sqlglot import exp
from sqlglot.errors import SqlglotError

from kongming_agent.graph.state import ValidationResult

log = logging.getLogger(__name__)

MODE_BUILTIN = "builtin"
MODE_SHADOW = "shadow"
MODE_PRIMARY = "primary"
_MODES = (MODE_BUILTIN, MODE_SHADOW, MODE_PRIMARY)
DEFAULT_MODE = MODE_SHADOW

# 只读根清单唯一定义在 liannu.rules(上方 import);公开 re-export 供恒等断言
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
    """启动自检:失败告警降级 builtin,不阻断启动。"""
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
    """validate 节点预检:模式化解析 + 本地判定。"""
    if _mode == MODE_BUILTIN:
        return _builtin(sql)
    engine = _load_engine()
    if engine is None:
        return _builtin(sql)
    try:
        artifact = engine.parse(sql)
        engine_result = _from_artifact(artifact)
    except Exception as e:  # noqa: BLE001
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
        # 深嵌套受控拒绝:语法形态类,非权限问题 → 可重试
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


_AGGREGATE_KINDS: tuple[tuple[type[exp.AggFunc], str], ...] = (
    (exp.Count, "count"),
    (exp.Sum, "sum"),
    (exp.Avg, "avg"),
    (exp.Max, "max"),
    (exp.Min, "min"),
)


def scalar_aggregate_kind(sql: str) -> str | None:
    """单列标量查询的顶层聚合类型;供确定性作答措辞使用,不读列名。

    只认投影里唯一的 COUNT/SUM/AVG/MAX/MIN;含算术或 CASE 的表达式返回 None,
    由调用方用中性措辞。解析失败同样返回 None,不阻断作答。
    """
    if not sql.strip():
        return None
    try:
        tree = sqlglot.parse_one(sql, dialect="postgres")
    except SqlglotError:
        return None
    if not isinstance(tree, exp.Select) or len(tree.expressions) != 1:
        return None
    projection = tree.expressions[0]
    # FILTER (WHERE x > 0) 的谓词属于聚合参数,不是投影外算术;只拒绝
    # 聚合函数之外的二元表达式/ CASE。
    if projection.find(exp.Case) is not None or any(
        binary.find_ancestor(exp.AggFunc) is None and binary.find_ancestor(exp.Filter) is None
        for binary in projection.find_all(exp.Binary)
    ):
        return None
    kinds: set[str] = set()
    for node in [projection, *projection.find_all(exp.AggFunc)]:
        for agg_cls, kind in _AGGREGATE_KINDS:
            if isinstance(node, agg_cls):
                kinds.add(kind)
                break
    return next(iter(kinds)) if len(kinds) == 1 else None
