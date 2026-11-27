"""共享策略包执行视图:把激活包解析成本地规则名集合。

包引用本地不存在的规则即非法(fail-closed,由护栏走拒绝/替换路径);
standalone 不过滤(None=按本地规则目录全量执行)。
"""

from kongming.control.models import ControlError
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
    ) -> None:
        self._snapshots = snapshots
        self._mode = mode
        self._local_rules = local_rule_names

    async def rule_names_for_tenant(self, tenant_id: str) -> frozenset[str] | None:
        """返回该租户生效的规则名集合;None=不过滤(standalone)。"""
        if self._mode != "managed":
            return None
        sections = await self._snapshots.require_security()
        effective: set[str] = set()
        for package in sections.policy_packages:
            if package.status != "ACTIVE":
                continue
            if package.tenant_ids and tenant_id not in package.tenant_ids:
                continue
            for name in package.rule_names:
                if name not in self._local_rules:
                    raise PolicyPackageInvalid(
                        f"策略包 {package.package_key} 引用了未知规则 {name}"
                    )
                effective.add(name)
        return frozenset(effective)
