# pyright: reportPrivateUsage=false
"""worker 续读端点 /internal/task/context:goal/步骤投影 + 跨租户 404 同形。"""

from typing import Any

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

INTERNAL_HEADERS = {"X-Internal-Token": "test-internal-token"}


async def test_task_context_endpoint(
    client: AsyncClient, session: AsyncSession, make_user: Any
) -> None:
    from kongming.auth.seed import seed_all
    from kongming.config import get_settings
    from kongming.uuid7 import uuid7

    await seed_all(session, get_settings())
    uid = await make_user("ctx_user")
    tid = str(
        (
            await session.execute(
                text("SELECT id FROM public.sys_tenant WHERE tenant_key='default'")
            )
        ).scalar_one()
    )
    task_id = str(uuid7())
    await session.execute(
        text(
            "INSERT INTO public.sys_agent_task"
            " (id, tenant_id, user_id, goal, status, datasource_key,"
            "  budget_config, budget_used, events)"
            " VALUES (CAST(:i AS uuid), CAST(:t AS uuid), CAST(:u AS uuid),"
            "  '任务目标全文', 'RUNNING', 'ds', '{}', '{}', '[]')"
        ),
        {"i": task_id, "t": tid, "u": uid},
    )
    step_id = str(uuid7())
    await session.execute(
        text(
            "INSERT INTO public.sys_agent_task_step"
            " (id, task_id, ord, goal, status)"
            " VALUES (CAST(:s AS uuid), CAST(:i AS uuid), 0,"
            "  '第一步', 'DONE')"
        ),
        {"s": step_id, "i": task_id},
    )
    await session.commit()

    r = await client.get(
        f"/internal/task/context?task_id={task_id}&tenant_key=default", headers=INTERNAL_HEADERS
    )
    assert r.status_code == 200
    body = r.json()
    assert body["goal"] == "任务目标全文"
    assert body["steps"][0]["goal"] == "第一步"
    assert body["truncated"] is False

    # 未知租户键 → 任务归属校验失败 404 同形
    r2 = await client.get(
        f"/internal/task/context?task_id={task_id}&tenant_key=ghost", headers=INTERNAL_HEADERS
    )
    assert r2.status_code in (403, 404)


async def test_task_context_clamps_oversized_goal(
    client: AsyncClient, session: AsyncSession, make_user: Any
) -> None:
    """目标全文自身超限(创建口径 ≤4000 字,CJK 可至 ~12KB):字节截断保
    指令头部,载荷整体守住 8KB 钳制,截断如实标注。"""
    from kongming.auth.seed import seed_all
    from kongming.config import get_settings
    from kongming.uuid7 import uuid7

    await seed_all(session, get_settings())
    uid = await make_user("ctx_big")
    tid = str(
        (
            await session.execute(
                text("SELECT id FROM public.sys_tenant WHERE tenant_key='default'")
            )
        ).scalar_one()
    )
    task_id = str(uuid7())
    big_goal = "详" * 4000  # UTF-8 下 ~12KB,单靠裁步骤压不进 8KB
    await session.execute(
        text(
            "INSERT INTO public.sys_agent_task"
            " (id, tenant_id, user_id, goal, status, datasource_key,"
            "  budget_config, budget_used, events)"
            " VALUES (CAST(:i AS uuid), CAST(:t AS uuid), CAST(:u AS uuid),"
            "  :g, 'RUNNING', 'ds', '{}', '{}', '[]')"
        ),
        {"i": task_id, "t": tid, "u": uid, "g": big_goal},
    )
    await session.commit()

    r = await client.get(
        f"/internal/task/context?task_id={task_id}&tenant_key=default", headers=INTERNAL_HEADERS
    )
    assert r.status_code == 200
    body = r.json()
    assert body["truncated"] is True
    assert body["steps"] == []  # 步骤明细先裁
    assert len(r.content) <= 8 * 1024  # 钳制承诺守住
    assert body["goal"].startswith("详" * 100)  # 指令头部保留
