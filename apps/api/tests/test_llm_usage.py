"""llmusage 端点测试(021 multi-model-routing + 038 agent-observability):
落库/反解/聚合/守门;采集补全(latency/status)/趋势/明细/时间线。"""

import uuid
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.seed import seed_all
from kongming.config import get_settings
from tests.conftest import MakeUser
from tests.test_toolcall import _bind, _login

INTERNAL = {"X-Internal-Token": "test-internal-token"}


async def _admin_token(client: AsyncClient, s: AsyncSession) -> str:
    await seed_all(s, get_settings())
    return await _login(client, "admin", "admin123")


def _records(tenant_key: str | None = "default") -> dict:
    return {
        "records": [
            {
                "trace_id": "tr-u1",
                "tenant_key": tenant_key,
                "model": "qwen-plus",
                "task": "SQL_GEN",
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
            },
            {
                "trace_id": "tr-u1",
                "tenant_key": tenant_key,
                "model": "qwen-plus",
                "task": "SUMMARIZE",
                "prompt_tokens": 200,
                "completion_tokens": 60,
                "total_tokens": 260,
            },
        ]
    }


async def test_internal_token_required(client: AsyncClient) -> None:
    r = await client.post("/internal/llm-usage", json=_records())
    assert r.status_code == 403


async def test_report_persists_and_resolves_tenant(
    client: AsyncClient, session: AsyncSession
) -> None:
    await seed_all(session, get_settings())
    r = await client.post("/internal/llm-usage", headers=INTERNAL, json=_records())
    assert r.status_code == 200
    assert r.json()["accepted"] == 2
    rows = (
        await session.execute(
            text(
                "SELECT tenant_id, model, task, total_tokens FROM public.sys_llm_usage"
                " ORDER BY task"
            )
        )
    ).all()
    assert len(rows) == 2
    default_tid = (
        await session.execute(text("SELECT id FROM public.sys_tenant WHERE tenant_key='default'"))
    ).scalar_one()
    assert all(str(r.tenant_id) == str(default_tid) for r in rows)
    assert {r.task for r in rows} == {"SQL_GEN", "SUMMARIZE"}


async def test_report_unknown_tenant_null(
    client: AsyncClient, session: AsyncSession
) -> None:
    await seed_all(session, get_settings())
    r = await client.post(
        "/internal/llm-usage", headers=INTERNAL, json=_records(tenant_key="ghost")
    )
    assert r.status_code == 200
    n = (
        await session.execute(
            text("SELECT count(*) FROM public.sys_llm_usage WHERE tenant_id IS NULL")
        )
    ).scalar_one()
    assert n == 2


async def test_report_validation_422(client: AsyncClient) -> None:
    r = await client.post(
        "/internal/llm-usage",
        headers=INTERNAL,
        json={"records": [{"model": "", "task": "X"}]},
    )
    assert r.status_code == 422
    r2 = await client.post("/internal/llm-usage", headers=INTERNAL, json={"records": []})
    assert r2.status_code == 422


