"""审计查询全栈测试(audit spec 015 delta,安全规则类测试先行)。

SQL 审计/操作日志分页筛选+倒序+详情完整 sql_text;行级可见性(非 ALL
强制本人,传值被覆盖;详情 403 同形);无权限 403。真实 PG。
"""

import json
import uuid
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import MakeUser
from tests.test_chat_query import _auth, _bind, _default_tenant_id, _login, _mk_role_with_perms
from tests.test_rag_admin import _admin_token


async def _mk_audit(
    s: AsyncSession,
    *,
    user_id: str | None,
    tenant_id: str,
    verdict: str = "PASS",
    trace_id: str | None = None,
    question: str = "问句",
    created_at: datetime | None = None,
) -> str:
    aid = (
        await s.execute(
            text(
                "INSERT INTO public.sys_sql_audit"
                " (id, trace_id, user_id, tenant_id, datasource_key, question,"
                "  sql_text, verdict, row_count, duration_ms, created_at)"
                " VALUES (gen_random_uuid(), :tr, :u, :t, 'default', :q,"
                "  'SELECT count(*) FROM goods', :v, 4, 12, :ca) RETURNING id"
            ),
            {
                "tr": trace_id or uuid.uuid4().hex[:16],
                "u": user_id,
                "t": tenant_id,
                "q": question,
                "v": verdict,
                "ca": created_at or datetime.now(UTC),
            },
        )
    ).scalar_one()
    await s.commit()
    return str(aid)


async def _mk_oper(
    s: AsyncSession, *, user_id: str | None, action: str = "user.create", ip: str | None = None
) -> None:
    await s.execute(
        text(
            "INSERT INTO public.sys_oper_log (id, user_id, action, target, detail, ip)"
            " VALUES (gen_random_uuid(), :u, :a, 'target', '{}', :ip)"
        ),
        {"u": user_id, "a": action, "ip": ip},
    )
    await s.commit()


async def _self_audit_user(
    client: AsyncClient, s: AsyncSession, make_user: MakeUser, username: str
) -> tuple[str, str]:
    """SELF 数据范围 + system:audit:view(测非 ALL 行级可见性)。"""
    from kongming.auth.seed import seed_all
    from kongming.config import get_settings

    await seed_all(s, get_settings())
    uid = await make_user(username)
    rid = await _mk_role_with_perms(s, f"AUD-{username}", ["system:audit:view"])
    await _bind(s, "sys_user_role", uid, rid)
    await _bind(s, "sys_user_tenant", uid, await _default_tenant_id(s))
    return uid, await _login(client, username)


# ============ SQL 审计分页/详情 ============


