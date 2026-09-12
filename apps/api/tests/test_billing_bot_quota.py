"""035 Bot 维度配额测试(测试先行,安全类):BOT 级超限 429 归因/Bot 级与
租户级独立/跨租户 scope_ref 404/同组合 upsert 唯一/零命中放行/CALLS
维 BOT 级拒配。"""
# pyright: reportPrivateUsage=false

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import MakeUser
from tests.test_billing import _insert_usage, _user_with_key
from tests.test_bot import BOT_PAYLOAD
from tests.test_chat_query import _default_tenant_id
from tests.test_rag_admin import _admin_token


async def _make_bot_and_key(client: AsyncClient, token: str, tag: str) -> tuple[str, str]:
    # bot_key 租户内唯一:_user_with_key 已建 BOT_PAYLOAD 原键,本 helper 加后缀
    payload = {**BOT_PAYLOAD, "bot_key": f"{BOT_PAYLOAD['bot_key']}_{tag}"}
    r = await client.post(
        "/system/bots", headers={"Authorization": f"Bearer {token}"}, json=payload
    )
    assert r.status_code == 201, r.text
    bot_id = r.json()["id"]
    r2 = await client.post(
        f"/system/bots/{bot_id}/keys?name=q",
        headers={"Authorization": f"Bearer {token}"},
    )
    return str(bot_id), str(r2.json()["api_key"])


async def _set_bot_policy(
    tenant_id: str, bot_id: str, dimension: str, limit: int, warn: int = 80
) -> None:
    from kongming.billing.repository import SqlBillingRepo
    from kongming.db import get_sessionmaker

    async with get_sessionmaker()() as s:
        repo = SqlBillingRepo(s)
        await repo.upsert_policy(
            tenant_id=tenant_id,
            dimension=dimension,
            limit_value=limit,
            warn_pct=warn,
            scope="BOT",
            scope_ref=bot_id,
        )
        await repo.commit()


async def _insert_bot_usage(s: AsyncSession, tenant_id: str, bot_id: str, tokens: int) -> None:
    await s.execute(
        text(
            "INSERT INTO public.sys_llm_usage"
            " (id, trace_id, tenant_id, bot_id, model, task, prompt_tokens,"
            "  completion_tokens, seq)"
            " VALUES (gen_random_uuid(), :tr, CAST(:t AS uuid), CAST(:b AS uuid),"
            "         'qwen-plus', 'REACT', :p, :c, 0)"
        ),
        {
            "tr": f"tr-{bot_id[:8]}-x",
            "t": tenant_id,
            "b": bot_id,
            "p": tokens // 2,
            "c": tokens - tokens // 2,
        },
    )
    await s.commit()


