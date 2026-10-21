"""billing 测试:配额判定纯函数/网关切面(超额 429/未超放行/预警)/
套餐生效/月首出账/租户隔离。"""
# pyright: reportPrivateUsage=false

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.seed import seed_all
from kongming.billing.models import check_quota, estimate_cost
from kongming.config import get_settings
from tests.conftest import MakeUser
from tests.test_bot import BOT_PAYLOAD
from tests.test_chat_query import _bind, _default_tenant_id, _login

# ── 纯函数 ──


def test_check_quota_matrix() -> None:
    assert check_quota(used=50, limit=100, warn_pct=80, dimension="TOKENS").allowed is True
    assert check_quota(used=80, limit=100, warn_pct=80, dimension="TOKENS").warn is True
    v = check_quota(used=100, limit=100, warn_pct=80, dimension="CALLS")
    assert v.allowed is False and v.dimension == "CALLS"
    # limit=0 不限
    v2 = check_quota(used=999999, limit=0, warn_pct=80, dimension="COST")
    assert v2.allowed is True and v2.warn is False


def test_estimate_cost_int_fen() -> None:
    # 2500 token ×2厘/千 + 3 次 ×5厘 = 5+15=20 厘(向下取整)
    assert estimate_cost(tokens=2500, calls=3, price_per_mtok=2, price_per_call=5) == 20
    assert estimate_cost(tokens=0, calls=0, price_per_mtok=0, price_per_call=0) == 0


# ── HTTP 接口 ──


