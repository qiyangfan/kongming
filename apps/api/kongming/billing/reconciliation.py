"""LLM 成本对账(055):产品牌价轨与 muniu credit 成本轨的量级核对。

只读旁路纪律:任何失败降级为报告内显式「不可用」标注(HTTP 200 结构化
呈现),绝不抛出、绝不影响计费主链路,禁止以静默空值替代。两轨不做硬
换算——牌价是产品定价,允许与成本解耦;比对对象是窗口内的量级与偏差率。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_CREDIT_PER_LI = 1_000_000  # 1 厘(1e-3 元) = 1e6 credit(1e-9 元)


class CostTrack(BaseModel):
    """成本轨(muniu credit 口径)。available=False 时 reason 必非空。"""

    available: bool
    reason: str = ""
    requests: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_credits: int = 0


class ReconciliationReport(BaseModel):
    window_days: int
    window_since: str
    product_tokens: int
    product_invoice_li: int
    cost: CostTrack
    cost_li_equiv: int | None = None
    deviation_ratio: float | None = None
    threshold: float
    alert: bool = False
    annotations: list[str] = []


def _window_since(window_days: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=window_days)


def _window_since_rfc3339(window_days: int) -> str:
    return _window_since(window_days).isoformat()


def _sum_rows(rows: list[dict[str, Any]]) -> CostTrack:
    def _i(row: dict[str, Any], key: str) -> int:
        v = row.get(key, 0)
        return v if isinstance(v, int) else 0

    return CostTrack(
        available=True,
        requests=sum(_i(r, "requests") for r in rows),
        prompt_tokens=sum(_i(r, "prompt_tokens") for r in rows),
        completion_tokens=sum(_i(r, "completion_tokens") for r in rows),
        cost_credits=sum(_i(r, "cost_credits") for r in rows),
    )


async def fetch_muniu_cost(
    base_url: str,
    token: str,
    *,
    window_days: int,
    transport: httpx.AsyncBaseTransport | None = None,
) -> CostTrack:
    """拉 muniu 管理 API usage 汇总(group_by=key,全 key 求和)。

    任何失败 → available=False + reason,不抛出(纯读旁路纪律);
    异常只带异常类名,token 永不进入任何消息或日志。
    transport 仅供测试注入(生产缺省 None,自建真实客户端)。
    """
    if not token:
        return CostTrack(available=False, reason="muniu_admin_token 未配置")
    params: dict[str, str] = {"group_by": "key", "from": _window_since_rfc3339(window_days)}
    try:
        async with httpx.AsyncClient(timeout=5.0, transport=transport) as client:
            resp = await client.get(
                f"{base_url.rstrip('/')}/admin/v1/usage",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
    except httpx.HTTPError as exc:
        return CostTrack(available=False, reason=f"muniu 不可达: {type(exc).__name__}")
    if resp.status_code != 200:
        return CostTrack(available=False, reason=f"muniu HTTP {resp.status_code}")
    try:
        payload = cast("object", resp.json())
    except ValueError:
        return CostTrack(available=False, reason="muniu 响应非 JSON")
    # muniu 真实契约:{"points":[UsagePoint...], "from","to","group_by"}(admin.go usage handler)
    envelope = cast("dict[str, object] | None", payload if isinstance(payload, dict) else None)
    if envelope is None:
        return CostTrack(available=False, reason="muniu 响应形态异常(非对象)")
    points = cast("list[object] | None", envelope.get("points"))
    if not isinstance(points, list):
        return CostTrack(available=False, reason="muniu 响应缺 points 数组")
    typed: list[dict[str, Any]] = []
    for raw in points:
        if isinstance(raw, dict):
            typed.append(cast("dict[str, Any]", raw))
    return _sum_rows(typed)


async def collect_local_usage(session: AsyncSession, *, window_days: int) -> tuple[int, int]:
    """牌价轨:(窗口内 token 总量, 窗口内开票金额厘)。

    账单为月度出账(sys_invoice.period),窗口跨月时与成本轨存在自然
    错位——报告以 annotations 说明,不做强行摊平。
    """
    since = _window_since(window_days)
    tokens = (
        await session.execute(
            text(
                "SELECT COALESCE(SUM(total_tokens), 0) FROM public.sys_llm_usage "
                "WHERE created_at >= :since"
            ),
            {"since": since},
        )
    ).scalar_one()
    invoice_li = (
        await session.execute(
            text(
                "SELECT COALESCE(SUM(amount), 0) FROM public.sys_invoice "
                "WHERE created_at >= :since AND status <> 'VOID'"
            ),
            {"since": since},
        )
    ).scalar_one()
    return int(tokens), int(invoice_li)


def build_report(
    *,
    window_days: int,
    product_tokens: int,
    product_invoice_li: int,
    cost: CostTrack,
    threshold: float,
) -> ReconciliationReport:
    """纯函数组装报告与偏差判定(测试主战场)。"""
    annotations: list[str] = ["牌价轨与成本轨不做硬换算;牌价为产品定价,允许与成本解耦"]
    if product_invoice_li and not cost.available:
        annotations.append("成本侧不可用,偏差未判定")
    cost_li_equiv: int | None = None
    deviation: float | None = None
    alert = False
    if cost.available:
        cost_li_equiv = cost.cost_credits // _CREDIT_PER_LI
        if cost_li_equiv > 0:
            deviation = abs(product_invoice_li - cost_li_equiv) / cost_li_equiv
            alert = deviation > threshold
        elif product_invoice_li > 0:
            # 含两种形态:成本恰 0;0 < 成本 < 1 厘(整除后分母为 0,不可判偏差)
            annotations.append("成本侧窗口内不足 1 厘而牌价账单非 0,请人工核对(路由/计量漂移候选)")
        annotations.append("账单为月度出账,查询窗口跨月时两侧口径存在自然错位")
    return ReconciliationReport(
        window_days=window_days,
        window_since=_window_since_rfc3339(window_days),
        product_tokens=product_tokens,
        product_invoice_li=product_invoice_li,
        cost=cost,
        cost_li_equiv=cost_li_equiv,
        deviation_ratio=deviation,
        threshold=threshold,
        alert=alert,
        annotations=annotations,
    )
