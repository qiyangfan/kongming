"""统一出站通道:域工具执行的薄切面。

顺序固定(策略器契约):停用/参数校验 → 出口校验(白名单,零网络请求
拒绝)→ 域限流(第二维度)→ 执行器单次出站(超时)→ 失败分类(连接类至多
重试一次/响应类不重试)→ 响应脱敏 → 结构化观察。审计仍在既有六层 L5
(sys_tool_audit,域标签复用 channel 列),本切面不落库。
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from kongming.toolgateway.models import (
    FAILURE_PROTOCOL_PREFIX,
    FAILURE_UNDELIVERED_PREFIX,
    DomainPolicy,
    DomainSpec,
    RetryClass,
)

log = logging.getLogger(__name__)

# 域执行器签名:单次出站(url/payload/timeout 由切面给定);返回已解析响应体
Executor = Callable[..., Awaitable[object]]


@dataclass(frozen=True)
class _DomainService:
    """单个域的出站部件与运行时停用态。"""

    spec: DomainSpec
    policy: DomainPolicy
    executor: Executor
    disabled: bool

    def rejection(self, args: dict[str, object]) -> str | None:
        """出口白名单与参数校验;返回拒绝原因或 None。"""
        host = urlsplit(self.spec.endpoint).hostname or ""
        if not self.policy.exit_host_allowed(host):
            return f"policy: 出口主机未命中白名单({self.spec.id})"
        return self.policy.validate_args(args)


class ToolGateway:
    """域路由表 + 出站切面。策略器协议(四职责,纯函数)由各域实现绑定;
    策略器缺失的域不注册(fail-closed);运行时停用集合立即生效(重启随
    部署清单复位)。"""

    def __init__(
        self,
        *,
        manifest: dict[str, DomainSpec],
        policies: dict[str, DomainPolicy],
        executor: Executor | None = None,
        domain_executors: dict[str, Executor] | None = None,
    ) -> None:
        self._domains = manifest
        self._policies = policies
        self._executor = executor
        # 按域专属执行器(缺省回落通道级 executor)
        self._domain_executors = dict(domain_executors) if domain_executors else {}
        self._disabled: set[str] = set()
        self.warnings: list[str] = [
            f"toolgateway: 域 {did} 无策略器实现,未注册" for did in manifest if did not in policies
        ]

    # ── 路由与治理 ──

    def handles(self, domain: str) -> bool:
        return domain in self._domains and domain in self._policies and domain not in self._disabled

    def declared(self, domain: str) -> bool:
        """域是否已装配(清单+策略器+执行器;不含运行时停用态)。

        端口绑定判「装配」不判「停用」:停用零感知由注册表闸(注入集消失)
        与执行期拒绝承担,端口绑定不随停用翻转——否则停用瞬间的在途回调会被
        误标为「未接线(部署缺陷)」审计。"""
        return (
            domain in self._domains
            and domain in self._policies
            and (domain in self._domain_executors or self._executor is not None)
        )

    def quota(self, domain: str) -> int | None:
        spec = self._domains.get(domain)
        return spec.quota_per_minute if spec else None

    def apply_disabled(self, domains: frozenset[str] | set[str]) -> None:
        """启动装载持久停用集(替换语义;运维意图以库为准)。"""
        self._disabled = set(domains)

    def disable(self, domain: str) -> None:
        self._disabled.add(domain)

    def enable(self, domain: str) -> None:
        self._disabled.discard(domain)

    def summary(self) -> list[dict[str, object]]:
        """治理面清单摘要(不含凭证与白名单明细)。"""
        return [
            {
                "id": d.id,
                "endpoint_host": (urlsplit(d.endpoint).hostname or ""),
                "timeout_ms": d.timeout_ms,
                "quota_per_minute": d.quota_per_minute,
                "redaction": d.redaction,
                # 白名单摘要(只给规模,不给明细——spec 域治理面)
                "whitelist_size": len(d.whitelist_hosts),
                "policy_bound": d.id in self._policies,
                "disabled": d.id in self._disabled,
            }
            for d in self._domains.values()
        ]

    # ── 出站切面 ──

    async def execute(
        self,
        *,
        tool_name: str,
        args: dict[str, object],
        domain: str,
        trace_id: str,
        tenant_id: str,
        user_id: str,
        channel: str = "chat",
    ) -> dict[str, object]:
        """域工具执行:返回既有分发形态的结构化观察
        (status ∈ executed/rejected/error + result/reason)。"""
        domain_svc = self._lookup(domain)
        # 未装配与已停用同形:拒绝并按策略分类落审计(由调用方 L5 承担)
        if domain_svc is None or domain_svc.disabled:
            return {"status": "rejected", "reason": f"policy: 域 {domain} 不可用"}
        rejection = domain_svc.rejection(args)
        if rejection is not None:
            return {"status": "rejected", "reason": rejection}

        return await _deliver(domain_svc, args, trace_id=trace_id)

    def _lookup(self, domain: str) -> _DomainService | None:
        """解析域的装配部件;缺清单/策略器/执行器或已停用返回 None。"""
        policy = self._policies.get(domain)
        spec = self._domains.get(domain)
        executor = self._domain_executors.get(domain) or self._executor
        if spec is None or policy is None or executor is None:
            return None
        return _DomainService(spec, policy, executor, disabled=domain in self._disabled)


async def _deliver(
    domain_svc: _DomainService, args: dict[str, object], *, trace_id: str
) -> dict[str, object]:
    """出站执行:未送达至多重试一次(切面单点承担),其余分类立即收口。"""
    for attempt in (1, 2):
        outcome = await _attempt(
            domain_svc.executor,
            domain_svc.policy,
            args,
            context=_AttemptContext(
                url=domain_svc.spec.endpoint,
                timeout_s=domain_svc.spec.timeout_ms / 1000,
                domain=domain_svc.spec.id,
                trace_id=trace_id,
                final=attempt == 2,
            ),
        )
        if outcome.result is not None:
            return outcome.result
        if outcome.exhausted:
            break
    raise RuntimeError("unreachable")  # pragma: no cover —— 循环必经 return


@dataclass(frozen=True)
class _AttemptContext:
    """单次出站的固定参数与所处轮次。"""

    url: str
    timeout_s: float
    domain: str
    trace_id: str
    final: bool  # 是否为最后一次尝试(未送达也不再重试)


@dataclass(frozen=True)
class _AttemptOutcome:
    """单次出站结果:已收口的观察,或需继续重试的标记。"""

    result: dict[str, object] | None
    exhausted: bool


async def _attempt(
    executor: Executor,
    policy: DomainPolicy,
    args: dict[str, object],
    *,
    context: _AttemptContext,
) -> _AttemptOutcome:
    """单次出站并分类;OK 与各类失败收口返回观察,未送达且可重试返回空结果。"""
    delivered = False
    exc: Exception | None = None
    response: object = None
    try:
        response = await executor(url=context.url, payload=dict(args), timeout_s=context.timeout_s)
        delivered = True
    except Exception as e:  # noqa: BLE001 —— 分类收敛,不裸抛
        exc = e
        delivered = _was_delivered(e)
    verdict = policy.classify(exc, delivered=delivered)
    if verdict == RetryClass.OK:
        return _AttemptOutcome({"status": "executed", "result": policy.redact(response)}, True)
    # UNDELIVERED(连接层未送达):重试至多一次(切面单点承担);
    # PROTOCOL(已送达)立即收口不重试——重试判定只认分类,不再进下一轮
    if verdict != RetryClass.UNDELIVERED or context.final:
        # 域可声明 failure_reason(迁移零漂移文案);缺省走安全通用文案
        fr = getattr(policy, "failure_reason", None)
        reason = fr(verdict, exc) if fr else _safe_reason(context.domain, verdict, exc)
        return _AttemptOutcome({"status": "error", "reason": reason}, True)
    log.warning(
        "toolgateway 域 %s 连接类失败,重试一次: trace=%s err=%s",
        context.domain,
        context.trace_id,
        type(exc).__name__,
    )
    return _AttemptOutcome(None, False)


def _was_delivered(exc: Exception) -> bool:
    """是否「请求已送达」:读超时/HTTP 状态错=已送达;连接族=未送达。
    判定供策略器分类;具体分类仍由域策略器声明(可覆盖)。
    域收敛异常按 FAILURE_UNDELIVERED/FAILURE_PROTOCOL 前缀语义归类,
    与策略器 classify 保持同向(delivered 是一致性兜底信号,重试判定只认
    前缀分类)。"""
    text = str(exc)
    if text.startswith(FAILURE_UNDELIVERED_PREFIX):
        return False
    if text.startswith(FAILURE_PROTOCOL_PREFIX):
        return True
    if isinstance(
        exc,
        (
            httpx.ConnectError,
            httpx.ConnectTimeout,  # 连接超时=未送达(先于 TimeoutException 判定)
            httpx.RemoteProtocolError,
            httpx.ReadError,
            httpx.WriteError,
            httpx.CloseError,
        ),
    ):
        return False
    # 读超时/已取得响应(含报文畸形)均视为已送达
    return isinstance(exc, (httpx.TimeoutException, httpx.HTTPStatusError, ValueError, TypeError))


def _safe_reason(domain: str, verdict: RetryClass, exc: Exception | None) -> str:
    """降级观察的安全文案:不暴露内部堆栈与端点地址细节(spec 明文)。"""
    label = "unreachable" if verdict == RetryClass.UNDELIVERED else "protocol"
    detail = type(exc).__name__ if exc is not None else "unknown"
    return f"{label}: 域 {domain} 暂不可用({detail})"