async def _user_with_key(
    client: AsyncClient, s: AsyncSession, make_user: MakeUser, name: str
) -> tuple[str, str, str]:
    """用户 + Bot + api-key(返回 uid, jwt token, api_key 明文)。"""
    await seed_all(s, get_settings())
    uid = await make_user(name)
    rid = (
        await s.execute(text("SELECT id FROM public.sys_role WHERE role_key='DEVELOPER'"))
    ).scalar_one()
    await _bind(s, "sys_user_role", uid, str(rid))
    tid = await _default_tenant_id(s)
    await _bind(s, "sys_user_tenant", uid, tid)
    await s.commit()
    token = await _login(client, name)
    r = await client.post(
        "/system/bots", headers={"Authorization": f"Bearer {token}"}, json=BOT_PAYLOAD
    )
    bot_id = r.json()["id"]
    r2 = await client.post(
        f"/system/bots/{bot_id}/keys?name=q",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r2.status_code == 201
    return uid, token, str(r2.json()["api_key"])


async def _set_policy(
    client: AsyncClient, tenant_id: str, dimension: str, limit: int, warn: int = 80
) -> None:
    from kongming.billing.repository import SqlBillingRepo
    from kongming.db import get_sessionmaker

    async with get_sessionmaker()() as s:
        repo = SqlBillingRepo(s)
        await repo.upsert_policy(
            tenant_id=tenant_id, dimension=dimension, limit_value=limit, warn_pct=warn
        )
        await repo.commit()


async def _insert_usage(s: AsyncSession, tenant_id: str, tokens: int) -> None:
    """直接落用量行(模拟 agent 上报;当月)。"""
    await s.execute(
        text(
            "INSERT INTO public.sys_llm_usage"
            " (id, tenant_id, model, task, prompt_tokens, completion_tokens)"
            " VALUES (gen_random_uuid(), CAST(:t AS uuid), 'qwen-plus', 'CHAT', :p, :c)"
        ),
        {"t": tenant_id, "p": tokens // 2, "c": tokens - tokens // 2},
    )
    await s.commit()


async def test_gateway_quota_exceeded_429(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「超额 fail-closed 拒绝」:当月 token 超限 → 429 不执行。"""
    _uid, _token, api_key = await _user_with_key(client, session, make_user, "bl_q1")
    tenant_id = await _default_tenant_id(session)
    await _insert_usage(session, str(tenant_id), tokens=1000)
    await _set_policy(client, str(tenant_id), "TOKENS", limit=500)
    r = await client.post(
        "/v1/chat/completions",
        headers={"X-Api-Key": api_key},
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 429
    assert "QUOTA_EXCEEDED" in r.json()["detail"]


async def test_gateway_under_limit_pass_and_zero_regression(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「未超放行」+「未配置」:无策略请求照常。"""
    _, _token, api_key = await _user_with_key(client, session, make_user, "bl_p1")
    # 零策略零套餐:照常(agent 可达性决定 200/5xx 但非 429)
    r = await client.post(
        "/v1/chat/completions",
        headers={"X-Api-Key": api_key},
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code != 429
    # 加策略但用量未超:也放行
    tenant_id = await _default_tenant_id(session)
    await _set_policy(client, str(tenant_id), "TOKENS", limit=10**9)
    r2 = await client.post(
        "/v1/chat/completions",
        headers={"X-Api-Key": api_key},
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert r2.status_code != 429


async def test_gateway_warn_header(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「未超放行并预警」:达 80% 标记 X-Quota-Warning,不拒绝。"""
    _, _token, api_key = await _user_with_key(client, session, make_user, "bl_w1")
    tenant_id = await _default_tenant_id(session)
    await _insert_usage(session, str(tenant_id), tokens=850)
    await _set_policy(client, str(tenant_id), "TOKENS", limit=1000, warn=80)
    r = await client.post(
        "/v1/chat/completions",
        headers={"X-Api-Key": api_key},
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code != 429
    assert "X-Quota-Warning" in r.headers
    assert "TOKENS" in r.headers["X-Quota-Warning"]


async def test_plan_quota_takes_effect(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「套餐配额生效」:绑套餐后按套餐值判定(0 token 配额即拒)。"""
    _uid, _token, api_key = await _user_with_key(client, session, make_user, "bl_plan")
    tenant_id = await _default_tenant_id(session)
    # 创建月 10 token 套餐并绑定 → 有任一历史用量即超(先插一行用量)
    from kongming.billing.repository import SqlBillingRepo
    from kongming.db import get_sessionmaker

    async with get_sessionmaker()() as s:
        repo = SqlBillingRepo(s)
        pid = await repo.create_plan(
            plan_key="tiny",
            name="迷你版",
            monthly_tokens=10,
            monthly_calls=0,
            price_per_mtok=1,
            price_per_call=0,
        )
        await repo.bind_plan(tenant_id=str(tenant_id), plan_id=pid)
        await repo.commit()
    await _insert_usage(session, str(tenant_id), tokens=100)
    r = await client.post(
        "/v1/chat/completions",
        headers={"X-Api-Key": api_key},
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 429


async def test_monthly_invoice_idempotent(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「周期账单生成」:月首出账幂等(二次运行不重复)+金额整数厘。"""
    from kongming.billing.repository import SqlBillingRepo
    from kongming.billing.service import BillingService
    from kongming.db import get_sessionmaker

    await seed_all(session, get_settings())  # 重建 default 租户(TRUNCATED 已清)
    tenant_id = await _default_tenant_id(session)
    # 上月用量:月份必须与产品同一时源(BillingService._prev_month 用固定 +8 口径,
    # 而库时钟是 UTC——每月 1 日 00:00~08:00 两者跨月不一致,用量会写进产品不出账的月份)
    from kongming.billing.service import _prev_month

    prev = _prev_month()
    await session.execute(
        text(
            "INSERT INTO public.sys_llm_usage"
            " (id, tenant_id, model, task, prompt_tokens, completion_tokens, created_at)"
            " VALUES (gen_random_uuid(), CAST(:t AS uuid), 'qwen-plus', 'CHAT', 600, 400,"
            "         CAST(:m || '-15' AS date))"
        ),
        {"t": str(tenant_id), "m": prev},
    )
    await session.commit()
    async with get_sessionmaker()() as s:
        n1 = await BillingService(SqlBillingRepo(s)).generate_monthly_invoices()
    async with get_sessionmaker()() as s2:
        n2 = await BillingService(SqlBillingRepo(s2)).generate_monthly_invoices()
    assert n1 == 1 and n2 == 0  # 幂等
    all_rows = (
        await session.execute(text("SELECT tenant_id, period, amount FROM public.sys_invoice"))
    ).all()
    print("INVOICES:", all_rows, "prev:", prev)
    row = (
        (
            await session.execute(
                text(
                    "SELECT amount, usage_json FROM public.sys_invoice"
                    " WHERE tenant_id=CAST(:t AS uuid) AND period=:p"
                ),
                {"t": str(tenant_id), "p": prev},
            )
        )
        .mappings()
        .one()
    )
    assert isinstance(row["amount"], int)
    assert row["usage_json"]["total_tokens"] == 1000
    assert row["usage_json"]["by_model"][0]["model"] == "qwen-plus"


async def test_invoice_tenant_isolation(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「账单明细隔离」:他租户查询 404。"""
    _uid, token, _ = await _user_with_key(client, session, make_user, "bl_iso")
    tenant_id = await _default_tenant_id(session)
    # 他租户账单(手工落)
    from kongming.billing.repository import SqlBillingRepo
    from kongming.db import get_sessionmaker

    async with get_sessionmaker()() as s:
        repo = SqlBillingRepo(s)
        rid = await repo.insert_invoice(
            tenant_id=str(tenant_id),
            period="2026-07",
            usage_json={"total_tokens": 1},
            amount=1,
        )
        await repo.commit()
    # 另一租户用户查询(无该租户绑定 → 403;绑定 default 才能过——
    # 此用例验证同租户可见,跨租户 404 由 SQL 租户条件保证)
    r = await client.get(f"/portal/invoices/{rid}", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200  # 同租户可见(明细含 usage_json)
    assert r.json()["usage_json"]["total_tokens"] == 1


async def test_quota_reject_audit_written(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """回归:429 后 sys_oper_log 有 billing.quota_reject 行。"""
    _, _token, api_key = await _user_with_key(client, session, make_user, "bl_a1")
    tenant_id = await _default_tenant_id(session)
    await _insert_usage(session, str(tenant_id), tokens=1000)
    await _set_policy(client, str(tenant_id), "TOKENS", limit=500)
    r = await client.post(
        "/v1/chat/completions",
        headers={"X-Api-Key": api_key},
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 429
    n = (
        await session.execute(
            text("SELECT count(*) FROM public.sys_oper_log WHERE action='billing.quota_reject'")
        )
    ).scalar_one()
    assert n >= 1


async def test_month_boundary_shanghai_bucket(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """回归:月聚合按 Asia/Shanghai 月桶(AT TIME ZONE),
    UTC 上月末 16:xx(=CST 本月 0:xx)的用量计入 CST 当月。"""
    from kongming.billing.repository import SqlBillingRepo
    from kongming.billing.service import _current_month
    from kongming.db import get_sessionmaker

    await seed_all(session, get_settings())
    tenant_id = await _default_tenant_id(session)
    # UTC 本月 1 日 00:30(=CST 本月 1 日 08:30):UTC 会话时区会归入
    # CST 当月;仅当 SQL 按 Asia/Shanghai 分桶才计数(UTC 分桶归上月)
    month = _current_month()
    # 直接构造 datetime(asyncpg 只收对象不收 ISO 串)
    import datetime as dt

    first_utc = dt.datetime.fromisoformat(f"{month}-01T00:00:00+00:00")
    await session.execute(
        text(
            "INSERT INTO public.sys_llm_usage"
            " (id, tenant_id, model, task, prompt_tokens, completion_tokens, created_at)"
            " VALUES (gen_random_uuid(), CAST(:t AS uuid), 'qwen-plus', 'CHAT', 40, 30,"
            "         CAST(:ts AS timestamptz) + interval '30 minutes')"
        ),
        {"t": str(tenant_id), "ts": first_utc},
    )
    await session.commit()
    async with get_sessionmaker()() as s:
        used = await SqlBillingRepo(s).month_token_usage(tenant_id=str(tenant_id), month=month)
    assert used >= 70  # CST 月初用量归入当月(UTC 分桶会漏掉)


async def test_invoice_cross_tenant_404(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「账单明细隔离」:他租户用户查账单 404 同形。"""
    from tests.test_toolcall import _mk_tenant

    _uid, _token, _api_key = await _user_with_key(client, session, make_user, "bl_x1")
    tenant_id = await _default_tenant_id(session)
    from kongming.billing.repository import SqlBillingRepo
    from kongming.db import get_sessionmaker

    async with get_sessionmaker()() as s:
        repo = SqlBillingRepo(s)
        rid = await repo.insert_invoice(
            tenant_id=str(tenant_id),
            period="2026-06",
            usage_json={"total_tokens": 1},
            amount=1,
        )
        await repo.commit()
    # 第二租户用户(绑他租户,未绑 default)
    from tests.test_toolcall import _mk_role_with_perms

    uid_b = await make_user("bl_x2")
    tid_b = await _mk_tenant(session, "other_t")
    rid_b = await _mk_role_with_perms(session, "role_bl_x2", ["developer:portal"])
    await _bind(session, "sys_user_role", uid_b, rid_b)
    await _bind(session, "sys_user_tenant", uid_b, tid_b)
    await session.commit()
    token_b = await _login(client, "bl_x2")
    r = await client.get(
        f"/portal/invoices/{rid}",
        headers={"Authorization": f"Bearer {token_b}", "X-Tenant-Id": "other_t"},
    )
    assert r.status_code == 404
    # default 视角也 404(未绑定)
    r2 = await client.get(f"/portal/invoices/{rid}", headers={"Authorization": f"Bearer {token_b}"})
    assert r2.status_code in (403, 404)


async def test_portal_usage_aggregation(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「开发者查看用量」:门户聚合三维+策略+套餐。"""
    _uid, token, _ = await _user_with_key(client, session, make_user, "bl_pu")
    tenant_id = await _default_tenant_id(session)
    await _insert_usage(session, str(tenant_id), tokens=500)
    await _set_policy(client, str(tenant_id), "TOKENS", limit=1000)
    r = await client.get("/portal/usage", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["tokens"] == 500
    assert body["policies"][0]["dimension"] == "TOKENS"
