"""103 配额 scope 扩展 USER/TEAM 测试(测试先行,安全类):
迁移 0046(CHECK 扩展/降级守卫/往返)/策略 CRUD 三分支校验/清单端点
单授可取/网关判定多路叠加(TENANT→BOT→USER→TEAM)/TEAM 聚合联表
三场景(聚合/换队跟随/删除零命中)/CALLS 仅 TENANT。"""
# pyright: reportPrivateUsage=false

import asyncio
import uuid as uuid_mod

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import MakeUser, migrated_tmp_db
from tests.test_billing import _insert_usage, _user_with_key
from tests.test_bot import BOT_PAYLOAD
from tests.test_chat_query import _bind, _default_tenant_id, _login
from tests.test_rag_admin import _admin_token
from tests.test_toolcall import _mk_role_with_perms, _mk_tenant


async def _ins_policy(
    session: AsyncSession,
    *,
    tenant_id: str,
    scope: str,
    scope_ref: str | None,
    dimension: str = "TOKENS",
    limit: int = 100,
) -> None:
    """直插策略行(迁移契约测试用,绕过服务层校验)。"""
    await session.execute(
        text(
            "INSERT INTO public.sys_quota_policy"
            " (id, tenant_id, dimension, limit_value, warn_pct, scope, scope_ref)"
            " VALUES (gen_random_uuid(), CAST(:t AS uuid), :d, :l, 80, :s,"
            "         CAST(:sr AS uuid))"
        ),
        {"t": tenant_id, "d": dimension, "l": limit, "s": scope, "sr": scope_ref},
    )
    await session.commit()


async def _db_version(session: AsyncSession) -> str:
    v = await session.execute(text("SELECT version_num FROM alembic_version"))
    return str(v.scalar_one())


# ── 1.1 迁移 0046 ──


async def test_migration_0046_scope_check_and_guard() -> None:
    """CHECK 扩展契约:USER/TEAM 行可落(scope_ref 非空)、域外/空引用拒;
    downgrade 存量守卫:存在 USER 行时拒降,版本不动。"""
    async with migrated_tmp_db() as (session, cfg):
        tid = await _mk_tenant(session, "qt_mig1")
        await _ins_policy(
            session, tenant_id=str(tid), scope="USER", scope_ref=str(uuid_mod.uuid4())
        )
        await _ins_policy(
            session, tenant_id=str(tid), scope="TEAM", scope_ref=str(uuid_mod.uuid4())
        )
        # 域外 scope 拒
        with pytest.raises(Exception):  # noqa: B017,S101  # CHECK 违约即抛(DBAPIError)
            await _ins_policy(session, tenant_id=str(tid), scope="GROUP", scope_ref=None)
        await session.rollback()
        # USER/TEAM 行 scope_ref 须非空(互斥 CHECK 与 BOT 同形态)
        with pytest.raises(Exception):  # noqa: B017,S101
            await _ins_policy(session, tenant_id=str(tid), scope="USER", scope_ref=None)
        await session.rollback()
        # 存量守卫:有 USER 行,降级被拒且版本停在 0046
        # (head 高于 0046 时先降到 0046 再尝试继续降——守卫语义只针对 0046→0045 一步)
        from alembic import command

        await session.rollback()
        await asyncio.to_thread(command.downgrade, cfg, "0046")
        with pytest.raises(Exception):  # noqa: B017,S101  # 守卫 RuntimeError
            await asyncio.to_thread(command.downgrade, cfg, "0045")
        assert await _db_version(session) == "0046"
        # 必须回到 head:停旧版会让其后用例 TRUNCATE 撞不存在的表
        await session.rollback()
        await asyncio.to_thread(command.upgrade, cfg, "head")


