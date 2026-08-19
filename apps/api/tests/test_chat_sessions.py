"""会话列表/消息历史/删除端点全栈测试(chat spec 006 delta,安全规则类)。

真实 PG/Redis;会话数据直插 SQL(arrange 独立于被测实现),
消息要素回显经 /chat/query 真实落库路径验证。
安全面:未认证 401、无权限 403、越权/跨租户/不存在统一 404、级联删除。
"""

import uuid
from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.seed import seed_all
from kongming.chat.agent_client import AgentReply
from kongming.chat.router import get_agent_client
from kongming.config import get_settings
from kongming.main import app
from tests.conftest import MakeUser
from tests.test_chat_query import (
    FakeAgent,
    _auth,
    _bind,
    _default_tenant_id,
    _install,
    _login,
    _mk_role_with_perms,
    _pass_reply,
    _user_with_chat_perm,
)


@pytest.fixture
def fake_agent() -> AsyncIterator[dict[str, FakeAgent]]:  # type: ignore[misc]
    holder: dict[str, FakeAgent] = {}

    def _factory() -> FakeAgent:
        return holder["agent"]

    app.dependency_overrides[get_agent_client] = _factory
    yield holder  # type: ignore[misc]
    app.dependency_overrides.pop(get_agent_client, None)


async def _mk_tenant(s: AsyncSession, key: str) -> str:
    tid = (
        await s.execute(
            text(
                "INSERT INTO public.sys_tenant (id, tenant_key, tenant_name)"
                " VALUES (gen_random_uuid(), :k, :k) RETURNING id"
            ),
            {"k": key},
        )
    ).scalar_one()
    await s.commit()
    return str(tid)


async def _mk_session(
    s: AsyncSession,
    *,
    user_id: str,
    tenant_id: str,
    title: str = "测试会话",
    updated_at_expr: str = "now()",
) -> str:
    sid = (
        await s.execute(
            text(
                "INSERT INTO public.chat_session (id, user_id, tenant_id, title, updated_at)"  # noqa: S608
                f" VALUES (gen_random_uuid(), :u, :t, :title, {updated_at_expr})"  # noqa: S608
                " RETURNING id"
            ),  # noqa: S608  # updated_at_expr 为测试内常量,非用户输入
            {"u": user_id, "t": tenant_id, "title": title},
        )
    ).scalar_one()
    await s.commit()
    return str(sid)


# ============ 安全面(测试先行):列表 ============


async def test_sessions_unauthenticated_401(client: AsyncClient) -> None:
    r = await client.get("/chat/sessions")
    assert r.status_code == 401


