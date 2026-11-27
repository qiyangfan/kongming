"""共享策略包执行视图:把激活包解析成本地规则名集合。

包引用本地不存在的规则即非法(fail-closed,由护栏走拒绝/替换路径);
standalone 不过滤(None=按本地规则目录全量执行)。
"""

from kongming.control.models import ControlError, TenantResolutionError
from kongming.control.ports import TenantKeyResolver
from kongming.control.snapshot import SnapshotService


class PolicyPackageInvalid(ControlError):
    """包引用了本地规则目录中不存在的规则。"""


class PolicyService:
    def __init__(
        self,
        *,
        snapshots: SnapshotService,
        mode: str,
        local_rule_names: frozenset[str],
        resolver: TenantKeyResolver | None = None,
    ) -> None:
        self._snapshots = snapshots
        self._mode = mode
        self._local_rules = local_rule_names
        self._resolver = resolver

    async def rule_names_for_tenant(self, tenant_id: str) -> frozenset[str] | None:
        """返回该租户生效的规则名集合;None=不过滤(standalone)。

        作用域以 tenant_key 为准:本地 tenant_id 只用于解析键,不直接与
        快照 tenant_id 比较(两端 UUID 不保证一致)。
        """
        if self._mode != "managed":
            return None
        if self._resolver is None:
            raise TenantResolutionError("租户键解析器未装配")
        tenant_key = await self._resolver.key_for_id(tenant_id)
        if tenant_key is None:
            raise TenantResolutionError(f"租户 {tenant_id} 无本地租户键")
        sections = await self._snapshots.require_security()
        effective: set[str] = set()
        for package in sections.policy_packages:
            if package.status != "ACTIVE":
                continue
            if "*" not in package.tenant_keys and tenant_key not in package.tenant_keys:
                continue
            # 锦官只下发策略包引用，不存服务域规则体/规则名；本地无显式映射时
            # 按该包的本地规则目录全量执行，避免静默关闭护栏。
            names = package.rule_names or tuple(self._local_rules)
            for name in names:
                if name not in self._local_rules:
                    raise PolicyPackageInvalid(
                        f"策略包 {package.package_key} 引用了未知规则 {name}"
                    )
                effective.add(name)
        return frozenset(effective)
