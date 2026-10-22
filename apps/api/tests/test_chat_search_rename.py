"""覆盖会话搜索、重命名和租户隔离。"""
# pyright: reportPrivateUsage=false

import uuid

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import MakeUser
from tests.test_chat_query import _auth, _bind, _default_tenant_id, _user_with_chat_perm
from tests.test_chat_sessions import _mk_session


async def _mk_user_msg(s: AsyncSession, session_id: str, question: str) -> None:
    await s.execute(
        text(
            "INSERT INTO public.chat_message (id, session_id, role, question, status)"
            " VALUES (gen_random_uuid(), :s, 'USER', :q, 'SUCCESS')"
        ),
        {"s": session_id, "q": question},
    )
    await s.commit()


# ============ 会话搜索 ============


async def test_search_by_title(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    uid, token = await _user_with_chat_perm(client, session, make_user, "srch-title")
    tid = await _default_tenant_id(session)
    hit = await _mk_session(session, user_id=uid, tenant_id=tid, title="月度销售复盘")
    await _mk_session(session, user_id=uid, tenant_id=tid, title="库存闲聊")
    r = await client.get("/chat/sessions", params={"q": "复盘"}, headers=_auth(token))
    assert r.status_code == 200
    assert [i["id"] for i in r.json()] == [hit]


async def test_search_by_question_content(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """标题不含但 user 问句含关键词 → EXISTS 子查询命中。"""
    uid, token = await _user_with_chat_perm(client, session, make_user, "srch-msg")
    tid = await _default_tenant_id(session)
    hit = await _mk_session(session, user_id=uid, tenant_id=tid, title="库存问答")
    await _mk_user_msg(session, hit, "连弩还有多少库存")
    miss = await _mk_session(session, user_id=uid, tenant_id=tid, title="客户列表")
    await _mk_user_msg(session, miss, "有哪些客户")
    r = await client.get("/chat/sessions", params={"q": "连弩"}, headers=_auth(token))
    assert [i["id"] for i in r.json()] == [hit]


async def test_search_wildcard_literal(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """%/_ 按字面匹配(同款防线，共享 SQL 模糊模式工具)。"""
    uid, token = await _user_with_chat_perm(client, session, make_user, "srch-wild")
    tid = await _default_tenant_id(session)
    pct = await _mk_session(session, user_id=uid, tenant_id=tid, title="完成率100%达成")
    await _mk_session(session, user_id=uid, tenant_id=tid, title="普通会话")
    r = await client.get("/chat/sessions", params={"q": "100%"}, headers=_auth(token))
    assert [i["id"] for i in r.json()] == [pct]
    r = await client.get("/chat/sessions", params={"q": "_"}, headers=_auth(token))
    assert r.json() == []  # 下划线不展开为单字符通配


async def test_search_isolation_cross_user(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """他人会话的问句命中也不返回(过滤而非报错)。"""
    from tests.test_chat_query import _login

    uid_a, _ = await _user_with_chat_perm(client, session, make_user, "srch-a")
    tid = await _default_tenant_id(session)
    s_b = await _mk_session(session, user_id=uid_a, tenant_id=tid, title="甲的会话")
    await _mk_user_msg(session, s_b, "连弩库存")
    _, token_b = await _user_with_chat_perm(client, session, make_user, "srch-b")
    r = await client.get("/chat/sessions", params={"q": "连弩"}, headers=_auth(token_b))
    assert r.json() == []
    _ = _login  # 保留引用(辅助函数样板)


async def test_search_blank_equals_no_search(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    uid, token = await _user_with_chat_perm(client, session, make_user, "srch-blank")
    tid = await _default_tenant_id(session)
    for t in ("s1", "s2", "s3"):
        await _mk_session(session, user_id=uid, tenant_id=tid, title=t)
    for q in ("", "   "):
        r = await client.get("/chat/sessions", params={"q": q}, headers=_auth(token))
        assert len(r.json()) == 3  # 纯空白等价不搜索


async def test_search_keeps_updated_order(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    uid, token = await _user_with_chat_perm(client, session, make_user, "srch-ord")
    tid = await _default_tenant_id(session)
    old = await _mk_session(
        session,
        user_id=uid,
        tenant_id=tid,
        title="复盘-旧",
        updated_at_expr="now() - interval '2 days'",
    )
    new = await _mk_session(session, user_id=uid, tenant_id=tid, title="复盘-新")
    r = await client.get("/chat/sessions", params={"q": "复盘"}, headers=_auth(token))
    assert [i["id"] for i in r.json()] == [new, old]


# ============ 会话重命名 ============


async def test_rename_success_and_reorder(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    uid, token = await _user_with_chat_perm(client, session, make_user, "ren-ok")
    tid = await _default_tenant_id(session)
    s_old = await _mk_session(
        session,
        user_id=uid,
        tenant_id=tid,
        title="旧置顶",
        updated_at_expr="now() - interval '1 day'",
    )
    s_new = await _mk_session(session, user_id=uid, tenant_id=tid, title="新会话")
    r = await client.patch(
        f"/chat/sessions/{s_old}", json={"title": "  月度复盘  "}, headers=_auth(token)
    )
    assert r.status_code == 200
    body = r.json()
    assert body["title"] == "月度复盘"  # 去首尾空白
    # updated_at 刷新 → 列表置顶
    r = await client.get("/chat/sessions", headers=_auth(token))
    assert [i["id"] for i in r.json()][0] == s_old
    assert s_new in [i["id"] for i in r.json()]


async def test_rename_blank_title_400(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    uid, token = await _user_with_chat_perm(client, session, make_user, "ren-blank")
    tid = await _default_tenant_id(session)
    sid = await _mk_session(session, user_id=uid, tenant_id=tid, title="原标题")
    r = await client.patch(f"/chat/sessions/{sid}", json={"title": "   "}, headers=_auth(token))
    assert r.status_code == 400
    r = await client.patch(f"/chat/sessions/{sid}", json={"title": ""}, headers=_auth(token))
    assert r.status_code in (400, 422)  # 空串传输层/领域层双闸,均参数错误
    title = (
        await session.execute(text("SELECT title FROM public.chat_session WHERE id=:i"), {"i": sid})
    ).scalar_one()
    assert title == "原标题"  # 未更新


async def test_rename_overlong_400(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """非法判定以 strip 后值为准(spec 立约「去首尾空白后非空且不超上限」)——
    裸 201 字走领域层 400;超 2000 传输层 422 防离谱请求体。"""
    uid, token = await _user_with_chat_perm(client, session, make_user, "ren-long")
    tid = await _default_tenant_id(session)
    sid = await _mk_session(session, user_id=uid, tenant_id=tid)
    r = await client.patch(f"/chat/sessions/{sid}", json={"title": "x" * 201}, headers=_auth(token))
    assert r.status_code == 400  # 领域层 strip 后 >200
    r = await client.patch(
        f"/chat/sessions/{sid}", json={"title": "x" * 2001}, headers=_auth(token)
    )
    assert r.status_code == 422  # 传输层防离谱请求体


async def test_rename_strip_padded_201_succeeds(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """⑰:带空白 201 字 strip 后 ≤200 被传输层 422 误拒,
    现按 spec「去首尾空白后不超上限」成功并落 strip 后值。"""
    uid, token = await _user_with_chat_perm(client, session, make_user, "ren-pad")
    tid = await _default_tenant_id(session)
    sid = await _mk_session(session, user_id=uid, tenant_id=tid, title="原标题")
    padded = " " + "x" * 200  # 总长 201,strip 后恰 200
    r = await client.patch(f"/chat/sessions/{sid}", json={"title": padded}, headers=_auth(token))
    assert r.status_code == 200
    title = (
        await session.execute(text("SELECT title FROM public.chat_session WHERE id=:i"), {"i": sid})
    ).scalar_one()
    assert title == "x" * 200  # strip 后值落库


async def test_rename_other_user_404(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    uid, _ = await _user_with_chat_perm(client, session, make_user, "ren-owner")
    tid = await _default_tenant_id(session)
    sid = await _mk_session(session, user_id=uid, tenant_id=tid, title="甲的")
    _, token_b = await _user_with_chat_perm(client, session, make_user, "ren-other")
    r = await client.patch(
        f"/chat/sessions/{sid}", json={"title": "乙改的"}, headers=_auth(token_b)
    )
    assert r.status_code == 404  # 不存在与越权同形
    title = (
        await session.execute(text("SELECT title FROM public.chat_session WHERE id=:i"), {"i": sid})
    ).scalar_one()
    assert title == "甲的"


async def test_rename_not_found_404(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    _, token = await _user_with_chat_perm(client, session, make_user, "ren-nf")
    r = await client.patch(
        f"/chat/sessions/{uuid.uuid4()}", json={"title": "x"}, headers=_auth(token)
    )
    assert r.status_code == 404


async def test_rename_no_perm_403(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    from kongming.auth.seed import seed_all
    from kongming.config import get_settings
    from tests.test_chat_query import _login, _mk_role_with_perms

    await seed_all(session, get_settings())
    uid = await make_user("ren-noperm")
    rid = await _mk_role_with_perms(session, "REN_NOCHAT", [])
    await _bind(session, "sys_user_role", uid, rid)
    await _bind(session, "sys_user_tenant", uid, await _default_tenant_id(session))
    token = await _login(client, "ren-noperm")
    r = await client.patch(
        f"/chat/sessions/{uuid.uuid4()}", json={"title": "x"}, headers=_auth(token)
    )
    assert r.status_code == 403
