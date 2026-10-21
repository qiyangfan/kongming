"""覆盖模型用量对账、偏差计算、降级和接口鉴权。"""
# pyright: reportPrivateUsage=false

import httpx
from httpx import AsyncClient, MockTransport, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.seed import seed_all
from kongming.billing.reconciliation import (
    CostTrack,
    build_report,
    collect_local_usage,
    fetch_muniu_cost,
)
from kongming.config import get_settings

# ── muniu 响应解析(transport 注入,不碰真实网络) ──


async def test_fetch_muniu_cost_sums_rows() -> None:
    """group_by=key 多行求和;行内字段缺失容忍为 0;请求带 Bearer 与窗口。"""
    rows = [
        {
            "dim": "key-a",
            "requests": 3,
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "cost_credits": 8_600_000_000,
        },
        {"dim": "key-b", "requests": 1, "prompt_tokens": 10, "cost_credits": 1_400_000_000},
    ]
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization", "")
        # 真实契约形态(muniu admin.go usage handler):{"points":[...], "from","to","group_by"}
        return Response(200, json={"points": rows, "from": "f", "to": "t", "group_by": "key"})

    track = await fetch_muniu_cost(
        "http://muniu.test", "tok-xyz", window_days=7, transport=MockTransport(handler)
    )
    assert track.available is True
    assert track.requests == 4
    assert track.cost_credits == 10_000_000_000
    assert track.completion_tokens == 50
    assert seen["auth"] == "Bearer tok-xyz"
    assert "group_by=key" in seen["url"] and "from=" in seen["url"]


async def test_fetch_muniu_cost_unconfigured_token() -> None:
    track = await fetch_muniu_cost("http://muniu.test", "", window_days=7)
    assert track.available is False and "未配置" in track.reason


async def test_fetch_muniu_cost_rejects_non_envelope_shapes() -> None:
    """顶层数组/缺 points 键均为形态异常(信封契约,muniu admin.go 为准)。"""
    bodies: tuple[object, ...] = ({"dim": "k", "cost_credits": 1}, {"rows": []}, {"points": "x"})
    for body in bodies:
        track = await fetch_muniu_cost(
            "http://muniu.test",
            "tok",
            window_days=7,
            transport=MockTransport(lambda request, b=body: Response(200, json=b)),
        )
        assert track.available is False, body
        assert "形态异常" in track.reason or "points" in track.reason


async def test_fetch_muniu_cost_degrades_without_raising() -> None:
    """非 200/非 JSON/网络异常全部显式降级;token 永不进入 reason。"""
    import httpx as _httpx

    def handler_502(request: httpx.Request) -> httpx.Response:
        return Response(502, text="x")

    def handler_bad_json(request: httpx.Request) -> httpx.Response:
        return Response(200, content=b"not-json")

    def handler_boom(request: httpx.Request) -> httpx.Response:
        raise _httpx.ConnectError("boom")

    for handler in (handler_502, handler_bad_json, handler_boom):
        track = await fetch_muniu_cost(
            "http://muniu.test", "secret-token", window_days=7, transport=MockTransport(handler)
        )
        assert track.available is False
        assert track.reason and "secret-token" not in track.reason
    # 网络异常带异常类名(可观测,不带内部细节)
    track2 = await fetch_muniu_cost(
        "http://muniu.test", "secret-token", window_days=7, transport=MockTransport(handler_boom)
    )
    assert "ConnectError" in track2.reason


# ── 偏差率纯函数(1 厘 = 1e6 credit) ──


def test_build_report_deviation_and_threshold() -> None:
    # 成本 1000 万 credit = 10 厘;账单 12 厘 → 偏差 0.2,恰在阈值不告警
    r = build_report(
        window_days=7,
        product_tokens=100,
        product_invoice_li=12,
        cost=CostTrack(available=True, cost_credits=10_000_000),
        threshold=0.2,
    )
    assert r.cost_li_equiv == 10
    assert r.deviation_ratio == 0.2 and r.alert is False
    # 13 厘 → 0.3 > 0.2 告警
    r2 = build_report(
        window_days=7,
        product_tokens=100,
        product_invoice_li=13,
        cost=CostTrack(available=True, cost_credits=10_000_000),
        threshold=0.2,
    )
    assert r2.alert is True


def test_build_report_zero_cost_with_invoice() -> None:
    r = build_report(
        window_days=7,
        product_tokens=50,
        product_invoice_li=8,
        cost=CostTrack(available=True, cost_credits=0),
        threshold=0.2,
    )
    assert r.deviation_ratio is None and r.alert is False
    assert any("人工核对" in a for a in r.annotations)


def test_build_report_sub_li_cost_no_crash() -> None:
    """0 < 成本 < 1 厘(整除分母为 0):不得抛 ZeroDivisionError,显式标注人工核对。"""
    r = build_report(
        window_days=7,
        product_tokens=50,
        product_invoice_li=8,
        cost=CostTrack(available=True, cost_credits=500_000),  # 0.5 厘
        threshold=0.2,
    )
    assert r.cost_li_equiv == 0
    assert r.deviation_ratio is None and r.alert is False
    assert any("人工核对" in a for a in r.annotations)


def test_build_report_cost_unavailable_no_deviation() -> None:
    r = build_report(
        window_days=7,
        product_tokens=50,
        product_invoice_li=8,
        cost=CostTrack(available=False, reason="muniu 不可达"),
        threshold=0.2,
    )
    assert r.cost_li_equiv is None and r.deviation_ratio is None and r.alert is False
    assert any("不可用" in a for a in r.annotations)


# ── 本侧汇总(真实 SQL)与端点 ──


async def test_collect_local_usage_sums_two_tracks(session: AsyncSession) -> None:
    await session.execute(
        text(
            "INSERT INTO public.sys_llm_usage (id, tenant_id, model, task, total_tokens) "
            "VALUES (gen_random_uuid(), NULL, 'qwen-plus', 'SQL_GEN', 12345)"
        )
    )
    await session.execute(
        text(
            "INSERT INTO public.sys_invoice (id, tenant_id, period, usage_json, amount) "
            "VALUES (gen_random_uuid(), gen_random_uuid(), '2026-09', '{}', 777)"
        )
    )
    tokens, invoice_li = await collect_local_usage(session, window_days=30)
    assert tokens >= 12345 and invoice_li >= 777


async def test_endpoint_requires_manage_perm(client: AsyncClient, session: AsyncSession) -> None:
    """无登录 401;SUPER_ADMIN 可达且成本侧未配置时显式标注。"""
    r = await client.get("/system/billing/cost-reconciliation")
    assert r.status_code in (401, 403)
    await seed_all(session, get_settings())
    await session.commit()
    lr = await client.post("/auth/login", json={"username": "admin", "password": "admin123"})
    token = lr.json()["access_token"]
    r2 = await client.get(
        "/system/billing/cost-reconciliation?window_days=30",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["cost"]["available"] is False
    assert body["alert"] is False
    assert body["window_days"] == 30
