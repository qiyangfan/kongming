"""反馈提交全栈测试(feedback spec 014,安全规则类测试先行)。

真实 PG:归属 403 同形/仅 assistant/幂等覆盖三态(null 保持/显式覆盖/空串
清空/feedback_time 最新)/correctedSql 防火墙整体拒/纠错沉淀 query_history
(会话快照数据源)/审计同事务。
"""
# pyright: reportPrivateUsage=false

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import MakeUser
from tests.test_chat_query import (
    FakeAgent,
    _auth,
    _bind,
    _default_tenant_id,
    _install,
    _pass_reply,
    _user_with_chat_perm,
)
from tests.test_chat_sessions import _mk_session


@pytest.fixture
def fake_agent() -> Iterator[dict[str, FakeAgent]]:
    """agent 面 dependency_overrides 替身(与 test_chat_query 同形态,模块私有不可跨用)。"""
    from kongming.chat.router import get_agent_client
    from kongming.main import app

    holder: dict[str, FakeAgent] = {}

    def _factory() -> FakeAgent:
        return holder["agent"]

    app.dependency_overrides[get_agent_client] = _factory
    yield holder
    app.dependency_overrides.pop(get_agent_client, None)


async def _mk_assistant_msg(
    s: AsyncSession, session_id: str, *, sql_text: str | None = "SELECT 1"
) -> str:
    mid = (
        await s.execute(
            text(
                "INSERT INTO public.chat_message (id, session_id, role, sql_text, status)"
                " VALUES (gen_random_uuid(), :s, 'ASSISTANT', :sql, 'SUCCESS') RETURNING id"
            ),
            {"s": session_id, "sql": sql_text},
        )
    ).scalar_one()
    await s.commit()
    return str(mid)


async def _mk_user_msg(s: AsyncSession, session_id: str, question: str) -> None:
    await s.execute(
        text(
            "INSERT INTO public.chat_message (id, session_id, role, question, status)"
            " VALUES (gen_random_uuid(), :s, 'USER', :q, 'SUCCESS')"
        ),
        {"s": session_id, "q": question},
    )
    await s.commit()


async def _feedback_row(s: AsyncSession, mid: str) -> dict[str, Any]:
    row = (
        (
            await s.execute(
                text(
                    "SELECT rating, feedback_comment, corrected_sql, feedback_status,"
                    " feedback_time IS NOT NULL AS has_time"
                    " FROM public.chat_message WHERE id=:i"
                ),
                {"i": mid},
            )
        )
        .mappings()
        .one()
    )
    return dict(row)


async def _setup(s: AsyncSession, client: AsyncClient, make_user: MakeUser, name: str):
    """有 chat:feedback:submit(DEVELOPER 推荐绑定,014 种子)的用户+会话+assistant 消息。"""
    uid, token = await _user_with_chat_perm(client, s, make_user, name)
    tid = await _default_tenant_id(s)
    sid = await _mk_session(s, user_id=uid, tenant_id=tid)
    await _mk_user_msg(s, sid, "有多少种商品")
    mid = await _mk_assistant_msg(s, sid)
    return uid, token, tid, sid, mid


# ============ 提交与落库 ============


