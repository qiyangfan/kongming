"""验证问答入口的数据源预检：非法、禁用和跨租户 key 使用相同错误响应，
且不会创建会话或调用 Agent；合法 key 会传递并写入会话。"""
# pyright: reportPrivateUsage=false

from collections.abc import Iterator

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.chat.dependencies import get_agent_client
from kongming.config import get_settings
from kongming.datasource.cipher import get_cipher
from kongming.main import app
from tests.conftest import MakeUser
from tests.test_chat_query import (
    FakeAgent,
    _auth,
    _default_tenant_id,
    _user_with_chat_perm,
)


@pytest.fixture
def fake_agent() -> Iterator[dict[str, FakeAgent]]:
    """scripted agent 替身(与 test_chat_query 同款;fixture 不跨模块共享故就地定义)。"""
    holder: dict[str, FakeAgent] = {}
    app.dependency_overrides[get_agent_client] = lambda: holder["agent"]
    yield holder
    app.dependency_overrides.pop(get_agent_client, None)


def _install(holder: dict[str, FakeAgent], agent: FakeAgent) -> None:
    holder["agent"] = agent


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


async def _mk_ds(s: AsyncSession, key: str, tenant_id: str, *, status: str = "ACTIVE") -> None:
    enc = get_cipher(get_settings().datasource_encrypt_key).encrypt("kongming")
    await s.execute(
        text(
            "INSERT INTO public.sys_datasource"
            " (id, datasource_key, name, tenant_id, host, port, database, username,"
            "  password_enc, default_schema, status)"
            " VALUES (gen_random_uuid(), :k, :k, :t, '127.0.0.1', 5432, 'kongming_test',"
            "         'kongming', :pe, 'public', :st)"
        ),
        {"k": key, "t": tenant_id, "pe": enc, "st": status},
    )
    await s.commit()


async def _session_count(s: AsyncSession) -> int:
    return int((await s.execute(text("SELECT count(*) FROM public.chat_session"))).scalar_one())


async def test_forged_disabled_cross_tenant_same_shape(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, FakeAgent],
) -> None:
    """验证伪造、禁用和跨租户数据源 key 返回相同 400，且不会创建会话或调用 Agent。"""
    agent = FakeAgent()
    _install(fake_agent, agent)
    uid, token = await _user_with_chat_perm(session, make_user)
    assert uid
    tid = await _default_tenant_id(session)
    other_tid = await _mk_tenant(session, "chat-other")
    await _mk_ds(session, "chatoff", tid, status="DISABLED")
    await _mk_ds(session, "chatother", other_tid)

    details: set[str] = set()
    for key in ("ghost", "chatoff", "chatother"):
        r = await client.post(
            "/chat/query",
            json={"question": "有多少商品", "datasource_key": key},
            headers=_auth(token),
        )
        assert r.status_code == 400, key
        details.add(r.json()["detail"])
    assert details == {"DATASOURCE_INVALID"}
    assert agent.calls == []
    assert await _session_count(session) == 0


async def test_unavailable_keeps_datasource_key_snapshot(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, FakeAgent],
) -> None:
    """Agent 不可达且使用非 default 数据源时，响应仍保留 datasource_key 快照。"""
    _install(fake_agent, FakeAgent(boom=True))
    _, token = await _user_with_chat_perm(session, make_user)
    tid = await _default_tenant_id(session)
    await _mk_ds(session, "chatdsdown", tid)
    r = await client.post(
        "/chat/query",
        json={"question": "有多少商品", "datasource_key": "chatdsdown"},
        headers=_auth(token),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False and "暂不可用" in body["error"]
    assert body["datasource_key"] == "chatdsdown"


async def test_valid_key_passes_through(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, FakeAgent],
) -> None:
    """合法 key:预检放行,agent 收到原样 datasource_key,会话落库同一 key。"""
    agent = FakeAgent()
    _install(fake_agent, agent)
    uid, token = await _user_with_chat_perm(session, make_user)
    tid = await _default_tenant_id(session)
    await _mk_ds(session, "chatdyn", tid)

    r = await client.post(
        "/chat/query",
        json={"question": "有多少商品", "datasource_key": "chatdyn"},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    assert agent.calls[0]["datasource_key"] == "chatdyn"  # 透传(凭证不出控制面)
    row = (
        (await session.execute(text("SELECT datasource_key, user_id FROM public.chat_session")))
        .mappings()
        .one()
    )
    assert row["datasource_key"] == "chatdyn"
    assert str(row["user_id"]) == uid


async def test_malformed_key_422(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """畸形 key(大写/连字符)传输层 pattern 拒绝 422(查询面口径收口)。"""
    _, token = await _user_with_chat_perm(session, make_user)
    r = await client.post(
        "/chat/query",
        json={"question": "q", "datasource_key": "BAD-KEY"},
        headers=_auth(token),
    )
    assert r.status_code == 422


async def test_default_key_unchanged(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, FakeAgent],
) -> None:
    """缺省/显式 default 不触发预检,行为不变。"""
    agent = FakeAgent()
    _install(fake_agent, agent)
    _, token = await _user_with_chat_perm(session, make_user)
    r = await client.post("/chat/query", json={"question": "有多少商品"}, headers=_auth(token))
    assert r.status_code == 200
    assert agent.calls[0]["datasource_key"] == "default"
