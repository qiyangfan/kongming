"""MCP 出站运行时熔断(add-mcp-breaker)。

三态状态机(CLOSED 正常 / OPEN 冷却 / HALF_OPEN 半开探测)+ McpCaller
端口装饰器:运行时出站(tools/call 与 resources/read)对连续瞬时失败
(timeout/conn——server 不可达信号)自动冷却,达阈值后冷却期内快速失败
(不出站,`unavailable` 分类归一);冷却期满半开放行单个探测请求,成功
复位、失败回冷却。管理面探测(probe/get_prompt)结构上不受限——装饰器
不拦这两个方法,与 spec「管理面不受限」是类型面互锁而非约定;确定性
结果(protocol/remote/unsupported)不计入——熔断是瞬时故障止损器,
不掩盖确定性错误的真实报错。

状态为进程内存态(不落库,重启即清零,与出站「每次即开即用连接」的
无状态形态自洽);计数维度=endpoint(与 server 配置行一一对应,McpCaller
端口签名只有 endpoint,按此维度端口契约零变更);记账方法全同步(无
await),单事件循环内检查-置位天然原子,不引入锁。
"""

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from kongming.config import get_settings
from kongming.mcp.ports import McpCaller, McpCallError

# 计入熔断的失败分类(瞬时不可达信号);protocol/remote/unsupported 属
# 确定性行为,计入会把单工具问题放大成整 server 熔断且掩盖真实报错
_BREAKER_KINDS = frozenset({"timeout", "conn"})

log = logging.getLogger(__name__)

_T = TypeVar("_T")


class _Entry:
    """单 endpoint 熔断账目(failures 连续失败数;blocked_until 冷却截止;
    probing 半开探测进行中)。"""

    __slots__ = ("blocked_until", "failures", "probing")

    def __init__(self) -> None:
        self.failures = 0
        self.blocked_until = 0.0
        self.probing = False