async def test_submit_up_success_with_audit(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    _uid, token, _, _, mid = await _setup(session, client, make_user, "fb-up")
    r = await client.post(
        f"/chat/messages/{mid}/feedback", json={"rating": "UP"}, headers=_auth(token)
    )
    assert r.status_code == 200
    body = r.json()
    assert body["rating"] == "UP"
    row = await _feedback_row(session, mid)
    assert row["rating"] == "UP"
    assert row["feedback_status"] == "PENDING"  # 状态机 NONE→PENDING
    assert row["has_time"] is True
    oper = (
        (
            await session.execute(
                text(
                    "SELECT action, target FROM public.sys_oper_log WHERE action='feedback.submit'"
                ),
            )
        )
        .mappings()
        .one()
    )
    assert oper["target"] == mid


async def test_submit_down_with_comment_and_sql(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    _, token, _, _, mid = await _setup(session, client, make_user, "fb-down")
    r = await client.post(
        f"/chat/messages/{mid}/feedback",
        json={
            "rating": "DOWN",
            "comment": "数量不对",
            "corrected_sql": "SELECT count(*) FROM tenant_default.goods",
        },
        headers=_auth(token),
    )
    assert r.status_code == 200
    assert r.json()["corrected_sql"] == "SELECT count(*) FROM tenant_default.goods"
    row = await _feedback_row(session, mid)
    assert row["feedback_comment"] == "数量不对"
    # 070:提交路径不再写 query_history(审核后晋升——PENDING 态不可召回)
    hist = (
        await session.execute(
            text("SELECT count(*) FROM rag.query_history WHERE sql_text LIKE '%goods%'")
        )
    ).scalar_one()
    assert hist == 0


# ============ 归属与角色约束 ============


async def test_submit_cross_user_403_same_shape(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    _, _token, _tid, _, mid = await _setup(session, client, make_user, "fb-owner")
    # 另一用户(绑同租户)对他人消息反馈 → 403 同形(不区分不存在与无权)
    _, token_b = await _user_with_chat_perm(client, session, make_user, "fb-other")
    r = await client.post(
        f"/chat/messages/{mid}/feedback", json={"rating": "UP"}, headers=_auth(token_b)
    )
    assert r.status_code == 403
    # 不存在 id 同形 403
    r2 = await client.post(
        f"/chat/messages/{uuid.uuid4()}/feedback", json={"rating": "UP"}, headers=_auth(token_b)
    )
    assert r2.status_code == 403
    assert r2.json() == r.json()
    # 未被拒:原消息反馈列未被污染
    row = await _feedback_row(session, mid)
    assert row["rating"] is None


async def test_submit_user_message_400(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    uid, token = await _user_with_chat_perm(client, session, make_user, "fb-umsg")
    tid = await _default_tenant_id(session)
    sid = await _mk_session(session, user_id=uid, tenant_id=tid)
    await _mk_user_msg(session, sid, "用户问句")
    user_mid = (
        await session.execute(
            text("SELECT id FROM public.chat_message WHERE session_id=:s AND role='USER'"),
            {"s": sid},
        )
    ).scalar_one()
    r = await client.post(
        f"/chat/messages/{user_mid}/feedback", json={"rating": "UP"}, headers=_auth(token)
    )
    assert r.status_code == 400


async def test_submit_no_perm_403(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    uid, _token, _, _, mid = await _setup(session, client, make_user, "fb-noperm-owner")
    # 无角色用户(只绑租户,无 chat:feedback:submit)
    from tests.test_chat_query import _login

    uid2 = await make_user("fb-noperm")
    await _bind(session, "sys_user_tenant", uid2, await _default_tenant_id(session))
    token2 = await _login(client, "fb-noperm")
    r = await client.post(
        f"/chat/messages/{mid}/feedback", json={"rating": "UP"}, headers=_auth(token2)
    )
    assert r.status_code == 403
    assert uid != uid2  # 吊具健全性:确实是另一用户


# ============ 幂等覆盖三态 ============


async def test_submit_idempotent_merge_semantics(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    _, token, _, _, mid = await _setup(session, client, make_user, "fb-merge")
    # 首次:带 comment+sql
    r = await client.post(
        f"/chat/messages/{mid}/feedback",
        json={
            "rating": "DOWN",
            "comment": "初次留言",
            "corrected_sql": "SELECT 1 FROM tenant_default.goods",
        },
        headers=_auth(token),
    )
    assert r.status_code == 200
    t1 = (
        await session.execute(
            text("SELECT feedback_time FROM public.chat_message WHERE id=:i"), {"i": mid}
        )
    ).scalar_one()
    # rating-only 重复提交:null 保持现值
    r = await client.post(
        f"/chat/messages/{mid}/feedback", json={"rating": "UP"}, headers=_auth(token)
    )
    assert r.status_code == 200
    body = r.json()
    assert body["rating"] == "UP"
    assert body["comment"] == "初次留言"  # 保持
    assert body["corrected_sql"] == "SELECT 1 FROM tenant_default.goods"  # 保持
    # 显式空串清空纠错
    r = await client.post(
        f"/chat/messages/{mid}/feedback",
        json={"rating": "UP", "corrected_sql": ""},
        headers=_auth(token),
    )
    assert r.status_code == 200
    assert r.json()["corrected_sql"] is None
    # feedback_time 已刷新(晚于首次)
    t2 = (
        await session.execute(
            text("SELECT feedback_time FROM public.chat_message WHERE id=:i"), {"i": mid}
        )
    ).scalar_one()
    assert t2 >= t1
    # 070:提交路径不写 query_history(审核后晋升);三态合并语义不变,
    # 「样本停用后保留行」由处置路径测试覆盖
    n = (
        await session.execute(
            text("SELECT count(*) FROM rag.query_history WHERE sql_text LIKE '%goods%'")
        )
    ).scalar_one()
    assert n == 0


async def test_submit_same_sql_no_history_write(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """070:重复提交相同 correctedSql 同样零沉淀(提交路径不经召回);
    同反馈消息的样本准入=ADOPTED 时一次晋升(幂等键 message_id)。"""
    _, token, _, _, mid = await _setup(session, client, make_user, "fb-dup")
    payload = {"rating": "DOWN", "corrected_sql": "SELECT 2 FROM tenant_default.goods"}
    for _ in range(2):
        r = await client.post(f"/chat/messages/{mid}/feedback", json=payload, headers=_auth(token))
        assert r.status_code == 200
    n = (
        await session.execute(
            text("SELECT count(*) FROM rag.query_history WHERE sql_text LIKE '%SELECT 2%'")
        )
    ).scalar_one()
    assert n == 0


# ============ 防火墙整体拒 ============


async def test_submit_delete_sql_rejected_atomically(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    _, token, _, _, mid = await _setup(session, client, make_user, "fb-del")
    r = await client.post(
        f"/chat/messages/{mid}/feedback",
        json={"rating": "DOWN", "corrected_sql": "DELETE FROM tenant_default.goods"},
        headers=_auth(token),
    )
    assert r.status_code == 400
    row = await _feedback_row(session, mid)
    assert row["rating"] is None  # 整体拒:反馈列不落
    assert row["feedback_status"] == "NONE"
    n = (await session.execute(text("SELECT count(*) FROM rag.query_history"))).scalar_one()
    assert n == 0  # query_history 无变化


async def test_submit_reserved_table_sql_rejected(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    _, token, _, _, mid = await _setup(session, client, make_user, "fb-rsv")
    r = await client.post(
        f"/chat/messages/{mid}/feedback",
        json={"rating": "DOWN", "corrected_sql": "SELECT password_hash FROM public.sys_user"},
        headers=_auth(token),
    )
    assert r.status_code == 400
    row = await _feedback_row(session, mid)
    assert row["rating"] is None


async def test_submit_cross_tenant_schema_sql_rejected(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    _, token, _, _, mid = await _setup(session, client, make_user, "fb-xschema")
    r = await client.post(
        f"/chat/messages/{mid}/feedback",
        json={"rating": "DOWN", "corrected_sql": "SELECT count(*) FROM tenant_sakila.film"},
        headers=_auth(token),
    )
    assert r.status_code == 400  # schema 边界(tenant_sakila ∉ {tenant_default, public})
    row = await _feedback_row(session, mid)
    assert row["rating"] is None


# ============ messageId 打通(014) ============


async def test_query_response_contains_message_id(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, FakeAgent],
) -> None:
    """014 messageId 打通:实时响应 message_id 与库内 assistant 消息一致,
    datasource_key=执行快照;persist=false 时 message_id 为 null(spec)。"""
    _install(fake_agent, FakeAgent(_pass_reply()))
    _, token = await _user_with_chat_perm(client, session, make_user, "fb-mid")
    r = await client.post(
        "/chat/query",
        json={
            "question": "有多少种商品",
            "persist": True,
            "history": [],
            "datasource_key": "default",
        },
        headers=_auth(token),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["message_id"] is not None
    assert body["datasource_key"] == "default"
    db_mid = (
        await session.execute(
            text("SELECT id FROM public.chat_message WHERE session_id=:s AND role='ASSISTANT'"),
            {"s": body["session_id"]},
        )
    ).scalar_one()
    assert body["message_id"] == str(db_mid)
    # persist=false 无会话调用 message_id=null
    r2 = await client.post(
        "/chat/query",
        json={"question": "临时一问", "persist": False, "history": [], "datasource_key": "default"},
        headers=_auth(token),
    )
    assert r2.status_code == 200
    assert r2.json()["message_id"] is None


async def test_history_messages_echo_feedback_fields(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """历史消息回显 rating/corrected_sql;user 消息恒 null(spec「messageId 打通」)。"""
    _, token, _, sid, mid = await _setup(session, client, make_user, "fb-echo")
    r = await client.post(
        f"/chat/messages/{mid}/feedback",
        json={
            "rating": "DOWN",
            "comment": "历史留言",
            "corrected_sql": "SELECT 9 FROM tenant_default.goods",
        },
        headers=_auth(token),
    )
    assert r.status_code == 200
    r = await client.get(f"/chat/sessions/{sid}/messages", headers=_auth(token))
    assert r.status_code == 200
    items = r.json()
    user_item = next(i for i in items if i["role"] == "USER")
    asst_item = next(i for i in items if i["role"] == "ASSISTANT")
    assert user_item["rating"] is None
    assert user_item["corrected_sql"] is None
    assert user_item["feedback_comment"] is None
    assert asst_item["rating"] == "DOWN"
    assert asst_item["corrected_sql"] == "SELECT 9 FROM tenant_default.goods"
    assert asst_item["feedback_comment"] == "历史留言"  # R1-S3:comment 回显(弹窗预填源)