async def test_sessions_no_perm_403(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    await seed_all(session, get_settings())
    uid = await make_user("noperm")
    rid = await _mk_role_with_perms(session, "NO_CHAT2", [])
    await _bind(session, "sys_user_role", uid, rid)
    await _bind(session, "sys_user_tenant", uid, await _default_tenant_id(session))
    token = await _login(client, "noperm")
    for path in ("/chat/sessions", f"/chat/sessions/{uuid.uuid4()}/messages"):
        r = await client.get(path, headers=_auth(token))
        assert r.status_code == 403
    r = await client.delete(f"/chat/sessions/{uuid.uuid4()}", headers=_auth(token))
    assert r.status_code == 403


async def test_sessions_only_own_and_current_tenant_ordered(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """本人本租户过滤 + updated_at 倒序(他人/他租户会话不可见)。"""
    uid, token = await _user_with_chat_perm(client, session, make_user, "owner")
    uid_b = await make_user("other")
    t2 = await _mk_tenant(session, "t2")

    s_old = await _mk_session(
        session, user_id=uid, tenant_id=await _default_tenant_id(session),
        title="旧会话", updated_at_expr="now() - interval '3 days'",
    )
    s_new = await _mk_session(
        session, user_id=uid, tenant_id=await _default_tenant_id(session),
        title="新会话",
    )
    await _mk_session(session, user_id=uid_b, tenant_id=await _default_tenant_id(session),
                      title="他人的")
    await _mk_session(session, user_id=uid, tenant_id=t2, title="他租户的")

    r = await client.get("/chat/sessions", headers=_auth(token))
    assert r.status_code == 200
    items = r.json()
    assert [i["id"] for i in items] == [s_new, s_old]
    assert items[0]["title"] == "新会话"
    assert "updated_at" in items[0]


async def test_sessions_limit_bounds(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    uid, token = await _user_with_chat_perm(client, session, make_user, "limiter")
    tid = await _default_tenant_id(session)
    for i in range(3):
        await _mk_session(session, user_id=uid, tenant_id=tid, title=f"s{i}")
    r = await client.get("/chat/sessions", params={"limit": 2}, headers=_auth(token))
    assert r.status_code == 200
    assert len(r.json()) == 2
    r = await client.get("/chat/sessions", params={"limit": 201}, headers=_auth(token))
    assert r.status_code == 422  # 上限 200,超出即拒绝
    r = await client.get("/chat/sessions", params={"limit": 0}, headers=_auth(token))
    assert r.status_code == 422  # 下界 1


async def test_explicit_tenant_header_positive(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """X-Tenant-Id 显式头正向通路(白名单命中→该租户数据域),三端点齐验。"""
    uid, token = await _user_with_chat_perm(client, session, make_user, "thead")
    t2 = await _mk_tenant(session, "t2p")
    await _bind(session, "sys_user_tenant", uid, t2)
    headers = {**_auth(token), "X-Tenant-Id": "t2p"}

    sid = await _mk_session(session, user_id=uid, tenant_id=t2, title="t2 会话")
    await session.execute(
        text(
            "INSERT INTO public.chat_message (id, session_id, role, question, status)"
            " VALUES (gen_random_uuid(), :s, 'USER', 't2 问', 'SUCCESS')"
        ),
        {"s": sid},
    )
    await session.commit()

    r = await client.get("/chat/sessions", headers=headers)
    assert r.status_code == 200
    assert [i["id"] for i in r.json()] == [sid]  # 显式头域下只见 t2 会话

    r = await client.get(f"/chat/sessions/{sid}/messages", headers=headers)
    assert r.status_code == 200
    assert r.json()[0]["question"] == "t2 问"

    r = await client.delete(f"/chat/sessions/{sid}", headers=headers)
    assert r.status_code == 204


# ============ 安全面(测试先行):消息历史 ============


async def test_messages_of_other_user_404(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    uid, _ = await _user_with_chat_perm(client, session, make_user, "msgowner")
    uid_b = await make_user("msgother")
    await _bind(session, "sys_user_role", uid_b, (await _dev_role_id(session)))
    await _bind(session, "sys_user_tenant", uid_b, await _default_tenant_id(session))
    sid = await _mk_session(
        session, user_id=uid, tenant_id=await _default_tenant_id(session)
    )
    token_b = await _login(client, "msgother")
    r = await client.get(f"/chat/sessions/{sid}/messages", headers=_auth(token_b))
    assert r.status_code == 404  # 不存在与越权同形


async def _dev_role_id(s: AsyncSession) -> object:
    return (
        await s.execute(text("SELECT id FROM public.sys_role WHERE role_key='DEVELOPER'"))
    ).scalar_one()


async def test_messages_cross_tenant_404(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    uid, token = await _user_with_chat_perm(client, session, make_user, "crosst")
    t2 = await _mk_tenant(session, "t2c")
    await _bind(
        session, "sys_user_tenant", uid, t2
    )  # 同用户绑双租户,但会话挂在 t2
    sid = await _mk_session(
        session, user_id=uid, tenant_id=t2
    )
    r = await client.get(f"/chat/sessions/{sid}/messages", headers=_auth(token))  # 缺省 default
    assert r.status_code == 404


async def test_messages_not_found_404(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    _, token = await _user_with_chat_perm(client, session, make_user, "nf")
    r = await client.get(f"/chat/sessions/{uuid.uuid4()}/messages", headers=_auth(token))
    assert r.status_code == 404


async def test_messages_echo_order_and_shape(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser,
    fake_agent: dict[str, FakeAgent],
) -> None:
    """经 /chat/query 真实落库(PASS + BLOCKED 各一轮),回显保序且要素完整。"""
    _, token = await _user_with_chat_perm(client, session, make_user, "echo")
    _install(fake_agent, FakeAgent(_pass_reply()))
    r = await client.post(
        "/chat/query", json={"question": "各商品库存"}, headers=_auth(token)
    )
    assert r.status_code == 200
    sid = r.json()["session_id"]
    blocked = AgentReply(
        success=False, verdict="BLOCKED", sql="DELETE FROM goods", error="BLOCKED_ROOT: Delete"
    )
    _install(fake_agent, FakeAgent(blocked))
    r = await client.post(
        "/chat/query", json={"question": "删掉它们", "session_id": sid}, headers=_auth(token)
    )
    assert r.status_code == 200

    r = await client.get(f"/chat/sessions/{sid}/messages", headers=_auth(token))
    assert r.status_code == 200
    msgs = r.json()
    assert [m["role"] for m in msgs] == ["USER", "ASSISTANT", "USER", "ASSISTANT"]
    assert msgs[0]["question"] == "各商品库存"
    first = msgs[1]
    assert first["status"] == "SUCCESS"
    assert first["summary"] == "共 2 种商品"
    assert first["sql"] == "SELECT name, qty FROM goods"
    assert first["result"]["columns"] == ["name", "qty"]
    assert first["result"]["rows"][0] == ["连弩", 30]
    assert first["result"]["chart"]["type"] == "pie"
    assert first["result"]["chart"]["x"] == "name"  # x/y 完整(006 补存)
    rejected = msgs[3]
    assert rejected["status"] == "REJECTED"
    assert "非只读" in rejected["error"]


async def test_messages_legacy_result_json_recomputed(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """存量兼容:005 形态 result_json(裸 chartType,无 x/y/error)→
    回显以 suggest_chart 确定性重算补全 x/y(落库输入=原始输入)。"""
    uid, token = await _user_with_chat_perm(client, session, make_user, "legacy")
    tid = await _default_tenant_id(session)
    sid = await _mk_session(session, user_id=uid, tenant_id=tid)
    legacy = (
        '{"columns": ["name", "qty"], "rows": [["a", 1], ["b", 2]],'
        ' "insights": [], "stats": {}, "chartType": "pie"}'
    )
    await session.execute(
        text(
            "INSERT INTO public.chat_message (id, session_id, role, question, status, created_at)"
            " VALUES (gen_random_uuid(), :s, 'USER', '旧问', 'SUCCESS',"
            " now() - interval '1 second')"
        ),
        {"s": sid},
    )
    await session.execute(
        text(
            "INSERT INTO public.chat_message"
            " (id, session_id, role, sql_text, result_json, summary, status)"
            " VALUES (gen_random_uuid(), :s, 'ASSISTANT', null, :rj, '旧答', 'SUCCESS')"
        ),
        {"s": sid, "rj": legacy},
    )
    await session.commit()
    r = await client.get(f"/chat/sessions/{sid}/messages", headers=_auth(token))
    assert r.status_code == 200
    m = r.json()[1]
    chart = m["result"]["chart"]
    assert (chart["type"], chart["x"], chart["y"]) == ("pie", "name", "qty")  # 重算补全
    assert m["error"] is None  # 存量无 error → null,前端通用文案兜底


async def test_messages_agent_down_shape(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser,
    fake_agent: dict[str, FakeAgent],
) -> None:
    """agent 不可达轮:FAILED + error 文案可回显(006 统一落库路径)。"""
    _, token = await _user_with_chat_perm(client, session, make_user, "down")
    _install(fake_agent, FakeAgent(boom=True))
    r = await client.post("/chat/query", json={"question": "q"}, headers=_auth(token))
    sid = r.json()["session_id"]
    r = await client.get(f"/chat/sessions/{sid}/messages", headers=_auth(token))
    failed = r.json()[1]
    assert failed["status"] == "FAILED"
    assert "暂不可用" in failed["error"]


# ============ 安全面(测试先行):删除 ============


async def test_delete_session_cascades_messages(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser,
    fake_agent: dict[str, FakeAgent],
) -> None:
    _, token = await _user_with_chat_perm(client, session, make_user, "del")
    _install(fake_agent, FakeAgent(_pass_reply()))
    r = await client.post("/chat/query", json={"question": "q"}, headers=_auth(token))
    sid = r.json()["session_id"]
    n = (
        await session.execute(
            text("SELECT count(*) FROM public.chat_message WHERE session_id=:i"), {"i": sid}
        )
    ).scalar_one()
    assert n == 2

    r = await client.delete(f"/chat/sessions/{sid}", headers=_auth(token))
    assert r.status_code == 204
    assert (
        await session.execute(
            text("SELECT count(*) FROM public.chat_session WHERE id=:i"), {"i": sid}
        )
    ).scalar_one() == 0
    assert (
        await session.execute(
            text("SELECT count(*) FROM public.chat_message WHERE session_id=:i"), {"i": sid}
        )
    ).scalar_one() == 0  # 级联
    r = await client.get(f"/chat/sessions/{sid}/messages", headers=_auth(token))
    assert r.status_code == 404


async def test_delete_twice_404(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    uid, token = await _user_with_chat_perm(client, session, make_user, "del2")
    sid = await _mk_session(
        session, user_id=uid, tenant_id=await _default_tenant_id(session)
    )
    r = await client.delete(f"/chat/sessions/{sid}", headers=_auth(token))
    assert r.status_code == 204
    r = await client.delete(f"/chat/sessions/{sid}", headers=_auth(token))
    assert r.status_code == 404


async def test_delete_other_users_404(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    uid, _ = await _user_with_chat_perm(client, session, make_user, "delowner")
    sid = await _mk_session(
        session, user_id=uid, tenant_id=await _default_tenant_id(session)
    )
    uid_b = await make_user("delother")
    await _bind(session, "sys_user_role", uid_b, (await _dev_role_id(session)))
    await _bind(session, "sys_user_tenant", uid_b, await _default_tenant_id(session))
    token_b = await _login(client, "delother")
    r = await client.delete(f"/chat/sessions/{sid}", headers=_auth(token_b))
    assert r.status_code == 404
    # 会话未被误删
    assert (
        await session.execute(
            text("SELECT count(*) FROM public.chat_session WHERE id=:i"), {"i": sid}
        )
    ).scalar_one() == 1
