"""覆盖反馈处置状态、可见性、关联表抽取和统计口径。"""
# pyright: reportPrivateUsage=false

import uuid
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import MakeUser
from tests.test_chat_query import _auth, _bind, _default_tenant_id, _login, _mk_role_with_perms
from tests.test_chat_sessions import _mk_session
from tests.test_rag_admin import _admin_token


async def _mk_feedback_msg(
    s: AsyncSession,
    *,
    user_id: str,
    tenant_id: str,
    rating: str = "DOWN",
    comment: str | None = "留言",
    corrected_sql: str | None = None,
    sql_text: str | None = "SELECT 1",
    feedback_time: datetime | None = None,
    with_time: bool = True,
    question: str | None = "问句",
) -> str:
    """构造已提交反馈的 assistant 消息；管理端测试不依赖反馈提交路由。"""

    sid = await _mk_session(s, user_id=user_id, tenant_id=tenant_id)
    if question is not None:
        await s.execute(
            text(
                "INSERT INTO public.chat_message"
                " (id, session_id, role, question, status, created_at)"
                " VALUES (gen_random_uuid(), :s, 'USER', :q, 'SUCCESS',"
                "  now() - interval '1 second')"
            ),
            {"s": sid, "q": question},
        )
    ft = feedback_time if feedback_time is not None else (datetime.now(UTC) if with_time else None)
    mid = (
        await s.execute(
            text(
                "INSERT INTO public.chat_message"
                " (id, session_id, role, sql_text, status, rating, feedback_comment,"
                "  corrected_sql, feedback_time, feedback_status)"
                " VALUES (gen_random_uuid(), :s, 'ASSISTANT', :sql, 'SUCCESS', :r, :c, :cs,"
                "  :ft, 'PENDING') RETURNING id"
            ),
            {
                "s": sid,
                "sql": sql_text,
                "r": rating,
                "c": comment,
                "cs": corrected_sql,
                "ft": ft,
            },
        )
    ).scalar_one()
    await s.commit()
    return str(mid)


async def _view_user(
    client: AsyncClient, s: AsyncSession, make_user: MakeUser, username: str, *perms: str
) -> tuple[str, str]:
    """SELF 数据范围用户,绑指定 system:feedback:* 权限(测非 ALL 行级可见性)。"""
    uid = await make_user(username)
    rid = await _mk_role_with_perms(s, f"FBV-{username}", list(perms))
    await _bind(s, "sys_user_role", uid, rid)
    await _bind(s, "sys_user_tenant", uid, await _default_tenant_id(s))
    return uid, await _login(client, username)


# ============ 处置(feedback-refine) ============