async def test_migration_0046_downgrade_roundtrip() -> None:
    """降级往返:无 USER/TEAM 行时降级成功、存量 TENANT/BOT 行零影响、
    降级后 USER 行被收窄 CHECK 拒;再升级契约复现。"""
    async with migrated_tmp_db() as (session, cfg):
        from alembic import command

        tid = await _mk_tenant(session, "qt_mig2")
        # 存量形态:TENANT(NULL 引用)+ BOT 行
        await _ins_policy(session, tenant_id=str(tid), scope="TENANT", scope_ref=None)
        await _ins_policy(session, tenant_id=str(tid), scope="BOT", scope_ref=str(uuid_mod.uuid4()))
        # 会话连接的 autobegin 事务会持 sys_quota_policy 的 ACCESS SHARE,
        # 不先结束会让 alembic 的 ALTER(ACCESS EXCLUSIVE)死等——
        # 每次 alembic 命令前一律 rollback 清场
        await session.rollback()
        await asyncio.to_thread(command.downgrade, cfg, "0045")
        # 收窄后 USER 行拒(TENANT/BOT 存量行零影响)
        with pytest.raises(Exception):  # noqa: B017,S101
            await _ins_policy(
                session, tenant_id=str(tid), scope="USER", scope_ref=str(uuid_mod.uuid4())
            )
        await session.rollback()
        n = int(
            (
                await session.execute(
                    text(
                        "SELECT count(*) FROM public.sys_quota_policy"
                        " WHERE scope IN ('TENANT','BOT')"
                    )
                )
            ).scalar_one()
        )
        assert n == 2
        # 回 head:契约复现,USER 行可再落(rollback 释放 count 查询的事务锁)
        # head 与断言解耦(后续变更继续叠加迁移,不钉死具体版本号)
        await session.rollback()
        await asyncio.to_thread(command.upgrade, cfg, "head")
        from alembic.script import ScriptDirectory

        head = ScriptDirectory.from_config(cfg).get_current_head()
        assert await _db_version(session) == head
        await _ins_policy(
            session, tenant_id=str(tid), scope="USER", scope_ref=str(uuid_mod.uuid4())
        )


# ── 1.2 策略 CRUD scope 三分支 ──


async def _mk_team(session: AsyncSession, tenant_id: str, key: str) -> str:
    tid = (
        await session.execute(
            text(
                "INSERT INTO public.sys_team (id, tenant_id, team_key, name, created_by)"
                " VALUES (gen_random_uuid(), CAST(:t AS uuid), :k, :k, gen_random_uuid())"
                " RETURNING id"
            ),
            {"t": tenant_id, "k": key},
        )
    ).scalar_one()
    await session.commit()
    return str(tid)


