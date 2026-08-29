"""035 mock-payment-gateway 测试(测试先行,资金类):支付单创建/回调核销
幂等/伪签/金额不符/失败非终态/取消/惰性过期/作废/mock 开关/隔离。

核销路径唯一(design D1):mock-simulate 与 callback 两入口共用同一
reconcile——两入口各自的行为断言都打到同一状态机。
"""
# pyright: reportPrivateUsage=false

from typing import Any, cast

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.billing.channels import MockChannel
from kongming.config import get_settings
from tests.conftest import MakeUser
from tests.test_billing import _user_with_key
from tests.test_chat_query import _default_tenant_id


async def _mk_invoice(
    s: AsyncSession, tenant_id: str, amount: int = 12345, status: str = "PENDING",
    period: str = "2026-07",
) -> str:
    rid = (
        await s.execute(
            text(
                "INSERT INTO public.sys_invoice"
                " (id, tenant_id, period, usage_json, amount, status)"
                " VALUES (gen_random_uuid(), CAST(:t AS uuid), :p, '{}'::jsonb,"
                "         :a, :st)"
                " RETURNING id"
            ),
            {"t": tenant_id, "p": period, "a": amount, "st": status},
        )
    ).scalar_one()
    await s.commit()
    return str(rid)


def _sign(payment_id: str, trade_no: str, result: str, amount: int) -> str:
    return MockChannel(get_settings().billing_mock_secret).sign_payload(
        payment_id=payment_id, trade_no=trade_no, result=result, amount=amount
    )


async def _payment_row(s: AsyncSession, payment_id: str) -> dict[str, Any]:
    row = (
        await s.execute(
            text("SELECT * FROM public.sys_payment WHERE id=CAST(:i AS uuid)"),
            {"i": payment_id},
        )
    ).mappings().one()
    return dict(row)


async def _oper_count(s: AsyncSession, action: str, target: str) -> int:
    return int(
        (
            await s.execute(
                text(
                    "SELECT count(*) FROM public.sys_oper_log"
                    " WHERE action=:a AND target=:t"
                ),
                {"a": action, "t": target},
            )
        ).scalar_one()
    )


# ── 创建 ──