async def test_summary_aggregates(
    client: AsyncClient, session: AsyncSession
) -> None:
    await seed_all(session, get_settings())
    await client.post("/internal/llm-usage", headers=INTERNAL, json=_records())
    token = await _login(client, "admin", "admin123")
    r = await client.get(
        "/system/llm-usage/summary", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 2
    by_task = {i["task"]: i for i in items}
    assert by_task["SQL_GEN"]["calls"] == 1
    assert by_task["SQL_GEN"]["total_tokens"] == 150
    assert by_task["SUMMARIZE"]["total_tokens"] == 260
    assert all(i["tenant_key"] == "default" for i in items)


async def test_summary_forbidden(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    await seed_all(session, get_settings())
    uid = await make_user("usage_plain")
    tid = (
        await session.execute(text("SELECT id FROM public.sys_tenant WHERE tenant_key='default'"))
    ).scalar_one()
    await _bind(session, "sys_user_tenant", uid, str(tid))
    token = await _login(client, "usage_plain")
    r = await client.get(
        "/system/llm-usage/summary", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 403

# ========== 038 agent-observability ==========


async def _seed_usage_rows(
    s: AsyncSession, rows: list[dict]
) -> None:
    for r in rows:
        await s.execute(
            text(
                "INSERT INTO public.sys_llm_usage"
                " (id, trace_id, tenant_id, model, task, prompt_tokens,"
                "  completion_tokens, total_tokens, latency_ms, status, created_at)"
                " VALUES (gen_random_uuid(), :tr, NULL, :m, :task, :pt, :ct, :tt,"
                "         :lat, :st, :ca)"
            ),
            r,
        )
    await s.commit()


async def test_report_persists_latency_status(
    client: AsyncClient, session: AsyncSession
) -> None:
    """038:上报含 latency_ms/status 落库(失败调用也上报,usage=0)。"""
    await seed_all(session, get_settings())
    payload = {
        "records": [
            {
                "trace_id": "tr-038a",
                "tenant_key": "default",
                "model": "qwen-plus",
                "task": "SQL_GEN",
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "latency_ms": 30001,
                "status": "ERROR",
            }
        ]
    }
    r = await client.post("/internal/llm-usage", headers=INTERNAL, json=payload)
    assert r.status_code == 200
    row = (
        await session.execute(
            text(
                "SELECT latency_ms, status FROM public.sys_llm_usage"
                " WHERE trace_id='tr-038a'"
            )
        )
    ).one()
    assert row.latency_ms == 30001
    assert row.status == "ERROR"


async def test_report_backcompat_defaults(
    client: AsyncClient, session: AsyncSession
) -> None:
    """038:旧 agent 上报(无新字段)缺省 SUCCESS/NULL latency,兼容窗口。"""
    await seed_all(session, get_settings())
    r = await client.post("/internal/llm-usage", headers=INTERNAL, json=_records())
    assert r.status_code == 200
    rows = (
        await session.execute(
            text(
                "SELECT status, latency_ms FROM public.sys_llm_usage"
                " WHERE trace_id='tr-u1'"
            )
        )
    ).all()
    assert len(rows) == 2
    assert all(r.status == "SUCCESS" and r.latency_ms is None for r in rows)


async def test_summary_error_and_latency_stats(
    client: AsyncClient, session: AsyncSession
) -> None:
    """038:calls_error 计数;延迟统计仅 SUCCESS 参与(ERROR 的 30001 不入)。"""
    await seed_all(session, get_settings())
    await _seed_usage_rows(
        session,
        [
            {"tr": "tr-038b", "m": "qwen-plus", "task": "SQL_GEN",
             "pt": 10, "ct": 5, "tt": 15, "lat": 100, "st": "SUCCESS",
             "ca": datetime.now(UTC)},
            {"tr": "tr-038b", "m": "qwen-plus", "task": "SQL_GEN",
             "pt": 10, "ct": 5, "tt": 15, "lat": 300, "st": "SUCCESS",
             "ca": datetime.now(UTC)},
            {"tr": "tr-038b", "m": "qwen-plus", "task": "SQL_GEN",
             "pt": 0, "ct": 0, "tt": 0, "lat": 30001, "st": "ERROR",
             "ca": datetime.now(UTC)},
        ],
    )
    token = await _login(client, "admin", "admin123")
    r = await client.get(
        "/system/llm-usage/summary", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200
    item = next(i for i in r.json()["items"] if i["task"] == "SQL_GEN")
    assert item["calls"] == 3
    assert item["calls_error"] == 1
    assert item["latency_ms_avg"] == 200  # (100+300)/2
    assert item["latency_ms_max"] == 300


async def test_trend_pads_zero_days(
    client: AsyncClient, session: AsyncSession
) -> None:
    """038:趋势逐天补零(桶数=days;今天桶含刚落记录)。"""
    await seed_all(session, get_settings())
    await _seed_usage_rows(
        session,
        [
            {"tr": "tr-038c", "m": "qwen-plus", "task": "CHAT",
             "pt": 1, "ct": 1, "tt": 2, "lat": 50, "st": "SUCCESS",
             "ca": datetime.now(UTC)},
        ],
    )
    token = await _login(client, "admin", "admin123")
    r = await client.get(
        "/system/llm-usage/trend",
        params={"days": 7},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    points = r.json()["points"]
    assert len(points) == 7
    assert sum(p["calls"] for p in points) == 1
    assert sum(p["calls_error"] for p in points) == 0
    assert sum(p["total_tokens"] for p in points) == 2
    assert all(p["calls"] == 0 for p in points[:-1])


async def test_trend_days_bounds(client: AsyncClient, session: AsyncSession) -> None:
    await seed_all(session, get_settings())
    token = await _login(client, "admin", "admin123")
    r = await client.get(
        "/system/llm-usage/trend",
        params={"days": 91},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 422


async def test_page_filters_and_total(
    client: AsyncClient, session: AsyncSession
) -> None:
    """038:明细分页——status/trace_id 筛选、倒序、total 一致。"""
    await seed_all(session, get_settings())
    base = {"m": "qwen-plus", "task": "SUMMARIZE", "pt": 1, "ct": 1, "tt": 2}
    await _seed_usage_rows(
        session,
        [
            {**base, "tr": "tr-038d", "lat": 10, "st": "SUCCESS",
             "ca": datetime.now(UTC)},
            {**base, "tr": "tr-038d", "lat": None, "st": "ERROR",
             "ca": datetime.now(UTC)},
            {**base | {"task": "SQL_GEN"}, "tr": "tr-038e", "lat": 5,
             "st": "SUCCESS", "ca": datetime.now(UTC)},
        ],
    )
    token = await _login(client, "admin", "admin123")
    hdr = {"Authorization": f"Bearer {token}"}
    r = await client.get(
        "/system/llm-usage/page",
        params={"status": "ERROR"},
        headers=hdr,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["rows"][0]["status"] == "ERROR"
    assert body["rows"][0]["tenant_key"] == "unknown"
    r2 = await client.get(
        "/system/llm-usage/page",
        params={"trace_id": "tr-038d"},
        headers=hdr,
    )
    assert r2.json()["total"] == 2
    assert {row["status"] for row in r2.json()["rows"]} == {"SUCCESS", "ERROR"}
    r3 = await client.get(
        "/system/llm-usage/page",
        params={"task": "SQL_GEN", "model": "qwen-plus"},
        headers=hdr,
    )
    assert r3.json()["total"] == 1


async def test_timeline_merges_three_tables(
    client: AsyncClient, session: AsyncSession
) -> None:
    """038:时间线按 trace 聚 LLM+TOOL+SQL 三路事件,时间升序。"""
    await seed_all(session, get_settings())
    tid = (
        await session.execute(text("SELECT id FROM public.sys_tenant WHERE tenant_key='default'"))
    ).scalar_one()
    admin_id = (
        await session.execute(text("SELECT id FROM public.sys_user WHERE username='admin'"))
    ).scalar_one()
    trace = uuid.uuid4().hex[:16]
    await _seed_usage_rows(
        session,
        [
            {"tr": trace, "m": "qwen-plus", "task": "REACT", "pt": 10, "ct": 5,
             "tt": 15, "lat": 800, "st": "SUCCESS", "ca": datetime.now(UTC)},
        ],
    )
    await session.execute(
        text(
            "INSERT INTO public.sys_tool_audit"
            " (id, trace_id, user_id, tenant_id, tool_name, status, duration_ms,"
            "  channel, created_at)"
            " VALUES (gen_random_uuid(), :tr, :u, :t, 'execute_sql', 'EXECUTED',"
            "         120, 'chat', :ca)"
        ),
        {"tr": trace, "u": admin_id, "t": tid, "ca": datetime.now(UTC)},
    )
    await session.execute(
        text(
            "INSERT INTO public.sys_sql_audit"
            " (id, trace_id, user_id, tenant_id, sql_text, verdict, duration_ms,"
            "  created_at)"
            " VALUES (gen_random_uuid(), :tr, :u, :t,"
            "         'SELECT 1', 'PASS', 40, :ca)"
        ),
        {"tr": trace, "u": admin_id, "t": tid, "ca": datetime.now(UTC)},
    )
    await session.commit()
    token = await _login(client, "admin", "admin123")
    r = await client.get(
        "/system/llm-usage/timeline",
        params={"trace_id": trace},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["trace_id"] == trace
    kinds = [e["kind"] for e in body["events"]]
    assert sorted(kinds) == ["LLM", "SQL", "TOOL"]
    ts_list = [e["ts"] for e in body["events"]]
    assert ts_list == sorted(ts_list)
    llm_ev = next(e for e in body["events"] if e["kind"] == "LLM")
    assert llm_ev["tokens"] == 15
    assert llm_ev["latency_ms"] == 800
    tool_ev = next(e for e in body["events"] if e["kind"] == "TOOL")
    assert tool_ev["name"] == "execute_sql"
    assert tool_ev["latency_ms"] == 120
    sql_ev = next(e for e in body["events"] if e["kind"] == "SQL")
    assert sql_ev["status"] == "PASS"
    assert sql_ev["name"] == "default"  # R1-B5:SQL 事件 name=数据源


async def test_timeline_empty_200(client: AsyncClient, session: AsyncSession) -> None:
    await seed_all(session, get_settings())
    token = await _login(client, "admin", "admin123")
    r = await client.get(
        "/system/llm-usage/timeline",
        params={"trace_id": "never-exists-038"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert r.json()["events"] == []


async def test_new_endpoints_forbidden(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """038:trend/page/timeline 同挂 system:llm-usage:view。"""
    await seed_all(session, get_settings())
    uid = await make_user("usage_plain2")
    tid = (
        await session.execute(text("SELECT id FROM public.sys_tenant WHERE tenant_key='default'"))
    ).scalar_one()
    await _bind(session, "sys_user_tenant", uid, str(tid))
    token = await _login(client, "usage_plain2")
    hdr = {"Authorization": f"Bearer {token}"}
    assert (
        await client.get("/system/llm-usage/trend", headers=hdr)
    ).status_code == 403
    assert (
        await client.get("/system/llm-usage/page", headers=hdr)
    ).status_code == 403
    assert (
        await client.get(
            "/system/llm-usage/timeline", params={"trace_id": "x"}, headers=hdr
        )
    ).status_code == 403

# ========== 038 R1 评审补测 ==========


async def test_summary_all_error_group_null_latency(
    client: AsyncClient, session: AsyncSession
) -> None:
    """R1-B4:纯 ERROR 组延迟统计 NULL 传播(全组无 SUCCESS latency 样本)。"""
    await seed_all(session, get_settings())
    await _seed_usage_rows(
        session,
        [
            {"tr": "tr-038f", "m": "qwen-plus", "task": "TITLER",
             "pt": 0, "ct": 0, "tt": 0, "lat": 5000, "st": "ERROR",
             "ca": datetime.now(UTC)},
            {"tr": "tr-038f", "m": "qwen-plus", "task": "TITLER",
             "pt": 0, "ct": 0, "tt": 0, "lat": 6000, "st": "ERROR",
             "ca": datetime.now(UTC)},
        ],
    )
    token = await _login(client, "admin", "admin123")
    r = await client.get(
        "/system/llm-usage/summary", headers={"Authorization": f"Bearer {token}"}
    )
    item = next(
        i for i in r.json()["items"] if i["task"] == "TITLER"
    )
    assert item["calls"] == 2
    assert item["calls_error"] == 2
    assert item["latency_ms_avg"] is None  # ERROR 不入延迟统计
    assert item["latency_ms_max"] is None


async def test_bot_sink_persists_new_columns(
    client: AsyncClient, session: AsyncSession
) -> None:
    """R1-B4(design 承诺):bot 请求内同步通道(insert_bot_usage_batch)
    落 latency_ms/status——收集器 ERROR 记录经 sink 可达落库。"""
    from kongming.llmusage.repository import SqlLlmUsageRepo

    await seed_all(session, get_settings())
    tid = (
        await session.execute(
            text("SELECT id FROM public.sys_tenant WHERE tenant_key='default'")
        )
    ).scalar_one()
    repo = SqlLlmUsageRepo(session)
    inserted = await repo.insert_bot_usage_batch(
        [
            {
                "trace_id": "tr-sink-038",
                "model": "qwen-plus",
                "task": "REACT",
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "seq": 0,
                "latency_ms": 1234,
                "status": "ERROR",
            }
        ],
        tenant_id=str(tid),
        bot_id=str(uuid.uuid4()),
    )
    assert inserted == 1
    row = (
        await session.execute(
            text(
                "SELECT latency_ms, status FROM public.sys_llm_usage"
                " WHERE trace_id='tr-sink-038'"
            )
        )
    ).one()
    assert row.latency_ms == 1234
    assert row.status == "ERROR"


async def test_trend_cross_month_bucketing(
    client: AsyncClient, session: AsyncSession
) -> None:
    """R1-B4(design 承诺):跨月窗口——上月日期行归上月桶,不溢进今天桶。"""
    await seed_all(session, get_settings())
    # 构造 35 天前±12h 的沪时日期(R2-F1:固定回退避开午夜边界;
    # 恒在 45 天窗口内且必非今天——原「day=15 偏移」算式在每月
    # 1-6/29-31 号会落到窗外或当天,CI 必挂)
    now_sh = datetime.now(UTC) + timedelta(hours=8)
    last_month = now_sh - timedelta(days=35, hours=12)
    await _seed_usage_rows(
        session,
        [
            {"tr": "tr-038g", "m": "qwen-plus", "task": "CHAT",
             "pt": 5, "ct": 5, "tt": 10, "lat": 30, "st": "SUCCESS",
             "ca": last_month - timedelta(hours=8)},  # 存回 UTC
        ],
    )
    token = await _login(client, "admin", "admin123")
    r = await client.get(
        "/system/llm-usage/trend",
        params={"days": 45},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    points = r.json()["points"]
    assert len(points) == 45
    assert sum(p["calls"] for p in points) == 1
    today = points[-1]["date"]
    bucket = next(p for p in points if p["calls"] == 1)
    assert bucket["date"] != today  # 归历史桶
    assert bucket["date"].endswith(last_month.strftime("-%m-%d"))


async def test_page_deep_offset_and_bounds(
    client: AsyncClient, session: AsyncSession
) -> None:
    """R1-B4:深翻页越界空页 total 不变;days 上下界合法。"""
    await seed_all(session, get_settings())
    await _seed_usage_rows(
        session,
        [
            {"tr": "tr-038h", "m": "qwen-plus", "task": "CHAT",
             "pt": 1, "ct": 1, "tt": 2, "lat": 5, "st": "SUCCESS",
             "ca": datetime.now(UTC)},
        ],
    )
    token = await _login(client, "admin", "admin123")
    hdr = {"Authorization": f"Bearer {token}"}
    r = await client.get(
        "/system/llm-usage/page", params={"page": 999}, headers=hdr
    )
    body = r.json()
    assert body["total"] >= 1
    assert body["rows"] == []
    for days in (1, 90):
        r_days = await client.get(
            "/system/llm-usage/trend", params={"days": days}, headers=hdr
        )
        assert r_days.status_code == 200
        assert len(r_days.json()["points"]) == days


async def test_report_invalid_status_and_negative_latency_422(
    client: AsyncClient, session: AsyncSession
) -> None:
    """R1-B4:status 非法值 422;latency_ms 负值 422。"""
    await seed_all(session, get_settings())
    base = {
        "trace_id": "tr-x",
        "tenant_key": "default",
        "model": "qwen-plus",
        "task": "CHAT",
    }
    r = await client.post(
        "/internal/llm-usage",
        headers=INTERNAL,
        json={"records": [{**base, "status": "BOOM"}]},
    )
    assert r.status_code == 422
    r2 = await client.post(
        "/internal/llm-usage",
        headers=INTERNAL,
        json={"records": [{**base, "latency_ms": -1}]},
    )
    assert r2.status_code == 422
