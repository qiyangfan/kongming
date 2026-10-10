"""统一出站通道(089):域工具执行的薄切面。

顺序固定(D1 策略器契约):停用/参数校验 → 出口校验(白名单,零网络请求
拒绝)→ 域限流(第二维度)→ 执行器单次出站(超时)→ 失败分类(连接类至多
重试一次/响应类不重试)→ 响应脱敏 → 结构化观察。审计仍在既有六层 L5
(sys_tool_audit,域标签复用 channel 列),本切面不落库。

迁移判据:knowledge_search 经此切面的对外行为与 081 逐字段一致(观察形态/
重试语义/失败分类文案);未命中域的工具走既有路径,零回归。
"""

import logging
from collections.abc import Awaitable, Callable
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


class KnowledgeSourceError(Exception):
    """域执行失败的结构化收敛(081 同名异常迁移;分发兜底 ERROR + 审计)。"""


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
        # 094:按域专属执行器(缺省回落通道级 executor;既有域不声明=零回归)
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
        误标为「未接线(部署缺陷)」审计(审查 P2-1/P2-2)。"""
        return (
            domain in self._domains
            and domain in self._policies
            and (domain in self._domain_executors or self._executor is not None)
        )

    def quota(self, domain: str) -> int | None:
        spec = self._domains.get(domain)
        return spec.quota_per_minute if spec else None

    def apply_disabled(self, domains: frozenset[str] | set[str]) -> None:
        """0044:启动装载持久停用集(替换语义;运维意图以库为准)。"""
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
        policy = self._policies.get(domain)
        spec = self._domains.get(domain)
        executor = self._domain_executors.get(domain) or self._executor
        if domain in self._disabled or spec is None or policy is None or executor is None:
            # 与「域从未存在」同形:拒绝并按策略分类落审计(由调用方 L5 承担)
            return {"status": "rejected", "reason": f"policy: 域 {domain} 不可用"}
        exit_check = policy.exit_host_allowed
        host = urlsplit(spec.endpoint).hostname or ""
        if not exit_check(host):
            return {"status": "rejected", "reason": f"policy: 出口主机未命中白名单({domain})"}
        validate = policy.validate_args
        invalid = validate(args)
        if invalid:
            return {"status": "rejected", "reason": invalid}

        url = spec.endpoint
        timeout_s = spec.timeout_ms / 1000
        for attempt in (1, 2):
            delivered = False
            exc: Exception | None = None
            response: object = None
            try:
                response = await executor(url=url, payload=dict(args), timeout_s=timeout_s)
                delivered = True
            except Exception as e:  # noqa: BLE001 —— 分类收敛,不裸抛
                exc = e
                delivered = _was_delivered(e)
            verdict = policy.classify(exc, delivered=delivered)
            if verdict == RetryClass.OK:
                redact = policy.redact
                return {"status": "executed", "result": redact(response)}
            # UNDELIVERED(连接层未送达):重试至多一次(切面单点承担);
            # PROTOCOL(已送达)立即收口不重试——重试判定只认分类,不再进下一轮
            if verdict != RetryClass.UNDELIVERED or attempt == 2:
                # 域可声明 failure_reason(迁移零漂移文案);缺省走安全通用文案
                fr = getattr(policy, "failure_reason", None)
                reason = fr(verdict, exc) if fr else _safe_reason(domain, verdict, exc)
                return {"status": "error", "reason": reason}
            log.warning(
                "toolgateway 域 %s 连接类失败,重试一次: trace=%s err=%s",
                domain,
                trace_id,
                type(exc).__name__,
            )
        raise RuntimeError("unreachable")  # pragma: no cover —— 循环必经 return


def _was_delivered(exc: Exception) -> bool:
    """是否「请求已送达」:读超时/HTTP 状态错=已送达;连接族=未送达。
    判定供策略器分类;具体分类仍由域策略器声明(可覆盖)。
    域收敛异常(081/094 同款前缀)按前缀语义归类,与策略器 classify
    保持同向(delivered 是一致性兜底信号,重试判定只认前缀分类)。"""
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
    if isinstance(exc, httpx.TimeoutException):
        return True  # 读超时:已送达
    if isinstance(exc, httpx.HTTPStatusError):
        return True  # 已取得响应
    if isinstance(exc, (ValueError, TypeError)):
        return True  # 报文畸形:已取得响应
    return False


def _safe_reason(domain: str, verdict: RetryClass, exc: Exception | None) -> str:
    """降级观察的安全文案:不暴露内部堆栈与端点地址细节(spec 明文)。"""
    label = "unreachable" if verdict == RetryClass.UNDELIVERED else "protocol"
    detail = type(exc).__name__ if exc is not None else "unknown"
    return f"{label}: 域 {domain} 暂不可用({detail})"
