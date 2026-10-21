"""add-mcp-breaker:MCP 出站运行时熔断(状态机/端口装饰器/service 联动)。

全部手动构造(fake clock + 脚本化 stub + 最小 repo 桩),不触组合根单例
(无 pytest 污染);用例与 specs/mcp-interop「出站运行时熔断」十 Scenario
一一对照(「进程重启清零」由进程内存态设计保证,见注释断言)。
"""

import asyncio
from typing import Any

import pytest

from kongming.mcp.breaker import BreakerMcpCaller, McpBreaker
from kongming.mcp.ports import McpCallError


class _FakeClock:
    """可手动推进的单调时钟(状态机测试无 sleep)。"""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, sec: float) -> None:
        self.now += sec


def _open_breaker(threshold: int = 3) -> tuple[McpBreaker, _FakeClock, str]:
    """构造已达阈值的 breaker(缺省 3 次连续 timeout)。"""
    clock = _FakeClock()
    b = McpBreaker(threshold=threshold, cooldown_sec=60.0, clock=clock)
    ep = "http://srv.example/mcp"
    for _ in range(threshold):
        assert b.allow(ep)
        b.record_failure(ep)
    return b, clock, ep


# ── 状态机(三态语义) ──


async def test_threshold_opens_and_blocks() -> None:
    """连续失败达阈值进入冷却,期内拒绝(不出站由装饰器层断言,此处状态机)。"""
    b, _clock, ep = _open_breaker()
    assert not b.allow(ep)
    # 其他 endpoint 不受影响(endpoint 维度隔离)
    assert b.allow("http://other.example/mcp")


async def test_under_threshold_allows() -> None:
    """未达阈值照常放行(健康路径零感知)。"""
    clock = _FakeClock()
    b = McpBreaker(threshold=3, cooldown_sec=60.0, clock=clock)
    ep = "http://srv.example/mcp"
    b.record_failure(ep)
    b.record_failure(ep)
    assert b.allow(ep)


async def test_success_clears_counter() -> None:
    """出站成功清空连续失败计数(零星单次失败永不触发)。"""
    clock = _FakeClock()
    b = McpBreaker(threshold=3, cooldown_sec=60.0, clock=clock)
    ep = "http://srv.example/mcp"
    b.record_failure(ep)
    b.record_failure(ep)
    b.record_success(ep)
    b.record_failure(ep)
    b.record_failure(ep)
    assert b.allow(ep)


async def test_cooldown_expiry_half_opens_single() -> None:
    """冷却期满自动放行单个探测(半开);探测进行中其余调用继续拒绝。"""
    b, clock, ep = _open_breaker()
    clock.advance(61.0)
    assert b.allow(ep)  # 半开放行探测
    assert not b.allow(ep)  # 探测中其余拒绝


async def test_half_open_success_resets() -> None:
    """半开探测成功复位回正常态(计数清空,后续正常放行)。"""
    b, clock, ep = _open_breaker()
    clock.advance(61.0)
    assert b.allow(ep)
    b.record_success(ep)
    assert b.allow(ep)
    # 复位后零星失败重新从零计数
    b.record_failure(ep)
    assert b.allow(ep)


async def test_half_open_failure_recools() -> None:
    """半开探测失败重新进入冷却期;期满可再次半开。"""
    b, clock, ep = _open_breaker()
    clock.advance(61.0)
    assert b.allow(ep)
    b.record_failure(ep)
    assert not b.allow(ep)  # 回冷却
    clock.advance(61.0)
    assert b.allow(ep)  # 再次半开


async def test_reset_reopens() -> None:
    """显式复位(发现同步成功联动)立即恢复放行。"""
    b, _clock, ep = _open_breaker()
    b.reset(ep)
    assert b.allow(ep)


async def test_fresh_instance_allows() -> None:
    """进程内存态:新实例(进程重启)状态清零,首次调用照常出站。"""
    _open_breaker()
    clock = _FakeClock()
    fresh = McpBreaker(threshold=3, cooldown_sec=60.0, clock=clock)
    assert fresh.allow("http://srv.example/mcp")