async def test_user_policy_create_and_upsert_unique(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「USER 级绑定本租户用户」+「同 scope 组合唯一」:201 后重复
    upsert=更新限额不重复落行。"""
    uid, _token, _ = await _user_with_key(client, session, make_user, "qt_u1")
    admin = await _admin_token(client, session)
    tid = await _default_tenant_id(session)
    body = {
        "tenant_id": str(tid),
        "dimension": "TOKENS",
        "limit_value": 500,
        "scope": "USER",
        "scope_ref": str(uid),
    }
    r = await client.post(
        "/billing/policies", headers={"Authorization": f"Bearer {admin}"}, json=body
    )
    assert r.status_code == 201, r.text
    body["limit_value"] = 800
    r2 = await client.post(
        "/billing/policies", headers={"Authorization": f"Bearer {admin}"}, json=body
    )
    assert r2.status_code == 201
    rows = (
        await session.execute(
            text(
                "SELECT limit_value FROM public.sys_quota_policy"
                " WHERE scope='USER' AND scope_ref=CAST(:u AS uuid)"
            ),
            {"u": str(uid)},
        )
    ).all()
    assert len(rows) == 1 and int(rows[0][0]) == 800


async def test_user_policy_cross_tenant_and_missing_404(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「跨租户 scope_ref 拒绝」(USER 级):他租户用户 404;不存在 404。"""
    uid, _token, _ = await _user_with_key(client, session, make_user, "qt_x1")
    admin = await _admin_token(client, session)
    other = await _mk_tenant(session, "other_qt1")
    # 他租户用户
    r = await client.post(
        "/billing/policies",
        headers={"Authorization": f"Bearer {admin}"},
        json={
            "tenant_id": other,
            "dimension": "TOKENS",
            "limit_value": 10,
            "scope": "USER",
            "scope_ref": str(uid),
        },
    )
    assert r.status_code == 404
    # 不存在的用户
    r2 = await client.post(
        "/billing/policies",
        headers={"Authorization": f"Bearer {admin}"},
        json={
            "tenant_id": other,
            "dimension": "TOKENS",
            "limit_value": 10,
            "scope": "USER",
            "scope_ref": str(uuid_mod.uuid4()),
        },
    )
    assert r2.status_code == 404
    # 缺 scope_ref 400
    r3 = await client.post(
        "/billing/policies",
        headers={"Authorization": f"Bearer {admin}"},
        json={
            "tenant_id": other,
            "dimension": "TOKENS",
            "limit_value": 10,
            "scope": "USER",
        },
    )
    assert r3.status_code == 400


async def test_team_policy_create_and_cross_tenant_404(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """TEAM 级:本租户团队 201;他租户团队 404;不存在 404。"""
    _uid, _token, _ = await _user_with_key(client, session, make_user, "qt_t1")
    admin = await _admin_token(client, session)
    tid = await _default_tenant_id(session)
    team_id = await _mk_team(session, str(tid), "qt_team1")
    r = await client.post(
        "/billing/policies",
        headers={"Authorization": f"Bearer {admin}"},
        json={
            "tenant_id": str(tid),
            "dimension": "TOKENS",
            "limit_value": 300,
            "scope": "TEAM",
            "scope_ref": team_id,
        },
    )
    assert r.status_code == 201, r.text
    other = await _mk_tenant(session, "other_qt2")
    r2 = await client.post(
        "/billing/policies",
        headers={"Authorization": f"Bearer {admin}"},
        json={
            "tenant_id": other,
            "dimension": "TOKENS",
            "limit_value": 10,
            "scope": "TEAM",
            "scope_ref": team_id,  # 团队属 default 非 other
        },
    )
    assert r2.status_code == 404
    r3 = await client.post(
        "/billing/policies",
        headers={"Authorization": f"Bearer {admin}"},
        json={
            "tenant_id": other,
            "dimension": "TOKENS",
            "limit_value": 10,
            "scope": "TEAM",
            "scope_ref": str(uuid_mod.uuid4()),
        },
    )
    assert r3.status_code == 404


# ── 1.3 清单端点(单授 billing:manage) ──


async def _billing_only_token(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> str:
    """仅授 system:billing:manage 的用户(不授 user:manage/team:manage)。"""
    uid = await make_user("qt_bill")
    tid = await _default_tenant_id(session)
    rid = await _mk_role_with_perms(session, "role_qt_bill", ["system:billing:manage"])
    await _bind(session, "sys_user_role", str(uid), rid)
    await _bind(session, "sys_user_tenant", str(uid), tid)
    await session.commit()
    return await _login(client, "qt_bill")


async def test_billing_users_teams_brief_billing_manage_only(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec(治理口径):单授 billing:manage 可取 USER/TEAM 选择器清单,
    不依赖 user:manage/team:manage;未授权 403。"""
    _uid, token, _ = await _user_with_key(client, session, make_user, "qt_l1")
    tid = await _default_tenant_id(session)
    await _mk_team(session, str(tid), "qt_team_l1")
    single = await _billing_only_token(client, session, make_user)
    ru = await client.get(
        f"/billing/users?tenant_id={tid}", headers={"Authorization": f"Bearer {single}"}
    )
    assert ru.status_code == 200, ru.text
    users = ru.json()
    assert any(u["id"] == _uid for u in users)
    rt = await client.get(
        f"/billing/teams?tenant_id={tid}", headers={"Authorization": f"Bearer {single}"}
    )
    assert rt.status_code == 200, rt.text
    assert any(t["team_key"] == "qt_team_l1" for t in rt.json())
    # 无 billing:manage 的门户用户 403
    r403 = await client.get(
        f"/billing/users?tenant_id={tid}", headers={"Authorization": f"Bearer {token}"}
    )
    assert r403.status_code == 403


# ── 2.1/2.2/2.3 网关判定多路化 ──


async def _bot_with_key(
    client: AsyncClient, token: str, tag: str, *, team_id: str | None = None
) -> tuple[str, str]:
    """建 Bot(可指定 team 归属)+ api-key,返回 (bot_id, api_key 明文)。"""
    payload = {**BOT_PAYLOAD, "bot_key": f"{BOT_PAYLOAD['bot_key']}_{tag}"}
    r = await client.post(
        "/system/bots", headers={"Authorization": f"Bearer {token}"}, json=payload
    )
    assert r.status_code == 201, r.text
    bot_id = str(r.json()["id"])
    if team_id is not None:
        from sqlalchemy import text as _text

        from kongming.db import get_sessionmaker

        async with get_sessionmaker()() as s:
            await s.execute(
                _text(
                    "UPDATE public.sys_bot_definition SET team_id=CAST(:tm AS uuid)"
                    " WHERE id=CAST(:b AS uuid)"
                ),
                {"tm": team_id, "b": bot_id},
            )
            await s.commit()
    r2 = await client.post(
        f"/system/bots/{bot_id}/keys?name=q", headers={"Authorization": f"Bearer {token}"}
    )
    assert r2.status_code == 201
    return bot_id, str(r2.json()["api_key"])


async def _set_scope_policy(
    tenant_id: str,
    scope: str,
    scope_ref: str | None,
    dimension: str,
    limit: int,
    warn: int = 80,
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
            scope=scope,
            scope_ref=scope_ref,
        )
        await repo.commit()


async def _post_chat(client: AsyncClient, api_key: str) -> Response:
    return await client.post(
        "/v1/chat/completions",
        headers={"X-Api-Key": api_key},
        json={"messages": [{"role": "user", "content": "hi"}]},
    )


async def _insert_bot_usage_by_id(
    session: AsyncSession, tenant_id: str, bot_id: str, tokens: int
) -> None:
    """按 bot_id 落用量行(归属=Bot 同步采集口径;当月;trace_id 随机防
    (trace_id,seq) 唯一索引冲突)。"""
    await session.execute(
        text(
            "INSERT INTO public.sys_llm_usage"
            " (id, trace_id, tenant_id, bot_id, model, task, prompt_tokens,"
            "  completion_tokens, seq)"
            " VALUES (gen_random_uuid(), :tr, CAST(:t AS uuid), CAST(:b AS uuid),"
            "         'qwen-plus', 'REACT', :p, :c, 0)"
        ),
        {
            "tr": f"tr-{uuid_mod.uuid4().hex[:16]}",
            "t": tenant_id,
            "b": bot_id,
            "p": tokens // 2,
            "c": tokens - tokens // 2,
        },
    )
    await session.commit()


async def test_user_quota_exceeded_429(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「USER 级超限拒绝」:用户当月(其名下 Bot 用量合计)达限 →
    429 QUOTA_EXCEEDED: USER:TOKENS。"""
    uid, token, _ = await _user_with_key(client, session, make_user, "qt_g1")
    tid = await _default_tenant_id(session)
    bot_id, api_key = await _bot_with_key(client, token, "g1")
    await _insert_bot_usage_by_id(session, str(tid), bot_id, tokens=1000)
    await _set_scope_policy(str(tid), "USER", str(uid), "TOKENS", limit=500)
    r = await _post_chat(client, api_key)
    assert r.status_code == 429
    assert "USER:TOKENS" in r.json()["detail"]


async def test_team_quota_exceeded_429(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「TEAM 级按资源归属聚合」:团队名下 Bot 用量达限 → 429 带
    TEAM:TOKENS 前缀。"""
    _uid, token, _ = await _user_with_key(client, session, make_user, "qt_g2")
    tid = await _default_tenant_id(session)
    team_id = await _mk_team(session, str(tid), "qt_g2_team")
    bot_id, api_key = await _bot_with_key(client, token, "g2", team_id=team_id)
    await _insert_bot_usage_by_id(session, str(tid), bot_id, tokens=1000)
    await _set_scope_policy(str(tid), "TEAM", team_id, "TOKENS", limit=500)
    r = await _post_chat(client, api_key)
    assert r.status_code == 429
    assert "TEAM:TOKENS" in r.json()["detail"]


async def test_tenant_before_user_when_both_exceeded(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """判定顺序(TENANT→BOT→USER→TEAM):租户与 USER 两路同超 → 按租户级
    归因(无前缀文案先命中)。"""
    uid, token, _ = await _user_with_key(client, session, make_user, "qt_g3")
    tid = await _default_tenant_id(session)
    bot_id, api_key = await _bot_with_key(client, token, "g3")
    await _insert_bot_usage_by_id(session, str(tid), bot_id, tokens=1000)  # Bot 归属
    await _insert_usage(session, str(tid), tokens=1000)  # 租户口径
    await _set_scope_policy(str(tid), "TENANT", None, "TOKENS", limit=500)
    await _set_scope_policy(str(tid), "USER", str(uid), "TOKENS", limit=500)
    r = await _post_chat(client, api_key)
    assert r.status_code == 429
    detail = r.json()["detail"]
    assert "USER:" not in detail and "BOT:" not in detail


async def test_bot_before_team_when_both_exceeded(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """判定顺序:BOT 与 TEAM 两路同超 → 先命中 BOT(拒绝归因取先命中超限路)。"""
    _uid, token, _ = await _user_with_key(client, session, make_user, "qt_g4")
    tid = await _default_tenant_id(session)
    team_id = await _mk_team(session, str(tid), "qt_g4_team")
    bot_id, api_key = await _bot_with_key(client, token, "g4", team_id=team_id)
    await _insert_bot_usage_by_id(session, str(tid), bot_id, tokens=1000)
    await _set_scope_policy(str(tid), "BOT", bot_id, "TOKENS", limit=500)
    await _set_scope_policy(str(tid), "TEAM", team_id, "TOKENS", limit=500)
    r = await _post_chat(client, api_key)
    assert r.status_code == 429
    assert "BOT:TOKENS" in r.json()["detail"]


async def test_user_before_team_when_both_exceeded(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """判定顺序:USER 与 TEAM 两路同超 → 先命中 USER。"""
    uid, token, _ = await _user_with_key(client, session, make_user, "qt_g5")
    tid = await _default_tenant_id(session)
    team_id = await _mk_team(session, str(tid), "qt_g5_team")
    bot_id, api_key = await _bot_with_key(client, token, "g5", team_id=team_id)
    await _insert_bot_usage_by_id(session, str(tid), bot_id, tokens=1000)
    await _set_scope_policy(str(tid), "USER", str(uid), "TOKENS", limit=500)
    await _set_scope_policy(str(tid), "TEAM", team_id, "TOKENS", limit=500)
    r = await _post_chat(client, api_key)
    assert r.status_code == 429
    assert "USER:TOKENS" in r.json()["detail"]


async def test_user_policy_zero_hit_pass(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """USER 级策略仅命中「调用 Bot 归属该用户」:他属 Bot 的调用零命中放行。"""
    uid_a, _token_a, _ = await _user_with_key(client, session, make_user, "qt_g6a")
    # 用户 B(bot_key 全局唯一,自建 Bot 走带后缀 helper)
    uid_b = await make_user("qt_g6b")
    rid = (
        await session.execute(text("SELECT id FROM public.sys_role WHERE role_key='DEVELOPER'"))
    ).scalar_one()
    tid = await _default_tenant_id(session)
    await _bind(session, "sys_user_role", uid_b, str(rid))
    await _bind(session, "sys_user_tenant", uid_b, tid)
    await session.commit()
    token_b = await _login(client, "qt_g6b")
    _bot_b, api_key_b = await _bot_with_key(client, token_b, "g6b")
    # A 的 USER 级策略;B 的 Bot(B 归属)调用 → 不命中 A 的策略
    assert uid_a != uid_b
    await _set_scope_policy(str(tid), "USER", str(uid_a), "TOKENS", limit=1)
    r = await _post_chat(client, api_key_b)
    assert r.status_code != 429


# ── 2.2 TEAM 聚合联表三场景 ──


async def test_team_quota_two_bots_aggregated(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「TEAM 级按资源归属聚合」:T 名下 Bot X 与 Y 各耗部分,合计达限 →
    T 名下任一 Bot 的下一次调用被 429 TEAM:TOKENS 拒绝。"""
    _uid, token, _ = await _user_with_key(client, session, make_user, "qt_m1")
    tid = await _default_tenant_id(session)
    team_id = await _mk_team(session, str(tid), "qt_m1_team")
    bot_x, api_key_x = await _bot_with_key(client, token, "m1x", team_id=team_id)
    bot_y, _ = await _bot_with_key(client, token, "m1y", team_id=team_id)
    await _insert_bot_usage_by_id(session, str(tid), bot_x, tokens=300)
    await _insert_bot_usage_by_id(session, str(tid), bot_y, tokens=300)
    await _set_scope_policy(str(tid), "TEAM", team_id, "TOKENS", limit=500)
    r = await _post_chat(client, api_key_x)
    assert r.status_code == 429
    assert "TEAM:TOKENS" in r.json()["detail"]


async def test_team_quota_follows_current_owner(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「资源换队跟随现归属」:Bot X 当月用量 N,月中从 T 移至 R →
    N 计入 R(现归属)不再计入 T(TEAM T 策略零命中,TEAM R 策略命中)。"""
    _uid, token, _ = await _user_with_key(client, session, make_user, "qt_m2")
    tid = await _default_tenant_id(session)
    team_t = await _mk_team(session, str(tid), "qt_m2_t")
    team_r = await _mk_team(session, str(tid), "qt_m2_r")
    bot_x, api_key_x = await _bot_with_key(client, token, "m2x", team_id=team_t)
    await _insert_bot_usage_by_id(session, str(tid), bot_x, tokens=1000)
    # 月中换队:归 T 的旧策略零命中(用量不再计入 T)
    await _set_scope_policy(str(tid), "TEAM", team_t, "TOKENS", limit=500)
    import uuid as _uuid

    from sqlalchemy import text as _text

    from kongming.db import get_sessionmaker

    async with get_sessionmaker()() as s:
        await s.execute(
            _text(
                "UPDATE public.sys_bot_definition SET team_id=CAST(:r AS uuid)"
                " WHERE id=CAST(:b AS uuid)"
            ),
            {"r": team_r, "b": bot_x},
        )
        await s.commit()
    r1 = await _post_chat(client, api_key_x)
    assert r1.status_code != 429  # T 级不再限 Bot X(跟随现归属)
    # R 级策略命中:N 计入 R → 超
    await _set_scope_policy(str(tid), "TEAM", team_r, "TOKENS", limit=500)
    r2 = await _post_chat(client, api_key_x)
    assert r2.status_code == 429
    assert "TEAM:TOKENS" in r2.json()["detail"]
    assert _uuid  # 保持 uuid 引用形态一致


async def test_team_deleted_policy_zero_hit(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「引用目标(团队)删除后零命中」:团队删除 → 该策略零限流效果
    (=放行,非拒绝),策略行保留不级联。"""
    _uid, token, _ = await _user_with_key(client, session, make_user, "qt_m3")
    tid = await _default_tenant_id(session)
    team_id = await _mk_team(session, str(tid), "qt_m3_team")
    _bot_id, api_key = await _bot_with_key(client, token, "m3", team_id=team_id)
    await _set_scope_policy(str(tid), "TEAM", team_id, "TOKENS", limit=1)
    # 删除团队(引用目标消失;bot.team_id 残留指向死团队)
    from sqlalchemy import text as _text

    from kongming.db import get_sessionmaker

    async with get_sessionmaker()() as s:
        await s.execute(
            _text("DELETE FROM public.sys_team WHERE id=CAST(:i AS uuid)"), {"i": team_id}
        )
        await s.commit()
    r = await _post_chat(client, api_key)
    assert r.status_code != 429  # 零命中=不限制
    n = int(
        (
            await session.execute(
                text(
                    "SELECT count(*) FROM public.sys_quota_policy"
                    " WHERE scope='TEAM' AND scope_ref=CAST(:i AS uuid)"
                ),
                {"i": team_id},
            )
        ).scalar_one()
    )
    assert n == 1  # 策略行保留,不级联清理


# ── 2.3 CALLS 维度仅 TENANT ──


async def test_calls_non_tenant_scopes_rejected_400(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「CALLS 维度仅 TENANT 级」:BOT 已有 035 用例;USER/TEAM 级
    CALLS 策略创建期一律 400。"""
    uid, _token, _ = await _user_with_key(client, session, make_user, "qt_c1")
    tid = await _default_tenant_id(session)
    team_id = await _mk_team(session, str(tid), "qt_c1_team")
    admin = await _admin_token(client, session)
    for scope, ref in (("USER", str(uid)), ("TEAM", team_id)):
        r = await client.post(
            "/billing/policies",
            headers={"Authorization": f"Bearer {admin}"},
            json={
                "tenant_id": str(tid),
                "dimension": "CALLS",
                "limit_value": 10,
                "scope": scope,
                "scope_ref": ref,
            },
        )
        assert r.status_code == 400, (scope, r.text)