async def test_handle_status_success_with_audit(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    token = await _admin_token(client, session)
    tid = await _default_tenant_id(session)
    uid = await make_user("fbh-owner")
    mid = await _mk_feedback_msg(session, user_id=uid, tenant_id=tid)
    r = await client.put(
        f"/feedback/{mid}/status",
        json={"status": "ADOPTED", "note": "已排期精修"},
        headers=_auth(token),
    )
    assert r.status_code == 200
    assert r.json() == {"status": "ADOPTED", "note": "已排期精修"}
    row = (
        (
            await session.execute(
                text(
                    "SELECT feedback_status, feedback_handler IS NOT NULL AS has_handler,"
                    " feedback_handle_time IS NOT NULL AS has_time, feedback_handle_note"
                    " FROM public.chat_message WHERE id=:i"
                ),
                {"i": mid},
            )
        )
        .mappings()
        .one()
    )
    assert row["feedback_status"] == "ADOPTED"
    assert row["has_handler"] is True
    assert row["has_time"] is True
    assert row["feedback_handle_note"] == "已排期精修"
    oper = (
        await session.execute(
            text("SELECT count(*) FROM public.sys_oper_log WHERE action='feedback.handle'")
        )
    ).scalar_one()
    assert oper == 1


async def test_handle_invalid_status_400(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    token = await _admin_token(client, session)
    tid = await _default_tenant_id(session)
    uid = await make_user("fbh-inv")
    mid = await _mk_feedback_msg(session, user_id=uid, tenant_id=tid)
    r = await client.put(f"/feedback/{mid}/status", json={"status": "NONE"}, headers=_auth(token))
    assert r.status_code == 400  # NONE 不可写(仅 PENDING/ADOPTED/REJECTED/DISMISSED)
    r = await client.put(f"/feedback/{mid}/status", json={"status": "BOGUS"}, headers=_auth(token))
    assert r.status_code == 400


async def test_handle_note_merge_semantics(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    token = await _admin_token(client, session)
    tid = await _default_tenant_id(session)
    uid = await make_user("fbh-note")
    mid = await _mk_feedback_msg(session, user_id=uid, tenant_id=tid)
    # 置备注后:null=保持
    await client.put(
        f"/feedback/{mid}/status", json={"status": "ADOPTED", "note": "初注"}, headers=_auth(token)
    )
    r = await client.put(
        f"/feedback/{mid}/status", json={"status": "REJECTED"}, headers=_auth(token)
    )
    assert r.json()["note"] == "初注"  # 保持
    # 显式空串=清空
    r = await client.put(
        f"/feedback/{mid}/status", json={"status": "DISMISSED", "note": ""}, headers=_auth(token)
    )
    assert r.json()["note"] is None


async def test_handle_not_found_404_and_visibility(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    token = await _admin_token(client, session)
    r = await client.put(
        f"/feedback/{uuid.uuid4()}/status", json={"status": "ADOPTED"}, headers=_auth(token)
    )
    assert r.status_code == 404
    # 非 ALL(SELF)处置人:他人反馈 404 同形(防越权处置,spec)
    _, token_self = await _view_user(
        client, session, make_user, "fbh-self", "system:feedback:handle"
    )
    tid = await _default_tenant_id(session)
    uid = await make_user("fbh-victim")
    mid = await _mk_feedback_msg(session, user_id=uid, tenant_id=tid)
    r = await client.put(
        f"/feedback/{mid}/status", json={"status": "ADOPTED"}, headers=_auth(token_self)
    )
    assert r.status_code == 404
    # 自己的反馈可处置
    mid_own = await _mk_feedback_msg(session, user_id=_, tenant_id=tid)
    r = await client.put(
        f"/feedback/{mid_own}/status", json={"status": "ADOPTED"}, headers=_auth(token_self)
    )
    assert r.status_code == 200


async def test_handle_no_perm_403(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """DEVELOPER(chat:execute)无 system:feedback:* → 403。"""
    from tests.test_chat_query import _user_with_chat_perm

    _, token = await _user_with_chat_perm(client, session, make_user, "fbh-np")
    r = await client.put(
        f"/feedback/{uuid.uuid4()}/status", json={"status": "ADOPTED"}, headers=_auth(token)
    )
    assert r.status_code == 403


async def test_handle_unanswered_message_404(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """处置限定已反馈的 assistant 消息——user 消息/零反馈消息 404 同形,
    防「状态非 NONE 但 feedback_time 空」幽灵行。"""
    token = await _admin_token(client, session)
    tid = await _default_tenant_id(session)
    uid = await make_user("fbh-ghost")
    sid = await _mk_session(session, user_id=uid, tenant_id=tid)
    # 零反馈 assistant 消息
    bare = (
        await session.execute(
            text(
                "INSERT INTO public.chat_message (id, session_id, role, sql_text, status)"
                " VALUES (gen_random_uuid(), :s, 'ASSISTANT', 'SELECT 1', 'SUCCESS')"
                " RETURNING id"
            ),
            {"s": sid},
        )
    ).scalar_one()
    # user 消息
    umsg = (
        await session.execute(
            text(
                "INSERT INTO public.chat_message (id, session_id, role, question, status)"
                " VALUES (gen_random_uuid(), :s, 'USER', '问句', 'SUCCESS') RETURNING id"
            ),
            {"s": sid},
        )
    ).scalar_one()
    await session.commit()
    for mid in (str(bare), str(umsg)):
        r = await client.put(
            f"/feedback/{mid}/status", json={"status": "ADOPTED"}, headers=_auth(token)
        )
        assert r.status_code == 404
        row = (
            await session.execute(
                text("SELECT feedback_status FROM public.chat_message WHERE id=:i"), {"i": mid}
            )
        ).scalar_one()
        assert row == "NONE"  # 无幽灵行


async def test_resubmit_clears_handle_columns(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """已处置(ADOPTED)消息被再次反馈→重置 PENDING 且处置三列清空
    (防「新 PENDING + 陈旧处置者」组合)。"""
    token = await _admin_token(client, session)
    tid = await _default_tenant_id(session)
    uid, token_u = await _view_user(client, session, make_user, "fbh-resub", "chat:feedback:submit")
    mid = await _mk_feedback_msg(session, user_id=uid, tenant_id=tid)
    await client.put(
        f"/feedback/{mid}/status",
        json={"status": "ADOPTED", "note": "已采纳"},
        headers=_auth(token),
    )
    r = await client.post(
        f"/chat/messages/{mid}/feedback", json={"rating": "UP"}, headers=_auth(token_u)
    )
    assert r.status_code == 200
    row = (
        (
            await session.execute(
                text(
                    "SELECT feedback_status, feedback_handler, feedback_handle_time,"
                    " feedback_handle_note FROM public.chat_message WHERE id=:i"
                ),
                {"i": mid},
            )
        )
        .mappings()
        .one()
    )
    assert row["feedback_status"] == "PENDING"
    assert row["feedback_handler"] is None
    assert row["feedback_handle_time"] is None
    assert row["feedback_handle_note"] is None


async def test_page_invalid_uuid_422(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """非法 uuid 传输层 422(裸 str 直穿 CAST → PG 22P02 裸 500)。"""
    token = await _admin_token(client, session)
    r = await client.get("/feedback/page", params={"user_id": "abc"}, headers=_auth(token))
    assert r.status_code == 422
    r = await client.get("/feedback/page", params={"tenant_id": "abc"}, headers=_auth(token))
    assert r.status_code == 422
    r = await client.get("/feedback/overview", params={"tenant_id": "abc"}, headers=_auth(token))
    assert r.status_code == 422


# ============ 涉及表定位(feedback-refine) ============


async def test_involved_tables_extract_and_match(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    token = await _admin_token(client, session)
    tid = await _default_tenant_id(session)
    uid = await make_user("fbt-owner")
    # 知识条目:goods 有,film 无
    await session.execute(
        text(
            "INSERT INTO rag.table_descriptions"
            " (id, tenant_id, datasource_key, schema_name, table_name,"
            "  table_comment, columns_comment, sample_queries, source_type)"
            " VALUES (gen_random_uuid(), :t, 'default', 'tenant_default', 'goods',"
            "  '商品', '{}', '{}', 'AI_GENERATED')"
        ),
        {"t": tid},
    )
    await session.commit()
    mid = await _mk_feedback_msg(
        session,
        user_id=uid,
        tenant_id=tid,
        sql_text=(
            "SELECT g.name FROM tenant_default.goods g JOIN tenant_default.goods g2 ON g.id=g2.id"
        ),
        corrected_sql="WITH x AS (SELECT 1) SELECT count(*) FROM tenant_default.goods",
    )
    r = await client.get(f"/feedback/{mid}/involved-tables", headers=_auth(token))
    assert r.status_code == 200
    body = r.json()
    assert body["parse_error"] is False
    by_name = {t["table_name"]: t for t in body["tables"]}
    assert set(by_name) == {"goods"}  # 自 JOIN 去重;CTE 名 x 不进表集
    assert by_name["goods"]["has_knowledge"] is True
    assert by_name["goods"]["knowledge_id"] is not None
    assert by_name["goods"]["datasource_key"] == "default"


async def test_involved_tables_parse_error_fallback(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    token = await _admin_token(client, session)
    tid = await _default_tenant_id(session)
    uid = await make_user("fbt-pe")
    mid = await _mk_feedback_msg(
        session, user_id=uid, tenant_id=tid, sql_text="SELECT '未闭合", corrected_sql=None
    )
    r = await client.get(f"/feedback/{mid}/involved-tables", headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["parse_error"] is True  # 解析失败兜底(spec)


async def test_involved_tables_no_perm_403(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    from tests.test_chat_query import _user_with_chat_perm

    _, token = await _user_with_chat_perm(client, session, make_user, "fbt-np")
    r = await client.get(f"/feedback/{uuid.uuid4()}/involved-tables", headers=_auth(token))
    assert r.status_code == 403


# ============ 统计概览(feedback-analytics) ============


async def test_overview_counts_and_trend_zero_fill(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    token = await _admin_token(client, session)
    tid = await _default_tenant_id(session)
    uid = await make_user("fbo-owner")
    now = datetime.now(UTC)
    await _mk_feedback_msg(session, user_id=uid, tenant_id=tid, rating="UP", feedback_time=now)
    await _mk_feedback_msg(
        session,
        user_id=uid,
        tenant_id=tid,
        rating="DOWN",
        corrected_sql="SELECT 1",
        feedback_time=now - timedelta(days=1),
    )
    # 未反馈消息(无 feedback_time)不计入口径
    await _mk_feedback_msg(
        session, user_id=uid, tenant_id=tid, rating="DOWN", with_time=False, question=None
    )
    r = await client.get("/feedback/overview", params={"days": 7}, headers=_auth(token))
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert body["up_count"] == 1
    assert body["down_count"] == 1
    assert body["corrected_count"] == 1
    assert body["down_rate"] == 0.5
    assert len(body["trend"]) == 7  # 逐天补零
    assert sum(t["up"] for t in body["trend"]) == 1
    assert sum(t["down"] for t in body["trend"]) == 1


async def test_overview_empty_zero_division(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    token = await _admin_token(client, session)
    r = await client.get("/feedback/overview", headers=_auth(token))
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 0
    assert body["down_rate"] == 0  # 分母零不炸(spec)
    assert len(body["trend"]) == 30  # 默认 days=30


async def test_overview_no_perm_403(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    from tests.test_chat_query import _user_with_chat_perm

    _, token = await _user_with_chat_perm(client, session, make_user, "fbo-np")
    r = await client.get("/feedback/overview", headers=_auth(token))
    assert r.status_code == 403


# ============ 明细分页(feedback-analytics) ============


async def test_page_filters_and_order(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    token = await _admin_token(client, session)
    tid = await _default_tenant_id(session)
    uid = await make_user("fbp-owner")
    now = datetime.now(UTC)
    m1 = await _mk_feedback_msg(
        session,
        user_id=uid,
        tenant_id=tid,
        rating="UP",
        feedback_time=now - timedelta(hours=2),
        question="第一问",
    )
    m2 = await _mk_feedback_msg(
        session,
        user_id=uid,
        tenant_id=tid,
        rating="DOWN",
        corrected_sql="SELECT 1",
        feedback_time=now - timedelta(hours=1),
        question="第二问",
    )
    r = await client.get("/feedback/page", headers=_auth(token))
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    items = body["items"]
    assert [i["message_id"] for i in items] == [m2, m1]  # feedback_time 倒序
    assert items[0]["question"] == "第二问"  # 同会话该消息前最近 user 问句
    assert items[0]["tenant_key"] == "default"
    assert items[0]["feedback_status"] == "PENDING"
    # rating 筛选
    r = await client.get("/feedback/page", params={"rating": "UP"}, headers=_auth(token))
    assert [i["message_id"] for i in r.json()["items"]] == [m1]
    # onlyCorrected
    r = await client.get("/feedback/page", params={"only_corrected": True}, headers=_auth(token))
    assert [i["message_id"] for i in r.json()["items"]] == [m2]


async def test_page_feedback_status_filter(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    token = await _admin_token(client, session)
    tid = await _default_tenant_id(session)
    uid = await make_user("fbp-fst")
    mid = await _mk_feedback_msg(session, user_id=uid, tenant_id=tid)
    r = await client.get(
        "/feedback/page", params={"feedback_status": "PENDING"}, headers=_auth(token)
    )
    assert [i["message_id"] for i in r.json()["items"]] == [mid]
    r = await client.get(
        "/feedback/page", params={"feedback_status": "ADOPTED"}, headers=_auth(token)
    )
    assert r.json()["total"] == 0
    # 处置后可见
    await client.put(f"/feedback/{mid}/status", json={"status": "ADOPTED"}, headers=_auth(token))
    r = await client.get(
        "/feedback/page", params={"feedback_status": "ADOPTED"}, headers=_auth(token)
    )
    assert r.json()["total"] == 1


async def test_page_visibility_self_forced(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """非 ALL(SELF):强制只见本人反馈,userId 传值被服务端覆盖(spec 行级可见性)。"""
    token_admin = await _admin_token(client, session)
    tid = await _default_tenant_id(session)
    uid_self, token_self = await _view_user(
        client, session, make_user, "fbp-self", "system:feedback:view"
    )
    uid_other = await make_user("fbp-other")
    mid_own = await _mk_feedback_msg(session, user_id=uid_self, tenant_id=tid, question="自己的")
    await _mk_feedback_msg(session, user_id=uid_other, tenant_id=tid, question="别人的")
    # 本人:只见自己
    r = await client.get("/feedback/page", headers=_auth(token_self))
    assert [i["message_id"] for i in r.json()["items"]] == [mid_own]
    # 显式传 userId=他人:被覆盖仍只见自己(spec)
    r = await client.get("/feedback/page", params={"user_id": uid_other}, headers=_auth(token_self))
    assert [i["message_id"] for i in r.json()["items"]] == [mid_own]
    # 超管传 userId 生效
    r = await client.get(
        "/feedback/page", params={"user_id": uid_other}, headers=_auth(token_admin)
    )
    assert [i["question"] for i in r.json()["items"]] == ["别人的"]
    # 概览同样强制(非 ALL 只见本人)
    r = await client.get("/feedback/overview", headers=_auth(token_self))
    assert r.json()["total"] == 1
