"""执行计划端点全栈测试(chat spec「EXPLAIN 执行计划」+ 010 delta,安全规则类测试先行)。

真实 PG:EXPLAIN 真过「租户/数据源解析→绑定复校→防火墙初检→data_scope 改写→
复检三件套→只读池」全管线(与 /internal/sql/execute 同源私有段);message_id
引用形态——客户端不可提交裸 SQL,服务端构造 EXPLAIN (FORMAT JSON),永不 ANALYZE;
归属 404/权限 403/无 SQL 400/动态源快照路由全覆盖;审计记 EXPLAIN 形态可回放。
"""

import uuid
from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.config import get_settings
from kongming.datasource.cipher import get_cipher
from kongming.db import get_engine
from kongming.internal import executor
from tests.conftest import MakeUser
from tests.test_chat_query import (
    _auth,
    _bind,
    _default_tenant_id,
    _mk_role_with_perms,
    _user_with_chat_perm,
)
from tests.test_chat_sessions import _mk_session

DS_SCHEMA = "ds_explain"  # 动态源快照证物 schema(同实例异 schema 形态,008 既定)


@pytest.fixture(scope="module", autouse=True)
async def _biz() -> AsyncIterator[None]:
    """默认租户 schema 与动态源 schema 各造一张计划证物表(EXPLAIN 不返回行,
    行数据无关断言——造行仅为规划器统计信息真实)。"""
    async with get_engine().begin() as conn:
        await conn.execute(text('CREATE SCHEMA IF NOT EXISTS "tenant_default"'))
        await conn.execute(
            text(
                'CREATE TABLE IF NOT EXISTS "tenant_default".explain_goods ('
                " id uuid PRIMARY KEY, mark varchar(8), create_by varchar(64))"
            )
        )
        await conn.execute(text('DELETE FROM "tenant_default".explain_goods'))
        await conn.execute(
            text(
                'INSERT INTO "tenant_default".explain_goods (id, mark, create_by)'
                " VALUES (gen_random_uuid(), 'A', 'someone')"
            )
        )
        await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{DS_SCHEMA}"'))
        await conn.execute(
            text(
                f'CREATE TABLE IF NOT EXISTS "{DS_SCHEMA}".goods_exp ('
                " id uuid PRIMARY KEY, mark varchar(8))"
            )
        )
        await conn.execute(text(f'DELETE FROM "{DS_SCHEMA}".goods_exp'))  # noqa: S608  # 模块常量
        await conn.execute(
            text(
                f'INSERT INTO "{DS_SCHEMA}".goods_exp (id, mark)'  # noqa: S608  # 模块常量
                " VALUES (gen_random_uuid(), 'D')"
            )
        )
    yield
    async with get_engine().begin() as conn:
        await conn.execute(text('DROP TABLE IF EXISTS "tenant_default".explain_goods'))
        await conn.execute(text(f'DROP SCHEMA IF EXISTS "{DS_SCHEMA}" CASCADE'))
    # 只读池是模块级全局:本模块真建了 default/exp_dyn 两池,dispose 还原——
    # 后继模块(test_datasource_admin 的「test-connection 不建池」断言)依赖空池前提
    await executor.dispose_read_engines()


async def _mk_msg(
    s: AsyncSession,
    session_id: str,
    *,
    role: str = "ASSISTANT",
    sql: str | None = None,
    status: str = "SUCCESS",
) -> str:
    mid = (
        await s.execute(
            text(
                "INSERT INTO public.chat_message (id, session_id, role, sql_text, status)"
                " VALUES (gen_random_uuid(), :s, :role, :sql, :st) RETURNING id"
            ),
            {"s": session_id, "role": role, "sql": sql, "st": status},
        )
    ).scalar_one()
    await s.commit()
    return str(mid)


async def _mk_ds(s: AsyncSession, key: str, tenant_id: str, *, status: str = "ACTIVE") -> str:
    """直插动态源行(密码经真 cipher 加密,与 008 测试同路径;同实例异 schema)。"""
    enc = get_cipher(get_settings().datasource_encrypt_key).encrypt("kongming")
    did = (
        await s.execute(
            text(
                "INSERT INTO public.sys_datasource"
                " (id, datasource_key, name, tenant_id, host, port, database, username,"
                "  password_enc, default_schema, status)"
                " VALUES (gen_random_uuid(), :k, :k, :t, '127.0.0.1', 5432,"
                " 'kongming_test', 'kongming', :pe, :ds, :st) RETURNING id"
            ),
            {"k": key, "t": tenant_id, "pe": enc, "ds": DS_SCHEMA, "st": status},
        )
    ).scalar_one()
    await s.commit()
    return str(did)


