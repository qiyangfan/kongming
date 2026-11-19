"""quality 用例层(零框架):轮次质量聚合编排与租户收敛。

租户隔离(audit 域同款口径):data_scope=ALL 的查询方显式传 tenant_id 过滤;
其余一律按请求租户强收敛(传值被覆盖——观测面的越权可见性等同数据越权)。
时间窗兜底:缺省回看 30 天(与反馈/审计默认窗一致)。
"""

import datetime as dt
from typing import cast

from kongming.quality.ports import QualityRepo

_DAYS_DEFAULT = 30
_DAYS_MAX = 90
_CONTEXT_LIMIT = 50  # 同会话串联轮次上限(spec「同会话轮次串联」)
_EXPORT_MAX_ROWS = 5000  # 导出行数上限(spec「轮次质量导出」;超出截断并如实可辨)


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
        low_score_threshold: int,
    ) -> dict[str, object]:
        vis = await self._visible_tenant_ids(actor_id, tenant_id)
        overview = await self._repo.overview(
            tenant_ids=vis, start=start, end=end, low_score_threshold=low_score_threshold
        )
        trend = await self._repo.daily_trend(tenant_ids=vis, days=min(days, _DAYS_MAX))
        by_ds = await self._repo.by_datasource(tenant_ids=vis, start=start, end=end)
        by_pv = await self._repo.by_prompt_version(tenant_ids=vis, start=start, end=end)
        total = int(overview.get("total", 0) or 0)
        return {
            "overview": _overview_payload(
                overview, total=total, low_score_threshold=low_score_threshold
            ),
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
        fallback: str | None,
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
            fallback=fallback,
            start=start,
            end=end,
            page=page,
            size=size,
        )

    async def score_detail(
        self, *, actor_id: str, tenant_id: str | None, message_id: str
    ) -> dict[str, object] | None:
        """评分详情(按消息取);租户不可见/未评分 → None(路由映射 404)。"""
        vis = await self._visible_tenant_ids(actor_id, tenant_id)
        return await self._repo.score_detail(tenant_ids=vis, message_id=message_id)

    async def session_context(
        self, *, actor_id: str, tenant_id: str | None, message_id: str
    ) -> dict[str, object] | None:
        """同会话轮次串联(锚点轮必含,升序,上限 50);租户不可见 → None(404)。"""
        vis = await self._visible_tenant_ids(actor_id, tenant_id)
        return await self._repo.session_context(
            tenant_ids=vis, message_id=message_id, limit=_CONTEXT_LIMIT
        )

    async def stage_trace(
        self, *, actor_id: str, tenant_id: str | None, message_id: str
    ) -> dict[str, object] | None:
        """节点阶段轨迹;租户不可见 → None(404),未落轨迹 → trace 键为 None。"""
        vis = await self._visible_tenant_ids(actor_id, tenant_id)
        return await self._repo.stage_trace_detail(tenant_ids=vis, message_id=message_id)

    async def export_rows(
        self,
        *,
        actor_id: str,
        tenant_id: str | None,
        status: str | None,
        datasource_key: str | None,
        fallback: str | None,
        start: dt.datetime | None,
        end: dt.datetime | None,
    ) -> tuple[list[dict[str, object]], bool]:
        """导出行集(page 同筛选同收敛);返回 (行, 是否截断)。"""
        vis = await self._visible_tenant_ids(actor_id, tenant_id)
        rows = await self._repo.export_rows(
            tenant_ids=vis,
            status=status,
            datasource_key=datasource_key,
            fallback=fallback,
            start=start,
            end=end,
            limit=_EXPORT_MAX_ROWS + 1,
        )
        truncated = len(rows) > _EXPORT_MAX_ROWS
        return rows[:_EXPORT_MAX_ROWS], truncated


def _fallback_total(overview: dict[str, object]) -> int:
    """兜底轮总数(闲聊+澄清;no_data 率剔除口径的分母缩减量)。"""
    chitchat = int(cast("int", overview.get("chitchat") or 0))
    clarify = int(cast("int", overview.get("clarify") or 0))
    return chitchat + clarify


def _rate(n: object, total: int) -> float:
    """以窗口总轮数为分母的占比(总数为 0 时收敛 0)。"""
    if not total:
        return 0.0
    # PG count(*) 产物恒 int;运行时收窄(overview 为 dict[str, Any] 边界)
    return round(int(cast("int", n or 0)) / total, 4)


def _rate_adjusted(n: object, excluded: int, total: int) -> float:
    """剔除兜底轮后的率(分子分母同步缩减;全兜底窗=0 除零收敛)。"""
    denom = total - excluded
    if denom <= 0:
        return 0.0
    return round(max(0, int(cast("int", n or 0)) - excluded) / denom, 4)


def _overview_payload(
    overview: dict[str, object], *, total: int, low_score_threshold: int
) -> dict[str, object]:
    """概览计数与各比率(兜底轮剔除口径用于 no_data 率)。"""
    excluded = _fallback_total(overview)
    return {
        "total": total,
        "success": int(cast("int", overview.get("success") or 0)),
        "no_data": int(cast("int", overview.get("no_data") or 0)),
        "blocked": int(cast("int", overview.get("blocked") or 0)),
        "failed": int(cast("int", overview.get("failed") or 0)),
        # 兜底轮分类计数(spec「兜底轮分类观测」;闲聊含直通与语义判定)
        "chitchat_count": int(cast("int", overview.get("chitchat") or 0)),
        "clarify_count": int(cast("int", overview.get("clarify") or 0)),
        # 未覆盖率剔除兜底轮(分子分母均不含——兜底轮不是「没找到数据」),
        # 原始 no_data 计数如实保留
        "no_data_rate": _rate_adjusted(overview.get("no_data"), excluded, total),
        "blocked_rate": _rate(overview.get("blocked"), total),
        "failed_rate": _rate(overview.get("failed"), total),
        "selfheal_rate": _rate(overview.get("selfheal"), total),
        "down_rate": _rate(overview.get("down"), total),
        "avg_tokens": round(float(cast("float", overview.get("avg_tokens") or 0)), 1),
        # 答案评分观测(spec「轮次质量观测」):已评分轮均分与低分计数
        # (未评分轮不进均值分母;avg 为 None=窗口内零评分)
        "avg_score": (
            float(cast("float", overview.get("avg_score")))
            if overview.get("avg_score") is not None
            else None
        ),
        "low_score_count": int(cast("int", overview.get("low_score_count") or 0)),
        "low_score_threshold": low_score_threshold,
    }
