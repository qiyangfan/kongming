"""quality 用例层(069,零框架):轮次质量聚合编排与租户收敛。

租户隔离(audit 域同款口径):data_scope=ALL 的查询方显式传 tenant_id 过滤;
其余一律按请求租户强收敛(传值被覆盖——观测面的越权可见性等同数据越权)。
时间窗兜底:缺省回看 30 天(与反馈/审计默认窗一致)。
"""

import datetime as dt
from typing import cast

from kongming.quality.ports import QualityRepo

_DAYS_DEFAULT = 30
_DAYS_MAX = 90


class QualityService:
    def __init__(self, repo: QualityRepo) -> None:
        self._repo = repo

    async def _visible_tenant_ids(self, actor_id: str, tenant_id: str | None) -> list[str] | None:
        """租户收敛(audit 域同款口径):data_scope=ALL 的查询方显式传
        tenant_id 过滤(None=跨租户全量);其余一律收敛到其绑定租户全集
        (显式传值被忽略——观测面的越权可见性等同数据越权;无绑定=空集)。"""
        if await self._repo.user_has_scope_all(user_id=actor_id):
            return None if tenant_id is None else [tenant_id]
        return await self._repo.list_bound_tenant_ids(user_id=actor_id)

    async def summary(
        self,
        *,
        actor_id: str,
        tenant_id: str | None,
        start: dt.datetime | None,
        end: dt.datetime | None,
        days: int,
    ) -> dict[str, object]:
        vis = await self._visible_tenant_ids(actor_id, tenant_id)
        overview = await self._repo.overview(tenant_ids=vis, start=start, end=end)
        trend = await self._repo.daily_trend(tenant_ids=vis, days=min(days, _DAYS_MAX))
        by_ds = await self._repo.by_datasource(tenant_ids=vis, start=start, end=end)
        by_pv = await self._repo.by_prompt_version(tenant_ids=vis, start=start, end=end)
        total = int(overview.get("total", 0) or 0)

        def _rate(n: object) -> float:
            if not total:
                return 0.0
            # PG count(*) 产物恒 int;运行时收窄(overview 为 dict[str, Any] 边界)
            return round(int(cast("int", n or 0)) / total, 4)

        return {
            "overview": {
                "total": total,
                "success": int(overview.get("success", 0) or 0),
                "no_data": int(overview.get("no_data", 0) or 0),
                "blocked": int(overview.get("blocked", 0) or 0),
                "failed": int(overview.get("failed", 0) or 0),
                "no_data_rate": _rate(overview.get("no_data")),
                "blocked_rate": _rate(overview.get("blocked")),
                "failed_rate": _rate(overview.get("failed")),
                "selfheal_rate": _rate(overview.get("selfheal")),
                "down_rate": _rate(overview.get("down")),
                "avg_tokens": round(float(overview.get("avg_tokens", 0) or 0), 1),
            },
            "trend": trend,
            "by_datasource": by_ds,
            "by_prompt_version": by_pv,
            "days": min(days, _DAYS_MAX),
        }

    async def page(
        self,
        *,
        actor_id: str,
        tenant_id: str | None,
        status: str | None,
        datasource_key: str | None,
        start: dt.datetime | None,
        end: dt.datetime | None,
        page: int,
        size: int,
    ) -> tuple[int, list[dict[str, object]]]:
        vis = await self._visible_tenant_ids(actor_id, tenant_id)
        return await self._repo.page(
            tenant_ids=vis,
            status=status,
            datasource_key=datasource_key,
            start=start,
            end=end,
            page=page,
            size=size,
        )