class McpBreaker:
    """连续失败自动冷却的熔断器(纯领域逻辑,时钟注入可测)。

    allow/record_success/record_failure/reset 均为同步方法;allow 返回
    False 时调用方 MUST 快速失败且不出站(冷却拒),返回 True 且此前处于
    冷却期满态时本次即半开探测请求。"""

    def __init__(
        self,
        *,
        threshold: int = 3,
        cooldown_sec: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._threshold = threshold
        self._cooldown = float(cooldown_sec)
        self._clock = clock
        self._entries: dict[str, _Entry] = {}

    def _entry(self, endpoint: str) -> _Entry:
        entry = self._entries.get(endpoint)
        if entry is None:
            entry = _Entry()
            self._entries[endpoint] = entry
        return entry

    def allow(self, endpoint: str) -> bool:
        """出站前闸:False=冷却期内或半开探测进行中(快速失败不出站);
        True=放行(冷却期满的首次放行即半开探测,伴随状态转移)。"""
        entry = self._entries.get(endpoint)
        if entry is None:
            return True
        if entry.probing:
            return False  # 半开探测进行中,其余请求继续拒绝
        if entry.blocked_until > self._clock():
            return False  # 冷却期内
        if entry.blocked_until > 0.0:
            # 冷却期满:本次放行即半开探测(探测结果经 record_* 收口)
            entry.probing = True
        return True

    def record_success(self, endpoint: str) -> None:
        """出站成功:计数清零回正常态(任何一次成功,含半开探测)。"""
        entry = self._entries.pop(endpoint, None)
        if entry is not None:
            # 仅真恢复打点:健康路径(从未打开,entry 不存在)无"恢复"可言,
            # 每次成功都打 info 是纯噪音
            log.info("mcp 熔断恢复(出站成功): %s", endpoint)

    def record_failure(self, endpoint: str) -> None:
        """出站失败(仅瞬时类,分类过滤在装饰器):连续计数 +1 达阈值进
        冷却;半开探测失败回冷却重计。"""
        entry = self._entry(endpoint)
        if entry.probing:
            entry.probing = False
        entry.failures += 1
        if entry.failures >= self._threshold:
            entry.blocked_until = self._clock() + self._cooldown
            log.warning(
                "mcp 熔断打开(连续失败 %d 次,冷却 %.0fs): %s",
                entry.failures,
                self._cooldown,
                endpoint,
            )

    def reset(self, endpoint: str) -> None:
        """显式复位(发现同步成功联动):管理员确认可达后立即可用。"""
        self._entries.pop(endpoint, None)

    def cancel_probe(self, endpoint: str) -> None:
        """半开探测请求未完成(取消/实现裸抛等非 McpCallError 收口径):
        回滚探测位、保持冷却期满态(下次 allow 重新放行探测),防探测位
        挂死导致该 server 被永久拒绝。幂等(entry 已收口时无操作)。"""
        entry = self._entries.get(endpoint)
        if entry is not None and entry.probing:
            entry.probing = False


class BreakerMcpCaller:
    """McpCaller 端口熔断装饰器:call/read_resource(运行时)拦截+记账,
    probe/get_prompt(管理面)直通不拦。breaker=None 全直通(总开关关闭
    =逐字旧行为)。半开探测失败的回灌保持叶子错误类别,不吞成 unavailable。"""

    def __init__(self, inner: McpCaller, breaker: McpBreaker | None) -> None:
        self._inner = inner
        self._breaker = breaker

    async def _guarded(self, endpoint: str, invoke: Callable[[], Awaitable[_T]]) -> _T:
        breaker = self._breaker
        if breaker is None:
            return await invoke()
        if not breaker.allow(endpoint):
            raise McpCallError(
                "unavailable",
                f"server 熔断冷却中(连续失败自动冷却),未出站: {endpoint}",
            )
        settled = False
        try:
            out = await invoke()
        except McpCallError as e:
            settled = True  # McpCallError 经 record_failure 收口(含探测位回滚)
            if e.kind in _BREAKER_KINDS:
                breaker.record_failure(endpoint)
            raise
        else:
            breaker.record_success(endpoint)
            settled = True
            return out
        finally:
            if not settled:
                # 非 McpCallError 收口径(客户端断连取消/实现裸抛):回滚探测
                # 位防挂死——allow 放行的半开探测若不收口,该 server 会被
                # 永久拒绝直到重启;其余路径(成功/已记账失败)下幂等无操作
                breaker.cancel_probe(endpoint)

    async def call(
        self,
        *,
        endpoint: str,
        headers: dict[str, str],
        tool_name: str,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        return await self._guarded(
            endpoint,
            lambda: self._inner.call(
                endpoint=endpoint, headers=headers, tool_name=tool_name, args=args
            ),
        )

    async def read_resource(
        self, *, endpoint: str, headers: dict[str, str], uri: str
    ) -> dict[str, Any]:
        return await self._guarded(
            endpoint,
            lambda: self._inner.read_resource(endpoint=endpoint, headers=headers, uri=uri),
        )

    async def probe(self, *, endpoint: str, headers: dict[str, str]) -> Any:
        return await self._inner.probe(endpoint=endpoint, headers=headers)

    async def get_prompt(
        self,
        *,
        endpoint: str,
        headers: dict[str, str],
        name: str,
        arguments: dict[str, str],
    ) -> dict[str, Any]:
        return await self._inner.get_prompt(
            endpoint=endpoint, headers=headers, name=name, arguments=arguments
        )


# ── 组合根装配(单点工厂 + 进程级单例) ──

_UNSET = object()
_breaker_cache: Any = _UNSET


def get_mcp_breaker() -> McpBreaker | None:
    """进程级单例(熔断状态跨请求存活;形态=惰性哨兵缓存)。

    总开关关闭返回 None(BreakerMcpCaller 直通=逐字旧行为);测试不走
    本入口(手动构造 McpBreaker 注入),无 pytest 污染。"""
    global _breaker_cache
    if _breaker_cache is _UNSET:
        settings = get_settings()
        _breaker_cache = (
            None
            if not settings.mcp_breaker_enabled
            else McpBreaker(
                threshold=settings.mcp_breaker_threshold,
                cooldown_sec=float(settings.mcp_breaker_cooldown_sec),
            )
        )
    return _breaker_cache  # type: ignore[no-any-return]


def build_mcp_caller(settings: Any) -> McpCaller:
    """出站 caller 单点装配(四个组合根共用):HttpMcpCaller 按部署配置
    构造,熔断总开关启用时包 BreakerMcpCaller(运行时拦截记账)。"""
    from kongming.mcp.client import HttpMcpCaller

    inner: McpCaller = HttpMcpCaller(timeout_sec=settings.mcp_outbound_timeout_sec)
    breaker = get_mcp_breaker()
    return inner if breaker is None else BreakerMcpCaller(inner, breaker)
