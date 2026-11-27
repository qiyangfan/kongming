"""权益执行:managed 取快照(缺失即 fail-closed),standalone 用部署配置。

配额数值只从快照或部署配置来;本模块不保存计划/账单/支付状态。
"""

from dataclasses import dataclass

from kongming.control.models import EntitlementMissing, EntitlementRecord
from kongming.control.ports import TenantKeyResolver
from kongming.control.snapshot import SnapshotService


@dataclass(frozen=True)
class QuotaLimits:
    calls_daily: int
    calls_monthly: int
    bot_calls_daily: int
    tool_calls_per_minute: int


class EntitlementService:
    def __init__(
        self,
        *,
        snapshots: SnapshotService,
        mode: str,
        default_limits: QuotaLimits,
        resolver: TenantKeyResolver | None = None,
    ) -> None:
        self._snapshots = snapshots
        self._mode = mode
        self._defaults = default_limits
        self._resolver = resolver

    async def for_tenant_id(self, tenant_id: str) -> QuotaLimits:
        """受保护请求的配额视图;统一经 tenant_key 匹配,managed 缺失即拒绝。"""
        if self._mode != "managed":
            return self._defaults
        if self._resolver is None:
            raise EntitlementMissing("租户键解析器未装配")
        tenant_key = await self._resolver.key_for_id(tenant_id)
        if tenant_key is None:
            raise EntitlementMissing(f"租户 {tenant_id} 无本地租户键")
        return await self.for_tenant_key(tenant_key)

    async def for_tenant_key(self, tenant_key: str) -> QuotaLimits:
        if self._mode != "managed":
            return self._defaults
        sections = await self._snapshots.require_security()
        for item in sections.entitlements:
            if item.tenant_key == tenant_key:
                return self._limits(item)
        raise EntitlementMissing(f"租户 {tenant_key} 无有效权益快照")

    async def entitlement_keys(self, tenant_key: str) -> frozenset[str]:
        """租户可用的目录权益键;standalone 返回空集=不额外限制。"""
        if self._mode != "managed":
            return frozenset()
        sections = await self._snapshots.require_security()
        for item in sections.entitlements:
            if item.tenant_key == tenant_key:
                return frozenset(item.entitlement_keys)
        raise EntitlementMissing(f"租户 {tenant_key} 无有效权益快照")

    @staticmethod
    def _limits(item: EntitlementRecord) -> QuotaLimits:
        if item.status != "ACTIVE":
            raise EntitlementMissing(f"租户 {item.tenant_key} 权益状态为 {item.status}")
        return QuotaLimits(
            calls_daily=item.quota_calls_daily,
            calls_monthly=item.quota_calls_monthly,
            bot_calls_daily=item.quota_bot_calls_daily,
            tool_calls_per_minute=item.quota_tool_calls_per_minute,
        )
