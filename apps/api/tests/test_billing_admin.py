"""覆盖套餐管理、租户选择和账单分页。"""
# pyright: reportPrivateUsage=false

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.seed import seed_all
from kongming.config import get_settings
from tests.conftest import MakeUser
from tests.test_billing import _user_with_key
from tests.test_billing_payment import _mk_invoice
from tests.test_chat_query import _bind, _default_tenant_id, _login
from tests.test_rag_admin import _admin_token

PLAN = {
    "plan_key": "pro",
    "name": "专业版",
    "monthly_tokens": 1000000,
    "monthly_calls": 10000,
    "price_per_mtok": 2,
    "price_per_call": 1,
}


async def _mk_plan(client: AsyncClient, admin: str) -> str:
    r = await client.post("/billing/plans", headers={"Authorization": f"Bearer {admin}"}, json=PLAN)
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def test_plan_update_with_audit(client: AsyncClient, session: AsyncSession) -> None:
    """套餐更新:字段生效+审计含变更前后。"""
    admin = await _admin_token(client, session)
    pid = await _mk_plan(client, admin)
    r = await client.patch(
        f"/billing/plans/{pid}",
        headers={"Authorization": f"Bearer {admin}"},
        json={**PLAN, "name": "旗舰版", "monthly_tokens": 5000000},
    )
    assert r.status_code == 200, r.text
    row = (
        (
            await session.execute(
                text(
                    "SELECT name, monthly_tokens FROM public.sys_billing_plan"
                    " WHERE id=CAST(:i AS uuid)"
                ),
                {"i": pid},
            )
        )
        .mappings()
        .one()
    )
    assert row["name"] == "旗舰版" and int(row["monthly_tokens"]) == 5000000
    audit = (
        (
            await session.execute(
                text(
                    "SELECT detail FROM public.sys_oper_log"
                    " WHERE action='billing.plan.update' AND target='pro'"
                )
            )
        )
        .mappings()
        .one()
    )
    assert audit["detail"]["before"]["monthly_tokens"] == 1000000
    assert audit["detail"]["after"]["monthly_tokens"] == 5000000


async def test_plan_delete_in_use_409_then_free_delete(
    client: AsyncClient, session: AsyncSession
) -> None:
    """spec「绑定中套餐拒删」:绑定 409;解绑后删除+审计。"""
    admin = await _admin_token(client, session)
    pid = await _mk_plan(client, admin)
    tid = await _default_tenant_id(session)
    r = await client.post(
        "/billing/tenants/bind-plan",
        headers={"Authorization": f"Bearer {admin}"},
        json={"tenant_id": str(tid), "plan_id": pid},
    )
    assert r.status_code == 200
    r2 = await client.delete(f"/billing/plans/{pid}", headers={"Authorization": f"Bearer {admin}"})
    assert r2.status_code == 409 and r2.json()["detail"] == "PLAN_IN_USE"
    # 解绑后可删
    await client.post(
        "/billing/tenants/bind-plan",
        headers={"Authorization": f"Bearer {admin}"},
        json={"tenant_id": str(tid), "plan_id": None},
    )
    r3 = await client.delete(f"/billing/plans/{pid}", headers={"Authorization": f"Bearer {admin}"})
    assert r3.status_code == 200
    gone = (
        await session.execute(
            text("SELECT count(*) FROM public.sys_billing_plan WHERE id=CAST(:i AS uuid)"),
            {"i": pid},
        )
    ).scalar_one()
    assert int(gone) == 0
    assert (
        await session.execute(
            text("SELECT count(*) FROM public.sys_oper_log WHERE action='billing.plan.delete'")
        )
    ).scalar_one() == 1


async def test_tenants_brief_with_billing_manage_only(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「单授 billing 管理可配置」:仅 system:billing:manage 角色可取租户清单。"""
    await seed_all(session, get_settings())
    uid = await make_user("billing_only")
    rid = (
        await session.execute(
            text(
                "INSERT INTO public.sys_role (id, role_key, role_name)"
                " VALUES (gen_random_uuid(), 'BILLING_OP', '计费运营') RETURNING id"
            )
        )
    ).scalar_one()
    perm_id = (
        await session.execute(
            text("SELECT id FROM public.sys_permission WHERE perm_code='system:billing:manage'")
        )
    ).scalar_one()
    await session.execute(
        text(
            "INSERT INTO public.sys_role_permission (role_id, permission_id)"
            " VALUES (CAST(:r AS uuid), CAST(:p AS uuid))"
        ),
        {"r": str(rid), "p": str(perm_id)},
    )
    await _bind(session, "sys_user_role", uid, str(rid))
    await _bind(session, "sys_user_tenant", uid, await _default_tenant_id(session))
    await session.commit()
    token = await _login(client, "billing_only")
    r = await client.get("/billing/tenants", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    items = r.json()
    assert any(t["tenant_key"] == "default" for t in items)
    assert all(set(t) == {"id", "tenant_key", "tenant_name"} for t in items)
    # Bot 下拉同解耦(单授 billing:manage 可列,解耦 bot:manage)
    tid = await _default_tenant_id(session)
    rb = await client.get(
        f"/billing/bots?tenant_id={tid}", headers={"Authorization": f"Bearer {token}"}
    )
    assert rb.status_code == 200, rb.text
    # 对照:无 billing:manage 的普通用户 403
    _, token2, _ = await _user_with_key(client, session, make_user, "bt_np")
    r2 = await client.get("/billing/tenants", headers={"Authorization": f"Bearer {token2}"})
    assert r2.status_code == 403


async def test_admin_invoices_pagination(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「管理面账单分页」:page/size/total 可翻页遍历;行附支付状态。"""
    admin = await _admin_token(client, session)
    tid = await _default_tenant_id(session)
    for i in range(3):
        await _mk_invoice(session, str(tid), amount=100 + i, period=f"2026-0{4 + i}")
    r = await client.get(
        "/billing/invoices?page=1&size=2", headers={"Authorization": f"Bearer {admin}"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] >= 3 and len(body["items"]) == 2
    assert body["page"] == 1 and body["size"] == 2
    r2 = await client.get(
        "/billing/invoices?page=2&size=2", headers={"Authorization": f"Bearer {admin}"}
    )
    assert len(r2.json()["items"]) >= 1
    ids1 = {i["id"] for i in body["items"]}
    ids2 = {i["id"] for i in r2.json()["items"]}
    assert not ids1 & ids2  # 翻页不重复
    assert "payment_status" in body["items"][0]  # 支付状态列(无单 null)