async def test_sql_page_filters_and_order(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    token = await _admin_token(client, session)
    tid = await _default_tenant_id(session)
    now = datetime.now(UTC)
    a1 = await _mk_audit(session, user_id=None, tenant_id=tid, verdict="PASS",
                         trace_id="trace-aaa", created_at=now - timedelta(hours=2))
    a2 = await _mk_audit(session, user_id=None, tenant_id=tid, verdict="BLOCKED",
                         trace_id="trace-bbb", created_at=now - timedelta(hours=1))
    r = await client.get("/audit/sql/page", headers=_auth(token))
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert [i["id"] for i in body["items"]] == [a2, a1]  # created_at 倒序
    assert body["items"][0]["tenant_key"] == "default"
    # verdict 筛选
    r = await client.get("/audit/sql/page", params={"verdict": "BLOCKED"}, headers=_auth(token))
    assert [i["id"] for i in r.json()["items"]] == [a2]
    # trace_id 筛选
    r = await client.get("/audit/sql/page", params={"trace_id": "trace-aaa"}, headers=_auth(token))
    assert [i["id"] for i in r.json()["items"]] == [a1]
    # 时间范围(起:1.5 小时前——只有 a2)
    r = await client.get(
        "/audit/sql/page",
        params={"start_time": (now - timedelta(hours=1, minutes=30)).isoformat()},
        headers=_auth(token),
    )
    assert [i["id"] for i in r.json()["items"]] == [a2]


async def test_sql_detail_full_text(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    token = await _admin_token(client, session)
    tid = await _default_tenant_id(session)
    aid = await _mk_audit(session, user_id=None, tenant_id=tid)
    r = await client.get(f"/audit/sql/{aid}", headers=_auth(token))
    assert r.status_code == 200
    body = r.json()
    assert body["sql_text"] == "SELECT count(*) FROM goods"  # 完整非截断
    assert body["verdict"] == "PASS"
    # 不存在 403 同形(不区分不存在与无权)
    r = await client.get(f"/audit/sql/{uuid.uuid4()}", headers=_auth(token))
    assert r.status_code == 403


async def test_sql_visibility_self_forced(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    uid_self, token_self = await _self_audit_user(client, session, make_user, "aud-self")
    tid = await _default_tenant_id(session)
    uid_other = await make_user("aud-other")
    aid_own = await _mk_audit(session, user_id=uid_self, tenant_id=tid)
    aid_other = await _mk_audit(session, user_id=uid_other, tenant_id=tid)
    # 分页:强制本人
    r = await client.get("/audit/sql/page", headers=_auth(token_self))
    assert [i["id"] for i in r.json()["items"]] == [aid_own]
    # 显式传 user_id=他人:被覆盖仍只见本人
    r = await client.get(
        "/audit/sql/page", params={"user_id": uid_other}, headers=_auth(token_self)
    )
    assert [i["id"] for i in r.json()["items"]] == [aid_own]
    # 详情:本人可见;他人 403 同形
    r = await client.get(f"/audit/sql/{aid_own}", headers=_auth(token_self))
    assert r.status_code == 200
    r = await client.get(f"/audit/sql/{aid_other}", headers=_auth(token_self))
    assert r.status_code == 403
    # 超管传 user_id 生效
    token_admin = await _admin_token(client, session)
    r = await client.get(
        "/audit/sql/page", params={"user_id": uid_other}, headers=_auth(token_admin)
    )
    assert [i["id"] for i in r.json()["items"]] == [aid_other]


async def test_sql_page_no_perm_403(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    from tests.test_chat_query import _user_with_chat_perm

    _, token = await _user_with_chat_perm(client, session, make_user, "aud-np")
    r = await client.get("/audit/sql/page", headers=_auth(token))
    assert r.status_code == 403


# ============ 操作日志分页 ============


async def test_oper_page_filters_and_visibility(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    token = await _admin_token(client, session)
    uid_self, token_self = await _self_audit_user(client, session, make_user, "audop-self")
    await _mk_oper(session, user_id=uid_self, action="user.create", ip="10.0.0.1")
    await _mk_oper(session, user_id=None, action="rag.reindex")
    # 超管全量+action 筛选
    r = await client.get("/audit/oper/page", headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["total"] == 2
    r = await client.get("/audit/oper/page", params={"action": "user.create"}, headers=_auth(token))
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["ip"] == "10.0.0.1"  # ip 列透出
    assert items[0]["user_nickname"] is not None
    # 非 ALL 强制本人(user_id=None 的系统调用不可见——vis 强制 user_id=本人)
    r = await client.get("/audit/oper/page", headers=_auth(token_self))
    assert [i["action"] for i in r.json()["items"]] == ["user.create"]


async def test_oper_page_no_perm_403(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    from tests.test_chat_query import _user_with_chat_perm

    _, token = await _user_with_chat_perm(client, session, make_user, "audop-np")
    r = await client.get("/audit/oper/page", headers=_auth(token))
    assert r.status_code == 403


# ============ ip 链路(015,contextvars 横切) ============


async def test_oper_log_ip_recorded_from_request(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """015 ip 链路:经 HTTP 请求的管理操作,sys_oper_log.ip=client host
    (httpx ASGITransport scope client=("127.0.0.1", port));调用点零改动
    (contextvar 横切)。"""
    from kongming.auth.seed import seed_all
    from kongming.config import get_settings

    await seed_all(session, get_settings())
    token = await _admin_token(client, session)
    tid = await _default_tenant_id(session)
    r = await client.post(
        "/system/users",
        json={
            "username": "ip-probe",
            "password": "Passw0rd!",
            "nickname": "探针",
            "tenant_id": tid,
            "role_ids": [],
        },
        headers=_auth(token),
    )
    assert r.status_code in (200, 201, 204)
    ip = (
        await session.execute(
            text(
                "SELECT ip FROM public.sys_oper_log WHERE action='user.create'"
                " ORDER BY created_at DESC LIMIT 1"
            )
        )
    ).scalar_one()
    assert ip == "127.0.0.1"  # httpx ASGITransport 默认 client host


async def test_oper_log_ip_none_without_request_context(
    session: AsyncSession,
) -> None:
    """无请求上下文(后台任务/直调):ip=None 不炸,语义正确(非用户请求无 ip)。"""
    from kongming.auth.repository import SqlOperLogRepo

    await SqlOperLogRepo(session).add(None, "test.direct", None, {})
    await session.commit()
    ip = (
        await session.execute(
            text(
                "SELECT ip FROM public.sys_oper_log WHERE action='test.direct'"
                " ORDER BY created_at DESC LIMIT 1"
            )
        )
    ).scalar_one()
    assert ip is None


# ============ 敏感参数脱敏锁定(R1-S5,audit spec) ============


async def test_reset_password_detail_never_contains_password(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """脱敏锁定:重置密码的操作日志 detail 不含明文密码/密码字段
    (现状=detail 仅 user_id;测试锁定防倒退,audit spec「敏感参数脱敏」)。"""
    token = await _admin_token(client, session)
    uid = (
        await session.execute(
            text(
                "INSERT INTO public.sys_user (id, username, nickname, password_hash,"
                " status)"
                " VALUES (gen_random_uuid(), 'pw-probe', '探针', 'x', 'ACTIVE')"
                " RETURNING id"
            )
        )
    ).scalar_one()
    await session.commit()
    r = await client.post(
        f"/system/users/{uid}/reset-password",
        json={"new_password": "Sup3rSecret!"},
        headers=_auth(token),
    )
    assert r.status_code in (200, 204)
    row = (
        await session.execute(
            text(
                "SELECT detail FROM public.sys_oper_log"
                " WHERE action='user.reset_password' ORDER BY created_at DESC LIMIT 1"
            )
        )
    ).mappings().one()
    detail = dict(row["detail"]) if isinstance(row["detail"], dict) else {}
    detail_text = json.dumps(detail, ensure_ascii=False)
    assert "Sup3rSecret!" not in detail_text  # 明文密码永不入 detail
    assert all("password" not in k.lower() for k in detail)  # 无密码字段键
