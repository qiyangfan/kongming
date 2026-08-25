"""llmusage 端点测试(021 multi-model-routing):落库/反解/聚合/守门。"""

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
