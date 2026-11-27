"""商店目录消费:managed 以目录快照过滤货架/直跑并校验权益;standalone 沿用本地上架。

目录/权益/运营标记只在控制面写;本模块不提供任何写入口,也不伪造缺失条目。
"""

import logging

from kongming.control.entitlement import EntitlementService
from kongming.control.models import CatalogEntryRecord
from kongming.control.snapshot import SnapshotService

log = logging.getLogger(__name__)


class CatalogService:
    def __init__(
        self,
        *,
        snapshots: SnapshotService,
        entitlements: EntitlementService,
        mode: str,
    ) -> None:
        self._snapshots = snapshots
        self._entitlements = entitlements
        self._mode = mode

    @property
    def managed(self) -> bool:
        return self._mode == "managed"

    async def entries_for_tenant(
        self, tenant_key: str, *, include_unlisted: bool = False
    ) -> dict[str, CatalogEntryRecord]:
        """租户可见条目(bot_key → 条目);默认只含货架 LISTED,缺失返回空(不伪造)。"""
        if not self.managed:
            return {}
        view = await self._snapshots.view()
        if not view.present:
            return {}
        items = {
            entry.bot_key: entry
            for entry in view.sections.catalog
            if (include_unlisted or entry.listing == "LISTED")
            and (
                entry.visibility == "ALL"
                or "*" in entry.tenant_keys
                or tenant_key in entry.tenant_keys
            )
        }
        granted = await self._entitlements.entitlement_keys(tenant_key)
        return {
            key: entry
            for key, entry in items.items()
            if entry.entitlement_key is None or entry.entitlement_key in granted
        }

    async def allows_entry(self, tenant_key: str, bot_key: str) -> bool:
        """直跑/详情闸:LISTED 与 UNLISTED 都可直达;standalone 不限。"""
        if not self.managed:
            return True
        entries = await self.entries_for_tenant(tenant_key, include_unlisted=True)
        return bot_key in entries

    async def require_entry(self, tenant_key: str, bot_key: str) -> CatalogEntryRecord | None:
        """直跑/详情的目录条目;standalone 返回 None 表示不做目录限制。"""
        if not self.managed:
            return None
        entries = await self.entries_for_tenant(tenant_key, include_unlisted=True)
        return entries.get(bot_key)
