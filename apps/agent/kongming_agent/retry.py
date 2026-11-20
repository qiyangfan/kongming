"""Agent 出站错误的重试与退避:LLM 出站按瞬时故障判定重试。

控制面回调只在连接阶段未送达时重试,避免重复执行已到达的请求。
"""

import asyncio
import logging
import secrets
from collections.abc import Awaitable, Callable

log = logging.getLogger(__name__)

# 瞬时 HTTP 状态:408 请求超时 / 429 限流 / 5xx 服务端(其余 4xx=请求语义错误,重发无解)
_TRANSIENT_STATUS = frozenset({408, 429, 500, 502, 503, 504})

# 控制面回调重试预算:本地控制面(同机/compose 内网)连接层抖动的快重试,
# 与 LLM 出站的 llm_max_attempts 分离——回调便宜且本地,不需要长退避
CALLBACK_MAX_ATTEMPTS = 3
CALLBACK_RETRY_BASE_MS = 200
CALLBACK_RETRY_MAX_WAIT_MS = 1000


def _status_code(exc: BaseException) -> int | None:
    """SDK/httpx 异常的 HTTP 状态码(鸭子类型:status_code 优先,兼容 status)。"""
    for attr in ("status_code", "status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    return None


def _retry_after_seconds(exc: BaseException) -> float | None:
    """Retry-After(秒)读取:SDK 异常的 .response.headers 或裸属性;缺失/畸形=None。"""
    raw: object = None
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is not None:
        getter = getattr(headers, "get", None)
        if callable(getter):
            value = getter("retry-after")
            if value is not None:
                raw = value
    if raw is None:
        raw = getattr(exc, "retry_after", None)
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        try:
            return float(raw.strip())
        except ValueError:
            return None
    return None


def _root_cause(exc: BaseException) -> BaseException:
    """解包语义包装(llm.py 与 llm_protocol 的 LlmError 均 `from e` 保留 cause):
    配置/协议类 LlmError(未配置、客户端形态异常)无 cause,root 即自身 → 非瞬时。"""
    current = exc
    for _ in range(3):  # 防自引用链的上界
        cause = current.__cause__
        if cause is None:
            break
        current = cause
    return current


def _type_names(exc: BaseException) -> set[str]:
    return {klass.__name__ for klass in type(exc).__mro__}


def is_transient_error(exc: BaseException) -> bool:
    """判断 LLM 出站错误是否可重试:HTTP 408/429/5xx、传输层错误、
    连接超时为瞬时;配置缺失、协议错误、无效响应不可重试。"""
    import httpx

    root = _root_cause(exc)
    status = _status_code(root)
    if status is not None:
        return status in _TRANSIENT_STATUS
    if isinstance(root, (httpx.TransportError, ConnectionError, TimeoutError)):
        return True
    names = _type_names(root)
    return bool("APIConnectionError" in names or "APITimeoutError" in names)


def is_connect_phase_error(exc: BaseException) -> bool:
    """控制面回调判据(纯函数):仅连接层未送达失败可重试。

    httpx ConnectError(拒连/重置于建连期)、ConnectTimeout(建连超时);
    SDK APIConnectionError 但须排除 APITimeoutError(其读超时=已送达)。
    读超时/ReadError/HTTP 状态错误一律 False——已送达即不可假定幂等。
    """
    import httpx

    root = _root_cause(exc)
    if isinstance(root, (httpx.ConnectError, httpx.ConnectTimeout)):
        return True
    names = _type_names(root)
    return bool("APIConnectionError" in names and "APITimeoutError" not in names)


def retry_wait_ms(
    attempt: int,
    exc: BaseException | None,
    *,
    base_delay_ms: int,
    max_wait_ms: int,
) -> float:
    """退避时长(毫秒):Retry-After 优先且截断到 max_wait(防供应商一句
    `Retry-After: 60` 把请求挂到上层 agent_timeout_sec 之后);否则指数退避
    + 全量 jitter(同刻多请求不同频重试)。attempt 从 1 起。"""
    if exc is not None:
        retry_after = _retry_after_seconds(exc)
        if retry_after is not None:
            return min(max(retry_after, 0.0), max_wait_ms / 1000) * 1000
    ceiling = min(base_delay_ms * (2 ** (max(attempt, 1) - 1)), max_wait_ms)
    # 抖动无需密码学强度;random 系函数会被 S311 判为不安全随机,故用
    # secrets.randbelow 取 [0, ceiling) 均匀值
    return secrets.randbelow(max(ceiling, 1)) if ceiling >= 1 else 0.0


async def call_with_retry[T](
    attempt: Callable[[], Awaitable[T]],
    *,
    max_attempts: int,
    base_delay_ms: int,
    max_wait_ms: int,
    is_transient: Callable[[BaseException], bool] = is_transient_error,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    on_retry: Callable[[int, float, BaseException], None] | None = None,
) -> T:
    """尝试循环:瞬时故障按退避重发,非瞬时或预算耗尽上抛原异常。

    入参 attempt 为单次调用厂函数(每次调用=一次真实出站);is_transient 可注入
    (LLM 层用 is_transient_error,回调层用 is_connect_phase_error);
    sleep/on_retry 供测试注入假时钟与断言,不真等。
    """
    if max_attempts < 1:
        raise ValueError("max_attempts 须 ≥ 1")
    for i in range(1, max_attempts + 1):
        try:
            return await attempt()
        except Exception as exc:
            if i >= max_attempts or not is_transient(exc):
                raise
            wait_ms = retry_wait_ms(i, exc, base_delay_ms=base_delay_ms, max_wait_ms=max_wait_ms)
            if on_retry is not None:
                on_retry(i, wait_ms, exc)
            if wait_ms > 0:
                await sleep(wait_ms / 1000)
    raise AssertionError("unreachable: 循环必已 return 或 raise")  # pragma: no cover