async def _latest_audit(s: AsyncSession, user_id: str) -> dict[str, object]:
    row = (
        (
            await s.execute(
                text(
                    "SELECT verdict, sql_text, datasource_key FROM public.sys_sql_audit"
                    " WHERE user_id = :u ORDER BY created_at DESC, id DESC LIMIT 1"
                ),
                {"u": user_id},
            )
        )
        .mappings()
        .one()
    )
    return dict(row)


# ============ 安全面:认证/权限/归属 ============


async def test_explain_unauthenticated_401(client: AsyncClient) -> None:
    r = await client.post("/chat/explain", json={"message_id": str(uuid.uuid4())})
    assert r.status_code == 401


async def test_explain_no_perm_403(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    from kongming.auth.seed import seed_all

    await seed_all(session, get_settings())
    uid = await make_user("exp-noperm")
    rid = await _mk_role_with_perms(session, "EXP_NOCHAT", [])
    await _bind(session, "sys_user_role", uid, rid)
    await _bind(session, "sys_user_tenant", uid, await _default_tenant_id(session))
    from tests.test_chat_query import _login

    token = await _login(client, "exp-noperm")
    r = await client.post(
        "/chat/explain", json={"message_id": str(uuid.uuid4())}, headers=_auth(token)
    )
    assert r.status_code == 403


async def test_explain_message_not_found_404(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    _, token = await _user_with_chat_perm(client, session, make_user, "exp-nf")
    r = await client.post(
        "/chat/explain", json={"message_id": str(uuid.uuid4())}, headers=_auth(token)
    )
    assert r.status_code == 404


async def test_explain_other_users_message_404(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    uid, _ = await _user_with_chat_perm(client, session, make_user, "exp-owner")
    tid = await _default_tenant_id(session)
    sid = await _mk_session(session, user_id=uid, tenant_id=tid)
    mid = await _mk_msg(session, sid, sql="SELECT mark FROM explain_goods")
    _, token_b = await _user_with_chat_perm(client, session, make_user, "exp-other")
    r = await client.post("/chat/explain", json={"message_id": mid}, headers=_auth(token_b))
    assert r.status_code == 404  # 不存在与越权同形,不泄露消息存在性


async def test_explain_cross_tenant_404(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    uid, token = await _user_with_chat_perm(client, session, make_user, "exp-xt")
    t2 = (
        await session.execute(
            text(
                "INSERT INTO public.sys_tenant (id, tenant_key, tenant_name)"
                " VALUES (gen_random_uuid(), 'exp-t2', 'exp-t2') RETURNING id"
            )
        )
    ).scalar_one()
    await session.commit()
    sid = await _mk_session(session, user_id=uid, tenant_id=str(t2))
    mid = await _mk_msg(session, sid, sql="SELECT mark FROM explain_goods")
    # 缺省头=default 租户域,消息挂在 exp-t2 → 404
    r = await client.post("/chat/explain", json={"message_id": mid}, headers=_auth(token))
    assert r.status_code == 404


async def test_explain_message_without_sql_400(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    uid, token = await _user_with_chat_perm(client, session, make_user, "exp-nosql")
    tid = await _default_tenant_id(session)
    sid = await _mk_session(session, user_id=uid, tenant_id=tid)
    mid_user = await _mk_msg(session, sid, role="USER")  # user 问句无 SQL
    mid_failed = await _mk_msg(session, sid, status="FAILED")  # 失败轮无 SQL
    for mid in (mid_user, mid_failed):
        r = await client.post("/chat/explain", json={"message_id": mid}, headers=_auth(token))
        assert r.status_code == 400


# ============ 执行面:计划树/改写/拦截/审计 ============


async def test_explain_happy_path_returns_plan_tree(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    uid, token = await _user_with_chat_perm(client, session, make_user, "exp-ok")
    tid = await _default_tenant_id(session)
    sid = await _mk_session(session, user_id=uid, tenant_id=tid)
    mid = await _mk_msg(session, sid, sql="SELECT mark FROM explain_goods")
    r = await client.post("/chat/explain", json={"message_id": mid}, headers=_auth(token))
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    plan = body["plan"]
    assert isinstance(plan, list) and plan, "EXPLAIN (FORMAT JSON) 顶层为数组"
    node = plan[0]["Plan"]
    assert node["Node Type"]  # 如 Seq Scan
    assert "Total Cost" in node and "Plan Rows" in node
    # 审计:sql_text=EXPLAIN 形态(可回放),verdict=PASS
    audit = await _latest_audit(session, uid)
    assert audit["verdict"] == "PASS"
    assert str(audit["sql_text"]).startswith("EXPLAIN (FORMAT JSON)")
    assert "explain_goods" in str(audit["sql_text"])
    assert "ANALYZE" not in str(audit["sql_text"])  # 永不真执行


async def test_explain_reuses_data_scope_rewrite(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """SELF 角色:被分析 SQL 先经 data_scope 改写(审计含 create_by 谓词,防越权探测行)。"""
    from kongming.auth.seed import seed_all

    await seed_all(session, get_settings())
    uid = await make_user("exp-self")
    rid = await _mk_role_with_perms(session, "EXP_SELF", ["chat:execute"])
    # SELF 作用域角色(_mk_role_with_perms 缺省 SELF)
    await _bind(session, "sys_user_role", uid, rid)
    await _bind(session, "sys_user_tenant", uid, await _default_tenant_id(session))
    from tests.test_chat_query import _login

    token = await _login(client, "exp-self")
    tid = await _default_tenant_id(session)
    sid = await _mk_session(session, user_id=uid, tenant_id=tid)
    mid = await _mk_msg(session, sid, sql="SELECT mark FROM explain_goods")
    r = await client.post("/chat/explain", json={"message_id": mid}, headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["success"] is True
    audit = await _latest_audit(session, uid)
    assert "create_by" in str(audit["sql_text"])  # 改写后形态(EXPLAIN 前缀内)


async def test_explain_rejected_message_sql_still_blocked(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """REJECTED 轮的落库 SQL(DELETE)再走管线仍被初检拦下:友好返回不 500,
    审计落 BLOCKED——EXPLAIN 通道不构成新的放行面。"""
    uid, token = await _user_with_chat_perm(client, session, make_user, "exp-del")
    tid = await _default_tenant_id(session)
    sid = await _mk_session(session, user_id=uid, tenant_id=tid)
    mid = await _mk_msg(session, sid, sql="DELETE FROM explain_goods", status="REJECTED")
    r = await client.post("/chat/explain", json={"message_id": mid}, headers=_auth(token))
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert body["plan"] is None
    assert "非只读" in body["error"]
    audit = await _latest_audit(session, uid)
    assert audit["verdict"] == "BLOCKED"
    assert not str(audit["sql_text"]).startswith("EXPLAIN")  # 初检即拦,未拼前缀


async def test_explain_reserved_table_blocked(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """诱导形态:落库 SQL 若指向保留表(手改库构造),复检保留表拒读仍拦。"""
    uid, token = await _user_with_chat_perm(client, session, make_user, "exp-rsv")
    tid = await _default_tenant_id(session)
    sid = await _mk_session(session, user_id=uid, tenant_id=tid)
    mid = await _mk_msg(session, sid, sql="SELECT username FROM sys_user")
    r = await client.post("/chat/explain", json={"message_id": mid}, headers=_auth(token))
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert "不允许的表" in body["error"]


async def test_explain_dynamic_datasource_snapshot(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """数据源快照:以会话落库的 datasource_key 路由(非当前切换器实时值),
    动态源失效后同形 400(与问答端点语义一致)。"""
    uid, token = await _user_with_chat_perm(client, session, make_user, "exp-dyn")
    tid = await _default_tenant_id(session)
    await _mk_ds(session, "exp_dyn", tid)
    sid = (
        await session.execute(
            text(
                "INSERT INTO public.chat_session (id, user_id, tenant_id, datasource_key, title)"
                " VALUES (gen_random_uuid(), :u, :t, 'exp_dyn', 'dyn 会话') RETURNING id"
            ),
            {"u": uid, "t": tid},
        )
    ).scalar_one()
    await session.commit()
    mid = await _mk_msg(session, str(sid), sql="SELECT mark FROM goods_exp")
    r = await client.post("/chat/explain", json={"message_id": mid}, headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["success"] is True
    audit = await _latest_audit(session, uid)
    assert audit["datasource_key"] == "exp_dyn"

    # 禁用后同形 400(防 key 探测,与问答一致)
    await session.execute(
        text("UPDATE public.sys_datasource SET status='DISABLED' WHERE datasource_key='exp_dyn'")
    )
    await session.commit()
    r = await client.post("/chat/explain", json={"message_id": mid}, headers=_auth(token))
    assert r.status_code == 400
    assert r.json()["detail"] == "DATASOURCE_INVALID"