def test_record_success_quiet_on_healthy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """健康路径(从未熔断)成功不打「恢复」打点,仅真恢复打点(日志零噪音)。

    经模块级 log 替身断言——本仓 pytest 日志基建 INFO 级不可测(setLevel
    不解锁,tests/test_rag_team_scope.py 既有同款),替身是确定性形态。"""

    class _LogStub:
        def __init__(self) -> None:
            self.infos: list[str] = []

        def info(self, msg: str, *args: object) -> None:
            self.infos.append(msg)

        def warning(self, *args: object) -> None:
            return None

    stub = _LogStub()
    monkeypatch.setattr("kongming.mcp.breaker.log", stub)
    clock = _FakeClock()
    b = McpBreaker(threshold=3, cooldown_sec=60.0, clock=clock)
    ep = "http://srv.example/mcp"
    b.record_success(ep)  # 从未失败:无「恢复」可言
    assert stub.infos == []
    b.record_failure(ep)
    b.record_success(ep)  # 失败后的成功调用应记录恢复。
    assert len(stub.infos) == 1 and "熔断恢复" in stub.infos[0]


# ── 端口装饰器(拦截/记账/直通) ──


class _SeqCaller:
    """脚本化 stub:call/read_resource 按脚本逐个吐结果/抛错,出站计数。"""

    def __init__(self, script: list[Any] | None = None) -> None:
        self._script = list(script or [])
        self.outbound = 0

    async def _next(self) -> dict[str, Any]:
        self.outbound += 1
        step = self._script.pop(0)
        if isinstance(step, McpCallError):
            raise step
        return step

    async def call(
        self,
        *,
        endpoint: str,
        headers: dict[str, str],
        tool_name: str,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        return await self._next()

    async def read_resource(
        self, *, endpoint: str, headers: dict[str, str], uri: str
    ) -> dict[str, Any]:
        return await self._next()

    async def probe(self, *, endpoint: str, headers: dict[str, str]) -> Any:
        self.outbound += 1
        return "PROBE"

    async def get_prompt(
        self,
        *,
        endpoint: str,
        headers: dict[str, str],
        name: str,
        arguments: dict[str, str],
    ) -> dict[str, Any]:
        self.outbound += 1
        return {"text": "P"}


_EP = "http://srv.example/mcp"
_OK: dict[str, Any] = {"content": "ok", "truncated": False, "original_size": 2}


async def test_fail_fast_after_threshold_no_outbound() -> None:
    """达阈值后第 N+1 次调用快速拒绝(unavailable),不产生出站。"""
    inner = _SeqCaller([McpCallError("timeout", "t")] * 3)
    w = BreakerMcpCaller(inner, McpBreaker(threshold=3, cooldown_sec=60.0))
    for _ in range(3):
        with pytest.raises(McpCallError) as ei:
            await w.call(endpoint=_EP, headers={}, tool_name="t", args={})
        assert ei.value.kind == "timeout"
    assert inner.outbound == 3
    with pytest.raises(McpCallError) as ei:
        await w.call(endpoint=_EP, headers={}, tool_name="t", args={})
    assert ei.value.kind == "unavailable"
    assert "未出站" in ei.value.detail
    assert inner.outbound == 3  # 第 4 次未出站


async def test_read_resource_counts_and_blocks() -> None:
    """read_resource 同口径记账与拦截。"""
    inner = _SeqCaller([McpCallError("conn", "refused")] * 3)
    w = BreakerMcpCaller(inner, McpBreaker(threshold=3, cooldown_sec=60.0))
    for _ in range(3):
        with pytest.raises(McpCallError):
            await w.read_resource(endpoint=_EP, headers={}, uri="file:///a")
    with pytest.raises(McpCallError) as ei:
        await w.read_resource(endpoint=_EP, headers={}, uri="file:///a")
    assert ei.value.kind == "unavailable"
    assert inner.outbound == 3


async def test_only_instant_kinds_count() -> None:
    """确定性结果(remote/protocol/unsupported)不计入,连续多次照常出站。"""
    inner = _SeqCaller([McpCallError("remote", "bad args")] * 4)
    w = BreakerMcpCaller(inner, McpBreaker(threshold=3, cooldown_sec=60.0))
    for _ in range(4):
        with pytest.raises(McpCallError) as ei:
            await w.call(endpoint=_EP, headers={}, tool_name="t", args={})
        assert ei.value.kind == "remote"
    assert inner.outbound == 4


async def test_success_via_wrapper_resets() -> None:
    """包装层成功出站清空计数(2 失败+成功+2 失败不熔断)。"""
    inner = _SeqCaller(
        [
            McpCallError("timeout", "t"),
            McpCallError("timeout", "t"),
            dict(_OK),
            McpCallError("timeout", "t"),
            McpCallError("timeout", "t"),
        ]
    )
    w = BreakerMcpCaller(inner, McpBreaker(threshold=3, cooldown_sec=60.0))
    for expected in ("timeout", "timeout", None, "timeout", "timeout"):
        if expected is None:
            assert (await w.call(endpoint=_EP, headers={}, tool_name="t", args={}))["content"]
        else:
            with pytest.raises(McpCallError):
                await w.call(endpoint=_EP, headers={}, tool_name="t", args={})
    assert inner.outbound == 5  # 从未熔断


async def test_half_open_failure_keeps_leaf_kind() -> None:
    """半开探测失败的回灌保持叶子错误类别(timeout),不吞成 unavailable。"""
    inner = _SeqCaller([McpCallError("timeout", "t")] * 3 + [McpCallError("timeout", "probe-fail")])
    clock = _FakeClock()
    w = BreakerMcpCaller(inner, McpBreaker(threshold=3, cooldown_sec=60.0, clock=clock))
    for _ in range(3):
        with pytest.raises(McpCallError):
            await w.call(endpoint=_EP, headers={}, tool_name="t", args={})
    clock.advance(61.0)
    with pytest.raises(McpCallError) as ei:
        await w.call(endpoint=_EP, headers={}, tool_name="t", args={})
    assert ei.value.kind == "timeout"
    assert inner.outbound == 4
    # 回冷却期内继续快速拒绝
    with pytest.raises(McpCallError) as ei:
        await w.call(endpoint=_EP, headers={}, tool_name="t", args={})
    assert ei.value.kind == "unavailable"


async def test_probe_and_get_prompt_passthrough_when_open() -> None:
    """管理面探测(probe/get_prompt)不受熔断约束:OPEN 态照常出站。"""
    inner = _SeqCaller([McpCallError("timeout", "t")] * 3)
    w = BreakerMcpCaller(inner, McpBreaker(threshold=3, cooldown_sec=60.0))
    for _ in range(3):
        with pytest.raises(McpCallError):
            await w.call(endpoint=_EP, headers={}, tool_name="t", args={})
    assert await w.probe(endpoint=_EP, headers={}) == "PROBE"
    assert (await w.get_prompt(endpoint=_EP, headers={}, name="n", arguments={}))["text"]
    assert inner.outbound == 5


async def test_none_breaker_passthrough() -> None:
    """breaker=None(总开关关闭)全直通:失败任意次照常出站。"""
    inner = _SeqCaller([McpCallError("timeout", "t")] * 5)
    w = BreakerMcpCaller(inner, None)
    for _ in range(5):
        with pytest.raises(McpCallError) as ei:
            await w.call(endpoint=_EP, headers={}, tool_name="t", args={})
        assert ei.value.kind == "timeout"
    assert inner.outbound == 5


class _CancelCaller:
    """取消语义 stub:运行时方法恒抛 CancelledError(客户端断连形态)。"""

    def __init__(self) -> None:
        self.outbound = 0

    async def call(self, **_kw: Any) -> dict[str, Any]:
        self.outbound += 1
        raise asyncio.CancelledError

    async def read_resource(self, **_kw: Any) -> dict[str, Any]:
        self.outbound += 1
        raise asyncio.CancelledError

    async def probe(self, **_kw: Any) -> Any:  # pragma: no cover
        raise AssertionError("取消用例不应触达管理面")

    async def get_prompt(self, **_kw: Any) -> dict[str, Any]:  # pragma: no cover
        raise AssertionError("取消用例不应触达管理面")


async def test_cancelled_probe_does_not_wedge_breaker() -> None:
    """半开探测被取消(客户端断连)不挂死探测位:回滚后下次 allow 重新放行
    探测,CancelledError 照常传播不吞。"""
    inner = _SeqCaller([McpCallError("timeout", "t")] * 3)
    clock = _FakeClock()
    breaker = McpBreaker(threshold=3, cooldown_sec=60.0, clock=clock)
    w = BreakerMcpCaller(inner, breaker)
    for _ in range(3):
        with pytest.raises(McpCallError):
            await w.call(endpoint=_EP, headers={}, tool_name="t", args={})
    clock.advance(61.0)
    cancel_inner = _CancelCaller()
    with pytest.raises(asyncio.CancelledError):
        await BreakerMcpCaller(cancel_inner, breaker).call(
            endpoint=_EP, headers={}, tool_name="t", args={}
        )
    assert cancel_inner.outbound == 1
    # 探测位已回滚(冷却期满态保持):下次 allow 重新放行探测,不永久拒绝
    assert breaker.allow(_EP)


# ── service 联动(发现同步成功显式复位) ──


class _SyncRepo:
    """sync 路径最小桩(get/get_call_target/save_snapshot)。"""

    def __init__(self) -> None:
        self.saved: dict[str, Any] = {}

    async def get(self, *, server_id: str, tenant_id: str) -> dict[str, Any]:
        return {
            "server_id": server_id,
            "tenant_id": tenant_id,
            "server_key": "srv",
            "endpoint": _EP,
            "tools": [],
            "resources": [],
            "prompts": [],
        }

    async def get_call_target(
        self, *, tenant_id: str, server_key: str, require_enabled: bool = True
    ) -> tuple[str, dict[str, str]]:
        return (_EP, {})

    async def save_snapshot(self, **kw: Any) -> None:
        self.saved = kw


class _ProbeOnlyCaller:
    """sync 联动桩:probe 返回空探测结果(零工具快照),其余不达。"""

    async def probe(self, *, endpoint: str, headers: dict[str, str]) -> Any:
        from kongming.mcp.models import ProbeResult

        return ProbeResult(tools=[], failures={}, unsupported=frozenset())

    async def call(
        self,
        *,
        endpoint: str,
        headers: dict[str, str],
        tool_name: str,
        args: dict[str, Any],
    ) -> dict[str, Any]:  # pragma: no cover
        raise AssertionError("sync 联动不应触达运行时出站")

    async def read_resource(
        self, *, endpoint: str, headers: dict[str, str], uri: str
    ) -> dict[str, Any]:  # pragma: no cover
        raise AssertionError("sync 联动不应触达运行时出站")

    async def get_prompt(
        self,
        *,
        endpoint: str,
        headers: dict[str, str],
        name: str,
        arguments: dict[str, str],
    ) -> dict[str, Any]:  # pragma: no cover
        raise AssertionError("sync 联动不应触达运行时出站")


async def test_sync_success_resets_breaker() -> None:
    """发现同步成功显式复位熔断:运行时调用随即恢复出站(不等半开)。"""
    from kongming.mcp.service import McpService

    breaker, _clock, ep = _open_breaker()
    svc = McpService(_SyncRepo(), _ProbeOnlyCaller(), breaker=breaker)  # type: ignore[arg-type]
    out = await svc.sync(server_id="s1", tenant_id="t1")
    assert out["ok"] is True
    assert breaker.allow(ep)