async def test_bot_quota_exceeded_429_attribution(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「Bot 级超限拒绝」:BOT 级 TOKENS 超限 → 429 带 BOT: 前缀归因。"""
    _, token, _ = await _user_with_key(client, session, make_user, "bq_e1")
    bot_id, api_key = await _make_bot_and_key(client, token, tag="e1")
    tid = await _default_tenant_id(session)
    await _insert_bot_usage(session, str(tid), bot_id, tokens=1000)
    await _set_bot_policy(str(tid), bot_id, "TOKENS", limit=500)
    r = await client.post(
        "/v1/chat/completions",
        headers={"X-Api-Key": api_key},
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 429
    assert "BOT:TOKENS" in r.json()["detail"]


async def test_bot_and_tenant_quota_independent(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「Bot 级与租户级独立」:Bot 级未超,租户级超 → 按租户级拒。"""
    _, token, _ = await _user_with_key(client, session, make_user, "bq_i1")
    bot_id, api_key = await _make_bot_and_key(client, token, tag="i1")
    tid = await _default_tenant_id(session)
    await _insert_bot_usage(session, str(tid), bot_id, tokens=100)
    await _insert_usage(session, str(tid), tokens=2000)  # 租户口径(无 bot_id)
    await _set_bot_policy(str(tid), bot_id, "TOKENS", limit=99999)
    from tests.test_billing import _set_policy

    await _set_policy(client, str(tid), "TOKENS", limit=500)  # 租户级 2000>500 超
    r = await client.post(
        "/v1/chat/completions",
        headers={"X-Api-Key": api_key},
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 429
    assert "BOT:" not in r.json()["detail"]  # 按租户级归因


async def test_bot_quota_zero_hit_pass(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「BOT 策略零命中放行」:策略存在但无该 bot 用量行 → 未超放行。"""
    _, token, _ = await _user_with_key(client, session, make_user, "bq_z1")
    bot_id, api_key = await _make_bot_and_key(client, token, tag="z1")
    tid = await _default_tenant_id(session)
    await _set_bot_policy(str(tid), bot_id, "TOKENS", limit=1)
    r = await client.post(
        "/v1/chat/completions",
        headers={"X-Api-Key": api_key},
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code != 429


async def test_bot_deleted_policy_zero_hit_401(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「Bot 被删除后其策略保留但零命中」(R1-A5):删除 Bot→api-key
    失效(JOIN 不上)→ 401 非 429,BOT 策略行残留不级联。"""
    _, token, _ = await _user_with_key(client, session, make_user, "bq_d1")
    bot_id, api_key = await _make_bot_and_key(client, token, tag="d1")
    tid = await _default_tenant_id(session)
    await _set_bot_policy(str(tid), bot_id, "TOKENS", limit=1)
    rd = await client.delete(f"/system/bots/{bot_id}", headers={"Authorization": f"Bearer {token}"})
    assert rd.status_code in (200, 204), rd.text
    r = await client.post(
        "/v1/chat/completions",
        headers={"X-Api-Key": api_key},
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 401
    # 策略残留不级联(配额策略是安全资产,静默删除不可取)
    cnt = int(
        (
            await session.execute(
                text(
                    "SELECT count(*) FROM public.sys_quota_policy"
                    " WHERE scope='BOT' AND scope_ref=CAST(:b AS uuid)"
                ),
                {"b": bot_id},
            )
        ).scalar_one()
    )
    assert cnt == 1


async def test_bot_policy_same_combo_upsert_unique(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「同 scope 组合唯一」:两次 upsert=一行(更新限额)。"""
    _, token, _ = await _user_with_key(client, session, make_user, "bq_u1")
    bot_id, _ = await _make_bot_and_key(client, token, tag="u1")
    tid = await _default_tenant_id(session)
    await _set_bot_policy(str(tid), bot_id, "TOKENS", limit=100)
    await _set_bot_policy(str(tid), bot_id, "TOKENS", limit=200)
    rows = (
        await session.execute(
            text(
                "SELECT limit_value FROM public.sys_quota_policy"
                " WHERE scope='BOT' AND scope_ref=CAST(:b AS uuid)"
            ),
            {"b": bot_id},
        )
    ).all()
    assert len(rows) == 1 and int(rows[0][0]) == 200


async def test_calls_dimension_bot_scope_rejected(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「CALLS 维度不支持 BOT 级」:配置 400。"""
    _, token, _ = await _user_with_key(client, session, make_user, "bq_c1")
    bot_id, _ = await _make_bot_and_key(client, token, tag="c1")
    admin = await _admin_token(client, session)
    tid = await _default_tenant_id(session)
    r = await client.post(
        "/billing/policies",
        headers={"Authorization": f"Bearer {admin}"},
        json={
            "tenant_id": str(tid),
            "dimension": "CALLS",
            "limit_value": 10,
            "scope": "BOT",
            "scope_ref": bot_id,
        },
    )
    assert r.status_code == 400


async def test_cross_tenant_scope_ref_404(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「跨租户 scope_ref 拒绝」:scope_ref 指向他租户 Bot → 404。"""
    _, token, _ = await _user_with_key(client, session, make_user, "bq_x1")
    bot_id, _ = await _make_bot_and_key(client, token, tag="x1")  # default 租户的 Bot
    admin = await _admin_token(client, session)
    other_tenant = "00000000-0000-0000-0000-000000000097"
    await session.execute(
        text(
            "INSERT INTO public.sys_tenant (id, tenant_key, tenant_name)"
            " VALUES (CAST(:t AS uuid), 'othert3', '他租户三')"
        ),
        {"t": other_tenant},
    )
    await session.commit()
    r = await client.post(
        "/billing/policies",
        headers={"Authorization": f"Bearer {admin}"},
        json={
            "tenant_id": other_tenant,
            "dimension": "TOKENS",
            "limit_value": 10,
            "scope": "BOT",
            "scope_ref": bot_id,  # Bot 属 default 非 othert3
        },
    )
    assert r.status_code == 404