async def test_create_payment_first(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「首次发起支付」:PENDING 账单→CREATED 单(金额快照+过期)+审计。"""
    _, token, _ = await _user_with_key(client, session, make_user, "pay_c1")
    tid = await _default_tenant_id(session)
    inv = await _mk_invoice(session, str(tid))
    r = await client.post(
        f"/portal/invoices/{inv}/payments",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "CREATED" and body["amount"] == 12345
    assert body["channel"] == "MOCK" and body["expires_at"]
    assert await _oper_count(session, "payment.create", body["id"]) == 1


async def test_create_payment_reissue_cancels_old(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「重复发起作废旧单」:在途单恒一。"""
    _, token, _ = await _user_with_key(client, session, make_user, "pay_c2")
    tid = await _default_tenant_id(session)
    inv = await _mk_invoice(session, str(tid))
    h = {"Authorization": f"Bearer {token}"}
    r1 = await client.post(f"/portal/invoices/{inv}/payments", headers=h)
    r2 = await client.post(f"/portal/invoices/{inv}/payments", headers=h)
    assert r1.status_code == 201 and r2.status_code == 201
    old = await _payment_row(session, r1.json()["id"])
    assert old["status"] == "CANCELLED"
    assert (await _payment_row(session, r2.json()["id"]))["status"] == "CREATED"
    open_cnt = int(
        (
            await session.execute(
                text(
                    "SELECT count(*) FROM public.sys_payment"
                    " WHERE invoice_id=CAST(:v AS uuid) AND status='CREATED'"
                ),
                {"v": inv},
            )
        ).scalar_one()
    )
    assert open_cnt == 1


async def test_create_payment_paid_or_void_invoice_409(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「已支付账单拒再付」:PAID/VOID 均 409。"""
    _, token, _ = await _user_with_key(client, session, make_user, "pay_c3")
    tid = await _default_tenant_id(session)
    inv_paid = await _mk_invoice(session, str(tid), status="PAID", period="2026-06")
    inv_void = await _mk_invoice(session, str(tid), status="VOID", period="2026-05")
    h = {"Authorization": f"Bearer {token}"}
    rp = await client.post(f"/portal/invoices/{inv_paid}/payments", headers=h)
    assert rp.status_code == 409
    rv = await client.post(f"/portal/invoices/{inv_void}/payments", headers=h)
    assert rv.status_code == 409


async def test_create_payment_cross_tenant_404(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「跨租户账单 404 同形」。"""
    _, token, _ = await _user_with_key(client, session, make_user, "pay_c4")
    other_tenant = "00000000-0000-0000-0000-000000000099"
    await session.execute(
        text(
            "INSERT INTO public.sys_tenant (id, tenant_key, tenant_name)"
            " VALUES (CAST(:t AS uuid), 'othert', '他租户')"
        ),
        {"t": other_tenant},
    )
    await session.commit()
    inv = await _mk_invoice(session, other_tenant)
    r = await client.post(
        f"/portal/invoices/{inv}/payments",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 404


# ── 核销(mock-simulate 入口) ──


async def test_mock_simulate_success_pays_invoice(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「成功回调核销」:simulate 成功→单 PAID+账单 PAID+审计。"""
    _, token, _ = await _user_with_key(client, session, make_user, "pay_s1")
    tid = await _default_tenant_id(session)
    inv = await _mk_invoice(session, str(tid))
    h = {"Authorization": f"Bearer {token}"}
    pid = (await client.post(f"/portal/invoices/{inv}/payments", headers=h)).json()["id"]
    r = await client.post(
        f"/portal/payments/{pid}/mock-simulate", headers=h, json={"result": "SUCCESS"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "PAID" and r.json()["idempotent"] is False
    pay = await _payment_row(session, pid)
    assert pay["status"] == "PAID" and str(pay["trade_no"]).startswith("mock-")
    assert pay["paid_at"] is not None
    inv_status = (
        await session.execute(
            text("SELECT status FROM public.sys_invoice WHERE id=CAST(:v AS uuid)"),
            {"v": inv},
        )
    ).scalar_one()
    assert inv_status == "PAID"
    assert await _oper_count(session, "payment.paid", pid) == 1


async def test_mock_simulate_replay_idempotent(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「重复回调幂等」:已 PAID 再 simulate→幂等收敛,审计不重复。"""
    _, token, _ = await _user_with_key(client, session, make_user, "pay_s2")
    tid = await _default_tenant_id(session)
    inv = await _mk_invoice(session, str(tid))
    h = {"Authorization": f"Bearer {token}"}
    pid = (await client.post(f"/portal/invoices/{inv}/payments", headers=h)).json()["id"]
    await client.post(
        f"/portal/payments/{pid}/mock-simulate", headers=h, json={"result": "SUCCESS"}
    )
    r2 = await client.post(
        f"/portal/payments/{pid}/mock-simulate", headers=h, json={"result": "SUCCESS"}
    )
    assert r2.status_code == 200 and r2.json()["idempotent"] is True
    assert await _oper_count(session, "payment.paid", pid) == 1


async def test_mock_simulate_failure_keeps_created(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「失败回调非终态」:FAILURE→保持 CREATED 可再成功。"""
    _, token, _ = await _user_with_key(client, session, make_user, "pay_s3")
    tid = await _default_tenant_id(session)
    inv = await _mk_invoice(session, str(tid))
    h = {"Authorization": f"Bearer {token}"}
    pid = (await client.post(f"/portal/invoices/{inv}/payments", headers=h)).json()["id"]
    rf = await client.post(
        f"/portal/payments/{pid}/mock-simulate", headers=h, json={"result": "FAILURE"}
    )
    assert rf.status_code == 200 and rf.json()["status"] == "CREATED"
    assert (await _payment_row(session, pid))["status"] == "CREATED"
    assert await _oper_count(session, "payment.failed", pid) == 1
    rs = await client.post(
        f"/portal/payments/{pid}/mock-simulate", headers=h, json={"result": "SUCCESS"}
    )
    assert rs.json()["status"] == "PAID"


# ── 核销(callback 入口) ──


async def test_callback_valid_signature(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「mock 通道签名验通」:无登录态,合法签名核销。"""
    _, token, _ = await _user_with_key(client, session, make_user, "pay_cb1")
    tid = await _default_tenant_id(session)
    inv = await _mk_invoice(session, str(tid))
    h = {"Authorization": f"Bearer {token}"}
    pid = (await client.post(f"/portal/invoices/{inv}/payments", headers=h)).json()["id"]
    r = await client.post(
        "/billing/payments/callback/MOCK",
        json={
            "payment_id": pid, "trade_no": "mock-cb0001", "result": "SUCCESS",
            "amount": 12345, "sign": _sign(pid, "mock-cb0001", "SUCCESS", 12345),
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "PAID"
    # 重放幂等
    r2 = await client.post(
        "/billing/payments/callback/MOCK",
        json={
            "payment_id": pid, "trade_no": "mock-cb0001", "result": "SUCCESS",
            "amount": 12345, "sign": _sign(pid, "mock-cb0001", "SUCCESS", 12345),
        },
    )
    assert r2.status_code == 200 and r2.json()["idempotent"] is True
    assert await _oper_count(session, "payment.paid", pid) == 1


async def test_callback_bad_signature_401(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「伪造签名拒绝」:401+审计,单据不变。"""
    _, token, _ = await _user_with_key(client, session, make_user, "pay_cb2")
    tid = await _default_tenant_id(session)
    inv = await _mk_invoice(session, str(tid))
    h = {"Authorization": f"Bearer {token}"}
    pid = (await client.post(f"/portal/invoices/{inv}/payments", headers=h)).json()["id"]
    r = await client.post(
        "/billing/payments/callback/MOCK",
        json={
            "payment_id": pid, "trade_no": "mock-evil", "result": "SUCCESS",
            "amount": 12345, "sign": "f" * 64,
        },
    )
    assert r.status_code == 401
    assert (await _payment_row(session, pid))["status"] == "CREATED"
    assert await _oper_count(session, "payment.bad_signature", pid) == 1


async def test_callback_amount_mismatch_400(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「金额不符拒绝」:合法签名+错金额→400+审计。"""
    _, token, _ = await _user_with_key(client, session, make_user, "pay_cb3")
    tid = await _default_tenant_id(session)
    inv = await _mk_invoice(session, str(tid))
    h = {"Authorization": f"Bearer {token}"}
    pid = (await client.post(f"/portal/invoices/{inv}/payments", headers=h)).json()["id"]
    r = await client.post(
        "/billing/payments/callback/MOCK",
        json={
            "payment_id": pid, "trade_no": "mock-am1", "result": "SUCCESS",
            "amount": 1, "sign": _sign(pid, "mock-am1", "SUCCESS", 1),
        },
    )
    assert r.status_code == 400
    assert (await _payment_row(session, pid))["status"] == "CREATED"
    assert await _oper_count(session, "payment.amount_mismatch", pid) == 1


async def test_callback_unknown_channel_404(client: AsyncClient) -> None:
    """spec「未注册通道 404」。"""
    r = await client.post(
        "/billing/payments/callback/WECHAT",
        json={
            "payment_id": "00000000-0000-0000-0000-000000000001",
            "trade_no": "x", "result": "SUCCESS", "amount": 1, "sign": "y",
        },
    )
    assert r.status_code == 404


# ── 取消/过期/作废 ──


async def test_cancel_payment_and_terminal_409(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「取消在途单」:CREATED→CANCELLED;终态再取消 409。"""
    _, token, _ = await _user_with_key(client, session, make_user, "pay_x1")
    tid = await _default_tenant_id(session)
    inv = await _mk_invoice(session, str(tid))
    h = {"Authorization": f"Bearer {token}"}
    pid = (await client.post(f"/portal/invoices/{inv}/payments", headers=h)).json()["id"]
    r = await client.post(f"/portal/payments/{pid}/cancel", headers=h)
    assert r.status_code == 200
    assert (await _payment_row(session, pid))["status"] == "CANCELLED"
    assert (await client.post(f"/portal/payments/{pid}/cancel", headers=h)).status_code == 409
    # 取消后同账单可重新发起
    r2 = await client.post(f"/portal/invoices/{inv}/payments", headers=h)
    assert r2.status_code == 201


async def test_lazy_expire_on_read_and_reconcile_noop(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「过期惰性置态」:过期单查询置 EXPIRED+审计;核销幂等收敛不生效。"""
    _, token, _ = await _user_with_key(client, session, make_user, "pay_x2")
    tid = await _default_tenant_id(session)
    inv = await _mk_invoice(session, str(tid))
    h = {"Authorization": f"Bearer {token}"}
    pid = (await client.post(f"/portal/invoices/{inv}/payments", headers=h)).json()["id"]
    await session.execute(
        text(
            "UPDATE public.sys_payment SET expires_at=now() - interval '1 minute'"
            " WHERE id=CAST(:i AS uuid)"
        ),
        {"i": pid},
    )
    await session.commit()
    r = await client.get(f"/portal/payments/{pid}", headers=h)
    assert r.json()["status"] == "EXPIRED"
    assert await _oper_count(session, "payment.expired", pid) == 1
    rs = await client.post(
        f"/portal/payments/{pid}/mock-simulate", headers=h, json={"result": "SUCCESS"}
    )
    assert rs.json()["status"] == "EXPIRED"
    assert (await _payment_row(session, pid))["status"] == "EXPIRED"


async def test_void_invoice_cascades_open_payment(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「作废待付账单」:VOID+在途单连带 CANCELLED;已 PAID 拒。"""
    from tests.test_rag_admin import _admin_token

    _, token, _ = await _user_with_key(client, session, make_user, "pay_v1")
    tid = await _default_tenant_id(session)
    inv = await _mk_invoice(session, str(tid))
    h = {"Authorization": f"Bearer {token}"}
    pid = (await client.post(f"/portal/invoices/{inv}/payments", headers=h)).json()["id"]
    admin = await _admin_token(client, session)
    r = await client.post(
        f"/billing/invoices/{inv}/void", headers={"Authorization": f"Bearer {admin}"}
    )
    assert r.status_code == 200, r.text
    assert (await _payment_row(session, pid))["status"] == "CANCELLED"
    inv_status = (
        await session.execute(
            text("SELECT status FROM public.sys_invoice WHERE id=CAST(:v AS uuid)"),
            {"v": inv},
        )
    ).scalar_one()
    assert inv_status == "VOID"
    # 已 PAID 拒作废
    inv2 = await _mk_invoice(session, str(tid), status="PAID", period="2026-04")
    r2 = await client.post(
        f"/billing/invoices/{inv2}/void", headers={"Authorization": f"Bearer {admin}"}
    )
    assert r2.status_code == 409


async def test_portal_payment_cross_tenant_404(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「跨租户不可见」:他租户支付单查询/取消/模拟均 404。"""
    _, token, _ = await _user_with_key(client, session, make_user, "pay_iso")
    other_tenant = "00000000-0000-0000-0000-000000000098"
    await session.execute(
        text(
            "INSERT INTO public.sys_tenant (id, tenant_key, tenant_name)"
            " VALUES (CAST(:t AS uuid), 'othert2', '他租户二')"
        ),
        {"t": other_tenant},
    )
    inv = await _mk_invoice(session, other_tenant)
    pid = str(
        (
            await session.execute(
                text(
                    "INSERT INTO public.sys_payment"
                    " (id, invoice_id, tenant_id, channel, amount, expires_at)"
                    " VALUES (gen_random_uuid(), CAST(:v AS uuid), CAST(:t AS uuid),"
                    "         'MOCK', 100, now() + interval '30 minutes')"
                    " RETURNING id"
                ),
                {"v": inv, "t": other_tenant},
            )
        ).scalar_one()
    )
    await session.commit()
    h = {"Authorization": f"Bearer {token}"}
    assert (await client.get(f"/portal/payments/{pid}", headers=h)).status_code == 404
    assert (await client.post(f"/portal/payments/{pid}/cancel", headers=h)).status_code == 404
    assert (
        await client.post(
            f"/portal/payments/{pid}/mock-simulate", headers=h,
            json={"result": "SUCCESS"},
        )
    ).status_code == 404


# ── mock 开关(服务级,通道表空) ──


async def test_mock_channel_closed() -> None:
    """spec「mock 通道关闭」:通道表为空时创建/核销均 PaymentChannelClosed。"""
    from kongming.billing.service import BillingService, PaymentChannelClosed

    class _Repo:  # 最小桩:不触库(通道判定在取数前)
        async def get_invoice(self, **kw: object) -> None:  # pragma: no cover
            return None

    svc = BillingService(cast("Any", _Repo()), channels={})
    try:
        await svc.create_payment(invoice_id="x", tenant_id="y")
        raise AssertionError("应抛 PaymentChannelClosed")
    except PaymentChannelClosed:
        pass


# ── R1-S1/A1:创建竞态与二次收款防线 ──


async def test_create_payment_race_returns_open_payment(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R1-A1:并发双击——条件插入被部分唯一索引拦(模拟返回 None)时,
    仍有在途单则幂等返回现有单(不新建/不重复审计),不甩 500。"""
    from kongming.billing.repository import SqlBillingRepo

    _, token, _ = await _user_with_key(client, session, make_user, "pay_r1")
    tid = await _default_tenant_id(session)
    inv = await _mk_invoice(session, str(tid))
    h = {"Authorization": f"Bearer {token}"}
    pid = (await client.post(f"/portal/invoices/{inv}/payments", headers=h)).json()["id"]

    async def _none(self: object, **kw: object) -> None:
        return None

    async def _zero(self: object, **kw: object) -> int:
        return 0  # 并发窗:T2 取消看不到 T1 未提交行(0 行命中)

    monkeypatch.setattr(SqlBillingRepo, "insert_payment_if_pending", _none)
    monkeypatch.setattr(SqlBillingRepo, "cancel_open_payments", _zero)
    r = await client.post(f"/portal/invoices/{inv}/payments", headers=h)
    assert r.status_code == 201, r.text
    assert r.json()["id"] == pid  # 同一在途单(双击幂等)
    cnt = int(
        (
            await session.execute(
                text(
                    "SELECT count(*) FROM public.sys_payment"
                    " WHERE invoice_id=CAST(:v AS uuid) AND status='CREATED'"
                ),
                {"v": inv},
            )
        ).scalar_one()
    )
    assert cnt == 1
    assert await _oper_count(session, "payment.create", pid) == 1  # 未重复审计


async def test_cond_insert_unique_violation_converges(
    client: AsyncClient, session: AsyncSession,
) -> None:
    """R1-A1(repo 级直证):PENDING 账单已有 CREATED 单时,不取消直接
    条件插入→唯一违例经 SAVEPOINT 收敛 None(会话不被污染可续用)。"""
    from kongming.auth.seed import seed_all
    from kongming.billing.repository import SqlBillingRepo
    from kongming.config import get_settings
    from kongming.db import get_sessionmaker

    await seed_all(session, get_settings())
    tid = await _default_tenant_id(session)
    inv = await _mk_invoice(session, str(tid))
    async with get_sessionmaker()() as s:
        repo = SqlBillingRepo(s)
        expires = __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ) + __import__("datetime").timedelta(minutes=30)
        rid1 = await repo.insert_payment_if_pending(
            invoice_id=inv, tenant_id=str(tid), channel="MOCK",
            amount=100, expires_at=expires,
        )
        assert rid1 is not None
        rid2 = await repo.insert_payment_if_pending(  # 撞 ux_sys_payment_invoice_open
            invoice_id=inv, tenant_id=str(tid), channel="MOCK",
            amount=100, expires_at=expires,
        )
        assert rid2 is None
        # 会话未被 IntegrityError 污染:后续语句照常
        got = await repo.get_payment(payment_id=rid1)
        assert got is not None
        await s.rollback()


async def test_reconcile_invoice_not_pending_alerts(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """R1-S1:极端并发(单已核销窗口账单被置 PAID)——CAS 照常成功,
    联动失败落 payment.invoice_not_pending 告警审计(不静默)。"""
    _, token, _ = await _user_with_key(client, session, make_user, "pay_r2")
    tid = await _default_tenant_id(session)
    inv = await _mk_invoice(session, str(tid))
    h = {"Authorization": f"Bearer {token}"}
    pid = (await client.post(f"/portal/invoices/{inv}/payments", headers=h)).json()["id"]
    # 模拟竞态窗:账单已被另一路径置 PAID(单仍在途)
    await session.execute(
        text(
            "UPDATE public.sys_invoice SET status='PAID' WHERE id=CAST(:v AS uuid)"
        ),
        {"v": inv},
    )
    await session.commit()
    r = await client.post(
        "/billing/payments/callback/MOCK",
        json={
            "payment_id": pid, "trade_no": "mock-race1", "result": "SUCCESS",
            "amount": 12345, "sign": _sign(pid, "mock-race1", "SUCCESS", 12345),
        },
    )
    assert r.status_code == 200 and r.json()["status"] == "PAID"
    assert await _oper_count(session, "payment.invoice_not_pending", pid) == 1
    assert await _oper_count(session, "payment.paid", pid) == 1
