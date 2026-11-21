"""覆盖工具权限、参数校验、限流、审计、确认流程和租户隔离。"""

import json
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, cast

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.seed import seed_all
from kongming.config import get_settings
from kongming.db import get_engine

MakeUser = Callable[..., Awaitable[str]]

INTERNAL = {"X-Internal-Token": "test-internal-token"}
SCHEMA = "tenant_tc"


@pytest.fixture(scope="module", autouse=True)
async def _biz() -> AsyncIterator[None]:  # pyright: ignore[reportUnusedFunction]
    """toolcall 测试租户业务 schema(execute_sql 真执行活数据)。"""
    async with get_engine().begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"'))  # 模块常量
        await conn.execute(
            text(
                f'CREATE TABLE IF NOT EXISTS "{SCHEMA}".goods ('
                " id uuid PRIMARY KEY, name varchar(64), create_by varchar(64))"
            )
        )
        await conn.execute(text(f'DELETE FROM "{SCHEMA}".goods'))  # noqa: S608  # 模块常量
        await conn.execute(
            text(
                f'INSERT INTO "{SCHEMA}".goods (id, name, create_by)'  # noqa: S608  # 模块常量
                " VALUES (gen_random_uuid(), '连弩', 'someone')"
            )
        )
    yield
    async with get_engine().begin() as conn:
        await conn.execute(text(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE'))


# ── arrange 助手(与 test_internal_execute 同形态,保持测试独立) ──


async def _mk_tenant(s: AsyncSession, key: str) -> str:
    tid = (
        await s.execute(
            text(
                "INSERT INTO public.sys_tenant (id, tenant_key, tenant_name, status)"
                " VALUES (gen_random_uuid(), :k, :k, 'ACTIVE') RETURNING id"
            ),
            {"k": key},
        )
    ).scalar_one()
    await s.commit()
    return str(tid)


async def _mk_role_with_perms(
    s: AsyncSession, key: str, codes: list[str], data_scope: str = "SELF"
) -> str:
    rid = (
        await s.execute(
            text(
                "INSERT INTO public.sys_role (id, role_key, role_name, data_scope)"
                " VALUES (gen_random_uuid(), :k, :k, :ds) RETURNING id"
            ),
            {"k": key, "ds": data_scope},
        )
    ).scalar_one()
    for code in codes:
        await s.execute(
            text(
                "INSERT INTO public.sys_permission (id, perm_code, perm_name)"
                " VALUES (gen_random_uuid(), :c, :c) ON CONFLICT DO NOTHING"
            ),
            {"c": code},
        )
        await s.execute(
            text(
                "INSERT INTO public.sys_role_permission (role_id, permission_id)"
                " SELECT :r, p.id FROM public.sys_permission p WHERE p.perm_code = :c"
                " ON CONFLICT DO NOTHING"
            ),
            {"r": rid, "c": code},
        )
    await s.commit()
    return str(rid)


async def _bind(s: AsyncSession, table: str, user_id: str, target_id: str) -> None:
    col = "role_id" if table == "sys_user_role" else "tenant_id"
    await s.execute(
        text(
            f"INSERT INTO public.{table} (user_id, {col}) VALUES (:u, :t)"  # noqa: S608  # 测试常量
        ),
        {"u": user_id, "t": target_id},
    )
    await s.commit()


async def _user_with_tools(
    s: AsyncSession, make_user: MakeUser, username: str, codes: list[str], tenant_key: str = "tc"
) -> str:
    """造一个绑租户+指定工具权限角色的用户,返回 user_id。"""
    uid = await make_user(username)
    rid = await _mk_role_with_perms(s, f"role_{username}", codes)
    tid = (
        await s.execute(
            text("SELECT id FROM public.sys_tenant WHERE tenant_key=:k"),
            {"k": tenant_key},
        )
    ).scalar_one()
    await _bind(s, "sys_user_role", uid, rid)
    await _bind(s, "sys_user_tenant", uid, str(tid))
    return uid


def _login(client_or_subject: object, username: str = "", password: str = "") -> str:
    from tests.conftest import login_token

    subject = client_or_subject if isinstance(client_or_subject, str) else None
    return login_token(subject)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _req(tool: str, args: dict[str, object], **kw: object) -> dict[str, object]:
    base: dict[str, object] = {
        "trace_id": f"tr-{uuid.uuid4().hex[:12]}",
        "tenant_key": "tc",
        "tool_name": tool,
        "args": args,
    }
    base.update(kw)
    return base


async def _tool_audits(s: AsyncSession, trace_id: str) -> list[dict[str, object]]:
    rows = (
        (
            await s.execute(
                text("SELECT * FROM public.sys_tool_audit WHERE trace_id = :t"),
                {"t": trace_id},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


@pytest.fixture(autouse=True)
async def _tenant(session: AsyncSession) -> None:  # pyright: ignore[reportUnusedFunction]
    await _mk_tenant(session, "tc")


# ============ L1 内部令牌与身份复校 ============


async def test_internal_token_required(client: AsyncClient) -> None:
    r = await client.post("/internal/tool/execute", json=_req("validate_sql", {"sql": "SELECT 1"}))
    assert r.status_code == 403


async def test_unknown_tenant_404(client: AsyncClient, make_user: MakeUser) -> None:
    uid = await make_user("tc_unknown")
    r = await client.post(
        "/internal/tool/execute",
        headers=INTERNAL,
        json=_req("validate_sql", {"sql": "SELECT 1"}, tenant_key="ghost", user_id=uid),
    )
    assert r.status_code == 404


async def test_unbound_user_403(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    uid = await make_user("tc_unbound")  # 未绑租户
    r = await client.post(
        "/internal/tool/execute",
        headers=INTERNAL,
        json=_req("validate_sql", {"sql": "SELECT 1"}, user_id=uid),
    )
    assert r.status_code == 403


async def test_user_id_required(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """工具执行无系统调用豁免(spec):user_id 缺失 → 传输层 422。"""
    r = await client.post(
        "/internal/tool/execute",
        headers=INTERNAL,
        json=_req("validate_sql", {"sql": "SELECT 1"}),
    )
    assert r.status_code == 422


# ============ L2 工具权限点 / L4 JSON Schema / L5 限流与审计 ============


async def test_unregistered_tool_rejected_audited(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    uid = await _user_with_tools(session, make_user, "tc_unreg", ["tool:sql:validate"])
    req = _req("drop_table", {}, user_id=uid)
    r = await client.post("/internal/tool/execute", headers=INTERNAL, json=req)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "rejected"
    assert "TOOL_NOT_REGISTERED" in (body["reason"] or "")
    audits = await _tool_audits(session, str(req["trace_id"]))
    assert len(audits) == 1
    assert audits[0]["status"] == "REJECTED"
    assert audits[0]["tool_name"] == "drop_table"


async def test_perm_denied_rejected(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    # 角色只授 tool:sql:validate,调 execute_sql 越权
    uid = await _user_with_tools(session, make_user, "tc_noperm", ["tool:sql:validate"])
    req = _req("execute_sql", {"sql": "SELECT 1"}, user_id=uid)
    r = await client.post("/internal/tool/execute", headers=INTERNAL, json=req)
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"
    assert "TOOL_PERMISSION_DENIED" in (r.json()["reason"] or "")
    audits = await _tool_audits(session, str(req["trace_id"]))
    assert [a["status"] for a in audits] == ["REJECTED"]


async def test_args_schema_invalid(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    uid = await _user_with_tools(session, make_user, "tc_args", ["tool:sql:validate"])
    # 缺必填 sql
    r1 = await client.post(
        "/internal/tool/execute", headers=INTERNAL, json=_req("validate_sql", {}, user_id=uid)
    )
    assert r1.json()["status"] == "rejected"
    assert "ARGS_INVALID" in (r1.json()["reason"] or "")
    # 多余字段(additionalProperties: false)
    r2 = await client.post(
        "/internal/tool/execute",
        headers=INTERNAL,
        json=_req("validate_sql", {"sql": "SELECT 1", "evil": 1}, user_id=uid),
    )
    assert r2.json()["status"] == "rejected"
    # 参数类型错
    r3 = await client.post(
        "/internal/tool/execute",
        headers=INTERNAL,
        json=_req("validate_sql", {"sql": 42}, user_id=uid),
    )
    assert r3.json()["status"] == "rejected"


async def test_sql_tool_semantic_precheck(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """验证 SQL 工具在 schema 校验前拒绝以非只读关键字开头的语句。"""
    uid = await _user_with_tools(session, make_user, "tc_ddl", ["tool:sql:execute"])
    req = _req("execute_sql", {"sql": "DROP TABLE goods"}, user_id=uid)
    r = await client.post("/internal/tool/execute", headers=INTERNAL, json=req)
    assert r.json()["status"] == "rejected"
    assert "ARGS_INVALID" in (r.json()["reason"] or "")
    # 审计在,且不产生 sys_sql_audit(未进入执行通路)
    audits = await _tool_audits(session, str(req["trace_id"]))
    assert [a["status"] for a in audits] == ["REJECTED"]
    n = (
        await session.execute(
            text("SELECT count(*) FROM public.sys_sql_audit WHERE trace_id=:t"),
            {"t": str(req["trace_id"])},
        )
    ).scalar_one()
    assert n == 0


async def test_rate_limited(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "tool_rate_limit_per_minute", 2)
    uid = await _user_with_tools(session, make_user, "tc_rl", ["tool:sql:validate"])
    for i in range(2):
        r = await client.post(
            "/internal/tool/execute",
            headers=INTERNAL,
            json=_req("validate_sql", {"sql": "SELECT 1"}, user_id=uid),
        )
        assert r.json()["status"] == "executed", f"第{i + 1}次应放行"
    req = _req("validate_sql", {"sql": "SELECT 1"}, user_id=uid)
    r3 = await client.post("/internal/tool/execute", headers=INTERNAL, json=req)
    assert r3.json()["status"] == "rate_limited"
    audits = await _tool_audits(session, str(req["trace_id"]))
    assert [a["status"] for a in audits] == ["RATE_LIMITED"]


async def test_audit_full_fields_and_digest_mask(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    uid = await _user_with_tools(session, make_user, "tc_mask", ["tool:sql:validate"])
    req = _req("validate_sql", {"sql": "SELECT 1"}, user_id=uid)
    r = await client.post("/internal/tool/execute", headers=INTERNAL, json=req)
    assert r.json()["status"] == "executed"
    audits = await _tool_audits(session, str(req["trace_id"]))
    assert len(audits) == 1
    a = audits[0]
    assert a["status"] == "EXECUTED"
    assert a["tool_name"] == "validate_sql"
    assert "SELECT 1" in str(a["args_digest"])
    assert a["user_id"] is not None and a["tenant_id"] is not None
    assert a["duration_ms"] is not None


def test_args_digest_masks_sensitive_keys() -> None:
    """参数摘要脱敏纯函数:password/secret/token 等键值永不进审计。"""
    from kongming.toolcall.models import args_digest

    d = args_digest({"sql": "SELECT 1", "password": "s3cret", "api_key": "k"})
    assert "s3cret" not in d and '"***"' in d
    assert "SELECT 1" in d
    long = args_digest({"sql": "x" * 1000})
    assert len(long) == 500


# ============ 内置工具 handler ============


async def test_validate_sql_handler(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    uid = await _user_with_tools(session, make_user, "tc_val", ["tool:sql:validate"])
    ok = await client.post(
        "/internal/tool/execute",
        headers=INTERNAL,
        json=_req("validate_sql", {"sql": "SELECT id FROM goods"}, user_id=uid),
    )
    assert ok.json()["status"] == "executed"
    assert ok.json()["result"]["passed"] is True
    bad = await client.post(
        "/internal/tool/execute",
        headers=INTERNAL,
        json=_req("validate_sql", {"sql": "DELETE FROM goods"}, user_id=uid),
    )
    # DELETE 首关键字过不了工具层语义预检(参数校验层拒绝)
    assert bad.json()["status"] == "rejected"


async def test_compute_handlers_pure_math(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """compute_delta/compute_share 控制面纯计算 handler(工作流等通道)。"""
    uid = await _user_with_tools(session, make_user, "tc_compute", ["tool:compute"])
    ok = await client.post(
        "/internal/tool/execute",
        headers=INTERNAL,
        json=_req("compute_delta", {"base": 80, "target": 100}, user_id=uid),
    )
    assert ok.json()["status"] == "executed"
    assert ok.json()["result"]["delta_pct"] == 25.0
    share = await client.post(
        "/internal/tool/execute",
        headers=INTERNAL,
        json=_req("compute_share", {"part": 25, "total": 100}, user_id=uid),
    )
    assert share.json()["status"] == "executed"
    assert share.json()["result"]["share_pct"] == 25.0
    zero = await client.post(
        "/internal/tool/execute",
        headers=INTERNAL,
        json=_req("compute_delta", {"base": 0, "target": 1}, user_id=uid),
    )
    assert zero.json()["result"]["status"] == "error"


async def test_execute_sql_handler_pass(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    # ALL 角色:data_scope 不改写,命中租户 schema 真数据
    uid_all = await _user_with_tools(session, make_user, "tc_exec", ["tool:sql:execute"])
    await session.execute(
        text("UPDATE public.sys_role SET data_scope='ALL' WHERE role_key='role_tc_exec'")
    )
    await session.commit()
    req = _req("execute_sql", {"sql": "SELECT name FROM goods"}, user_id=uid_all)
    r = await client.post("/internal/tool/execute", headers=INTERNAL, json=req)
    body = r.json()
    assert body["status"] == "executed"
    assert body["result"]["verdict"] == "PASS"
    assert body["result"]["rows"] == [["连弩"]]
    # 执行路由审计同 trace_id 贯穿(sys_sql_audit 同 trace 落 PASS)
    n = (
        await session.execute(
            text("SELECT count(*) FROM public.sys_sql_audit WHERE trace_id=:t"),
            {"t": str(req["trace_id"])},
        )
    ).scalar_one()
    assert n == 1


async def test_execute_sql_datascope_not_bypassed(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """SELF 角色经工具链查询:data_scope 改写照常生效(零行,本人行不存在)——
    工具通路不绕过行级数据权限(spec「SQL 执行工具保留数据源隔离」同口径)。"""
    uid = await _user_with_tools(session, make_user, "tc_self", ["tool:sql:execute"])
    r = await client.post(
        "/internal/tool/execute",
        headers=INTERNAL,
        json=_req("execute_sql", {"sql": "SELECT name FROM goods"}, user_id=uid),
    )
    body = r.json()
    assert body["status"] == "executed"
    assert body["result"]["verdict"] == "PASS"
    assert body["result"]["rows"] == []  # SELF 改写:create_by=本人,无命中


async def test_execute_sql_firewall_still_blocks(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """工具层语义预检放行的 SELECT 形态,执行路由复检仍拦保留表(spec:
    SQL 工具只读锁定——防御层独立兜底)。"""
    uid = await _user_with_tools(session, make_user, "tc_fw", ["tool:sql:execute"])
    r = await client.post(
        "/internal/tool/execute",
        headers=INTERNAL,
        json=_req(
            "execute_sql",
            {"sql": "SELECT id FROM public.sys_user"},  # 保留表:初检放行,复检拦
            user_id=uid,
        ),
    )
    body = r.json()
    assert body["status"] == "executed"
    assert body["result"]["verdict"] == "BLOCKED"
    assert "RESERVED_TABLE" in (body["result"]["reason"] or "")


async def test_rag_retrieve_handler(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    uid = await _user_with_tools(session, make_user, "tc_rag", ["tool:rag:retrieve"])
    r = await client.post(
        "/internal/tool/execute",
        headers=INTERNAL,
        json=_req("rag_retrieve", {"question": "有多少商品"}, user_id=uid),
    )
    body = r.json()
    assert body["status"] == "executed"
    # 知识库未命中时应回退实时内省，并将 goods 表加入结构提示。
    assert "goods" in body["result"]["schema_hint"]
    assert "history_sql" in body["result"]


# ============ 写工具确认流 ============


async def _mk_session(s: AsyncSession, user_id: str, tenant_key: str = "tc") -> str:
    tid = (
        await s.execute(
            text("SELECT id FROM public.sys_tenant WHERE tenant_key=:k"),
            {"k": tenant_key},
        )
    ).scalar_one()
    sid = (
        await s.execute(
            text(
                "INSERT INTO public.chat_session (id, user_id, tenant_id, title, datasource_key)"
                " VALUES (gen_random_uuid(), :u, :t, 't', 'default') RETURNING id"
            ),
            {"u": user_id, "t": str(tid)},
        )
    ).scalar_one()
    await s.commit()
    return str(sid)


async def _pending_confirm(client: AsyncClient, s: AsyncSession, user_id: str) -> tuple[str, str]:
    """经内部端点挂起一条公告写工具,返回 (confirm_id, trace_id)。"""
    req = _req(
        "create_system_announcement",
        {"title": "明日维护", "content": "明晚 22:00 停机维护"},
        user_id=user_id,
    )
    r = await client.post("/internal/tool/execute", headers=INTERNAL, json=req)
    body = r.json()
    assert body["status"] == "confirm_required"
    assert body["confirm_id"]
    return str(body["confirm_id"]), str(req["trace_id"])


async def test_write_tool_pending_confirm_no_side_effect(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    uid = await _user_with_tools(
        session, make_user, "tc_ann", ["tool:announcement:create", "chat:execute"]
    )
    confirm_id, trace_id = await _pending_confirm(client, session, uid)
    audits = await _tool_audits(session, trace_id)
    assert [a["status"] for a in audits] == ["PENDING_CONFIRM"]
    assert str(audits[0]["id"]) == confirm_id
    # cast 收口 args_json(object→映射,运行时不变)
    assert cast("dict[str, Any]", audits[0]["args_json"])["title"] == "明日维护"
    assert audits[0]["expires_at"] is not None
    n = (await session.execute(text("SELECT count(*) FROM public.sys_announcement"))).scalar_one()
    assert n == 0  # 未确认零副作用


async def test_confirm_executes_with_receipt(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    uid = await _user_with_tools(
        session, make_user, "tc_ok", ["tool:announcement:create", "chat:execute"]
    )
    sid = await _mk_session(session, uid)
    confirm_id, _ = await _pending_confirm(client, session, uid)
    token = _login(client)
    r = await client.post(
        "/chat/tool-confirm",
        headers=_auth(token),
        json={"confirm_id": confirm_id, "decision": "confirm", "session_id": sid},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "executed"
    # 公告落库
    ann = (
        (await session.execute(text("SELECT title, content FROM public.sys_announcement")))
        .mappings()
        .all()
    )
    assert len(ann) == 1
    assert ann[0]["title"] == "明日维护"
    # 审计状态推进
    row = (
        await session.execute(
            text("SELECT status FROM public.sys_tool_audit WHERE id=:i"), {"i": confirm_id}
        )
    ).scalar_one()
    assert row == "EXECUTED"
    # 回执消息落会话
    msgs = (
        await session.execute(
            text(
                "SELECT role, status FROM public.chat_message"
                " WHERE session_id=:s ORDER BY created_at"
            ),
            {"s": sid},
        )
    ).all()
    assert any(m.role == "ASSISTANT" for m in msgs)


async def test_confirm_reject(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    uid = await _user_with_tools(
        session, make_user, "tc_no", ["tool:announcement:create", "chat:execute"]
    )
    sid = await _mk_session(session, uid)
    confirm_id, _ = await _pending_confirm(client, session, uid)
    token = _login(client)
    r = await client.post(
        "/chat/tool-confirm",
        headers=_auth(token),
        json={"confirm_id": confirm_id, "decision": "reject", "session_id": sid},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "confirm_rejected"
    n = (await session.execute(text("SELECT count(*) FROM public.sys_announcement"))).scalar_one()
    assert n == 0
    row = (
        await session.execute(
            text("SELECT status FROM public.sys_tool_audit WHERE id=:i"), {"i": confirm_id}
        )
    ).scalar_one()
    assert row == "CONFIRM_REJECTED"


async def test_confirm_other_user_404(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    uid_a = await _user_with_tools(
        session, make_user, "tc_a", ["tool:announcement:create", "chat:execute"]
    )
    uid_b = await _user_with_tools(
        session, make_user, "tc_b", ["tool:announcement:create", "chat:execute"]
    )
    sid_b = await _mk_session(session, uid_b)
    confirm_id, _ = await _pending_confirm(client, session, uid_a)
    token_b = _login(client)
    r = await client.post(
        "/chat/tool-confirm",
        headers=_auth(token_b),
        json={"confirm_id": confirm_id, "decision": "confirm", "session_id": sid_b},
    )
    assert r.status_code == 404
    n = (await session.execute(text("SELECT count(*) FROM public.sys_announcement"))).scalar_one()
    assert n == 0


async def test_confirm_expired(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    uid = await _user_with_tools(
        session, make_user, "tc_exp", ["tool:announcement:create", "chat:execute"]
    )
    sid = await _mk_session(session, uid)
    confirm_id, _ = await _pending_confirm(client, session, uid)
    await session.execute(
        text("UPDATE public.sys_tool_audit SET expires_at = now() - interval '1 min' WHERE id=:i"),
        {"i": confirm_id},
    )
    await session.commit()
    token = _login(client)
    r = await client.post(
        "/chat/tool-confirm",
        headers=_auth(token),
        json={"confirm_id": confirm_id, "decision": "confirm", "session_id": sid},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "confirm_expired"
    n = (await session.execute(text("SELECT count(*) FROM public.sys_announcement"))).scalar_one()
    assert n == 0


async def test_confirm_idempotent_replay(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    uid = await _user_with_tools(
        session, make_user, "tc_idem", ["tool:announcement:create", "chat:execute"]
    )
    sid = await _mk_session(session, uid)
    confirm_id, _ = await _pending_confirm(client, session, uid)
    token = _login(client)
    for _ in range(2):
        r = await client.post(
            "/chat/tool-confirm",
            headers=_auth(token),
            json={"confirm_id": confirm_id, "decision": "confirm", "session_id": sid},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "executed"
    n = (await session.execute(text("SELECT count(*) FROM public.sys_announcement"))).scalar_one()
    assert n == 1  # 重复确认不产生重复写


async def test_task_resolve_confirm_approve_idempotent_receipt(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """任务通道确认兑现(graph-checkpoint spec):批准=全量复校后执行;
    重复兑换(步骤重放)幂等不重复执行,且反查回填首次执行结果(
    同口径——恢复重放缺结果会诱发模型再次发起同一动作)。"""
    uid = await _user_with_tools(
        session, make_user, "tc_task_c", ["tool:announcement:create", "chat:execute"]
    )
    confirm_id, _ = await _pending_confirm(client, session, uid)
    payload = {
        "tenant_key": "tc",
        "user_id": uid,
        "confirm_id": confirm_id,
        "decision": "approve",
    }
    r = await client.post("/internal/tool/resolve-confirm", headers=INTERNAL, json=payload)
    assert r.status_code == 200
    assert r.json()["status"] == "executed"
    n = (await session.execute(text("SELECT count(*) FROM public.sys_announcement"))).scalar_one()
    assert n == 1
    r2 = await client.post("/internal/tool/resolve-confirm", headers=INTERNAL, json=payload)
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["status"] == "executed"
    # 幂等反查:首次执行的公告 id 回填,任务侧重放不致当作未办再次发起
    assert body2["result"] and body2["result"]["announcement_id"]
    n = (await session.execute(text("SELECT count(*) FROM public.sys_announcement"))).scalar_one()
    assert n == 1


async def test_task_resolve_confirm_reject_and_foreign_forbidden(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """拒绝=CONFIRM_REJECTED 零执行;他人 confirm_id=REJECTED 同形拒绝
    (归属拒绝落审计,不触碰原挂起行)。"""
    uid = await _user_with_tools(
        session, make_user, "tc_task_r", ["tool:announcement:create", "chat:execute"]
    )
    uid_b = await _user_with_tools(session, make_user, "tc_task_rb", ["chat:execute"])
    confirm_id, _ = await _pending_confirm(client, session, uid)
    r = await client.post(
        "/internal/tool/resolve-confirm",
        headers=INTERNAL,
        json={
            "tenant_key": "tc",
            "user_id": uid_b,
            "confirm_id": confirm_id,
            "decision": "approve",
        },
    )
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"
    assert r.json()["reason"] == "CONFIRM_FORBIDDEN"
    n = (await session.execute(text("SELECT count(*) FROM public.sys_announcement"))).scalar_one()
    assert n == 0
    # 任务通道审计按 task-confirm 前缀归档(与 chat 通道 confirm- 区分)
    trace = (
        await session.execute(
            text(
                "SELECT trace_id FROM public.sys_tool_audit"
                " WHERE user_id=:u AND error_reason='CONFIRM_FORBIDDEN'"
                " ORDER BY created_at DESC LIMIT 1"
            ),
            {"u": uid_b},
        )
    ).scalar_one()
    assert str(trace).startswith("task-confirm-")
    # 原归属人仍可正常决策(挂起行未被他人尝试触碰)
    r2 = await client.post(
        "/internal/tool/resolve-confirm",
        headers=INTERNAL,
        json={"tenant_key": "tc", "user_id": uid, "confirm_id": confirm_id, "decision": "reject"},
    )
    assert r2.status_code == 200
    assert r2.json()["status"] == "rejected"
    n = (await session.execute(text("SELECT count(*) FROM public.sys_announcement"))).scalar_one()
    assert n == 0


async def test_confirm_perm_revoked_rejected(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """挂起后公告权限被回收:确认时 L2 复校拒执行(spec「权限回收运行时失效」)。

    chat:execute 与公告权限分两个角色绑定,回收只解绑公告角色(否则端点
    require_perm(chat:execute) 先行 403,测不到确认路径的复校)。
    """
    uid = await _user_with_tools(session, make_user, "tc_revoke", ["chat:execute"])
    rid_ann = await _mk_role_with_perms(session, "role_tc_revoke_ann", ["tool:announcement:create"])
    await _bind(session, "sys_user_role", uid, rid_ann)
    sid = await _mk_session(session, uid)
    confirm_id, _ = await _pending_confirm(client, session, uid)
    # 回收公告权限:仅解绑公告角色
    await session.execute(
        text("DELETE FROM public.sys_user_role WHERE user_id=:u AND role_id=:r"),
        {"u": uid, "r": rid_ann},
    )
    await session.commit()
    token = _login(client)
    r = await client.post(
        "/chat/tool-confirm",
        headers=_auth(token),
        json={"confirm_id": confirm_id, "decision": "confirm", "session_id": sid},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"
    n = (await session.execute(text("SELECT count(*) FROM public.sys_announcement"))).scalar_one()
    assert n == 0


# ============ 管理面 ============


async def _admin_token(client: AsyncClient, s: AsyncSession) -> str:
    await seed_all(s, get_settings())
    return _login(get_settings().admin_subject)


async def test_tools_list_admin(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    token = await _admin_token(client, session)
    r = await client.get("/system/tools", headers=_auth(token))
    assert r.status_code == 200
    tools = r.json()["tools"]
    names = {t["name"] for t in tools}
    assert {"validate_sql", "execute_sql", "rag_retrieve", "create_system_announcement"} <= names
    assert {"compute_delta", "compute_share"} <= names
    ann = next(t for t in tools if t["name"] == "create_system_announcement")
    assert ann["write"] is True and ann["require_confirm"] is True
    assert ann["perm_code"] == "tool:announcement:create"


async def test_tools_list_forbidden(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    await seed_all(session, get_settings())
    uid = await make_user("plain_user")
    tid = (
        await session.execute(text("SELECT id FROM public.sys_tenant WHERE tenant_key='default'"))
    ).scalar_one()
    await _bind(session, "sys_user_tenant", uid, str(tid))
    token = _login(client)
    r = await client.get("/system/tools", headers=_auth(token))
    assert r.status_code == 403
    r2 = await client.get("/system/tools/audit/page", headers=_auth(token))
    assert r2.status_code == 403


async def test_audit_page_filters(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    token = await _admin_token(client, session)
    admin_id = (
        await session.execute(
            text("SELECT id FROM public.sys_user WHERE id=:s"),
            {"s": get_settings().admin_subject},
        )
    ).scalar_one()
    # 造两条审计:一条 validate_sql EXECUTED,一条未注册 REJECTED
    await client.post(
        "/internal/tool/execute",
        headers=INTERNAL,
        json=_req("validate_sql", {"sql": "SELECT 1"}, user_id=str(admin_id), tenant_key="default"),
    )
    await client.post(
        "/internal/tool/execute",
        headers=INTERNAL,
        json=_req("ghost_tool", {}, user_id=str(admin_id), tenant_key="default"),
    )
    r = await client.get("/system/tools/audit/page?page=1&size=10", headers=_auth(token))
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 2
    # 审计响应兼容存量行:内置工具的 domain 为空,域工具带 domain。
    validate_row = next((i for i in body["items"] if i["tool_name"] == "validate_sql"), None)
    assert validate_row is not None and validate_row["domain"] is None
    # 按工具名筛选
    r2 = await client.get("/system/tools/audit/page?tool_name=validate_sql", headers=_auth(token))
    assert all(it["tool_name"] == "validate_sql" for it in r2.json()["items"])
    # 按状态筛选
    r3 = await client.get("/system/tools/audit/page?status=REJECTED", headers=_auth(token))
    assert all(it["status"] == "REJECTED" for it in r3.json()["items"])
    assert len(r3.json()["items"]) >= 1


async def test_audit_page_row_visibility(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """行级可见性:带 system:tool:manage 但 data_scope 非 ALL 的角色只见本人。"""
    await seed_all(session, get_settings())
    # 两个普通用户各产生一条审计
    uid_x = await _user_with_tools(session, make_user, "tc_vx", ["tool:sql:validate"])
    uid_y = await _user_with_tools(session, make_user, "tc_vy", ["tool:sql:validate"])
    for u in (uid_x, uid_y):
        await client.post(
            "/internal/tool/execute",
            headers=INTERNAL,
            json=_req("validate_sql", {"sql": "SELECT 1"}, user_id=u),
        )
    # 给 X 授带管理权限点的 SELF 角色
    rid = await _mk_role_with_perms(session, "role_tc_vx_mgr", ["system:tool:manage"])
    await _bind(session, "sys_user_role", uid_x, rid)
    token_x = _login(uid_x)
    r = await client.get("/system/tools/audit/page?page=1&size=50", headers=_auth(token_x))
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) >= 1
    assert all(str(it["user_id"]) == uid_x for it in items)


# ============ 参数校验、权限检查与并发调用 ============


async def test_confirm_other_user_404_audited(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """他人确认被拒落审计(REJECTED/CONFIRM_FORBIDDEN,尝试者维度新行,
    不触碰原挂起行)。"""
    uid_a = await _user_with_tools(
        session, make_user, "tc_fa", ["tool:announcement:create", "chat:execute"]
    )
    uid_b = await _user_with_tools(
        session, make_user, "tc_fb", ["tool:announcement:create", "chat:execute"]
    )
    sid_b = await _mk_session(session, uid_b)
    confirm_id, _trace_a = await _pending_confirm(client, session, uid_a)
    token_b = _login(client)
    r = await client.post(
        "/chat/tool-confirm",
        headers=_auth(token_b),
        json={"confirm_id": confirm_id, "decision": "confirm", "session_id": sid_b},
    )
    assert r.status_code == 404
    # 尝试者维度 REJECTED 审计(CONFIRM_FORBIDDEN)
    row = (
        await session.execute(
            text(
                "SELECT status, error_reason FROM public.sys_tool_audit"
                " WHERE user_id=:u AND tool_name='create_system_announcement'"
                " ORDER BY created_at DESC LIMIT 1"
            ),
            {"u": uid_b},
        )
    ).one()
    assert row.status == "REJECTED" and row.error_reason == "CONFIRM_FORBIDDEN"
    # 原挂起行不受影响
    orig = (
        await session.execute(
            text("SELECT status FROM public.sys_tool_audit WHERE id=:i"), {"i": confirm_id}
        )
    ).scalar_one()
    assert orig == "PENDING_CONFIRM"


async def test_confirm_after_unbind_tenant_403(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """挂起后用户被解绑租户,确认时 L1 复校拒(403),公告不写。"""
    uid = await _user_with_tools(
        session, make_user, "tc_unbind", ["tool:announcement:create", "chat:execute"]
    )
    sid = await _mk_session(session, uid)
    confirm_id, _ = await _pending_confirm(client, session, uid)
    # 解绑租户(chat_session 历史归属仍在——复校必须查 sys_user_tenant 当前绑定)
    await session.execute(text("DELETE FROM public.sys_user_tenant WHERE user_id=:u"), {"u": uid})
    await session.commit()
    token = _login(client)
    r = await client.post(
        "/chat/tool-confirm",
        headers=_auth(token),
        json={"confirm_id": confirm_id, "decision": "confirm", "session_id": sid},
    )
    assert r.status_code == 403
    n = (await session.execute(text("SELECT count(*) FROM public.sys_announcement"))).scalar_one()
    assert n == 0


async def test_confirm_idempotent_returns_first_result(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """重复确认幂等回放返回首次执行结果(announcement_id 一致,created=false)。"""
    uid = await _user_with_tools(
        session, make_user, "tc_first", ["tool:announcement:create", "chat:execute"]
    )
    sid = await _mk_session(session, uid)
    confirm_id, _ = await _pending_confirm(client, session, uid)
    token = _login(client)
    r1 = await client.post(
        "/chat/tool-confirm",
        headers=_auth(token),
        json={"confirm_id": confirm_id, "decision": "confirm", "session_id": sid},
    )
    first = r1.json()["result"]["announcement_id"]
    r2 = await client.post(
        "/chat/tool-confirm",
        headers=_auth(token),
        json={"confirm_id": confirm_id, "decision": "confirm", "session_id": sid},
    )
    body2 = r2.json()
    assert body2["status"] == "executed"
    assert body2["result"]["announcement_id"] == first
    assert body2["result"]["created"] is False
    # EXECUTED 行不残留挂起期文案
    reason = (
        await session.execute(
            text("SELECT error_reason FROM public.sys_tool_audit WHERE id=:i"),
            {"i": confirm_id},
        )
    ).scalar_one()
    assert reason is None


async def test_validate_args_comment_prefix_accepted(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """注释前缀的合法 SELECT 不再被参数预检误拒(AST 根判定)。"""
    uid = await _user_with_tools(session, make_user, "tc_comment", ["tool:sql:validate"])
    r = await client.post(
        "/internal/tool/execute",
        headers=INTERNAL,
        json=_req("validate_sql", {"sql": "-- 统计注释\nSELECT 1"}, user_id=uid),
    )
    assert r.json()["status"] == "executed"
    assert r.json()["result"]["passed"] is True


async def test_confirm_concurrent_double_submit(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """真并发双确认(asyncio.gather):行锁串行,公告恰 1 行,两响应均 200。"""
    import asyncio

    uid = await _user_with_tools(
        session, make_user, "tc_race", ["tool:announcement:create", "chat:execute"]
    )
    sid = await _mk_session(session, uid)
    confirm_id, _ = await _pending_confirm(client, session, uid)
    token = _login(client)
    payload = {"confirm_id": confirm_id, "decision": "confirm", "session_id": sid}
    r1, r2 = await asyncio.gather(
        client.post("/chat/tool-confirm", headers=_auth(token), json=payload),
        client.post("/chat/tool-confirm", headers=_auth(token), json=payload),
    )
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["status"] == "executed" and r2.json()["status"] == "executed"
    n = (await session.execute(text("SELECT count(*) FROM public.sys_announcement"))).scalar_one()
    assert n == 1


def test_validate_args_recursion_and_multistmt() -> None:
    """深嵌套括号和多语句 SQL 在参数校验阶段都不得绕过拒绝处理。"""
    from kongming.toolcall.models import ToolDef
    from kongming.toolcall.registry import get_tool
    from kongming.toolcall.service import validate_args

    tool = get_tool("execute_sql")
    assert isinstance(tool, ToolDef)
    deep = "SELECT " + "(" * 3000 + "1" + ")" * 3000
    # 不抛 RecursionError 即可(畸形放行下游防火墙,或拒绝——都收敛)
    out = validate_args(tool, {"sql": deep})
    assert out is None or "ARGS_INVALID" in out
    multi = validate_args(tool, {"sql": "SELECT 1; DROP TABLE x"})
    assert multi is not None and "单条" in multi


# ============ 已退役工单工具零出站回归 ============


@pytest.mark.parametrize("tool_name", ["ticket_search", "ticket_get", "ticket_create"])
async def test_retired_ticket_tool_direct_call_rejected_zero_outbound(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser, tool_name: str
) -> None:
    """遗留工单工具名直接调用:未注册即拒(TOOL_NOT_REGISTERED)并落审计;
    工具不在注册表=无执行器可派发,零出站(structural)。"""
    uid = await _user_with_tools(session, make_user, f"tc_rt_{tool_name}", ["chat:execute"])
    args: dict[str, object] = {"query": "报表"} if tool_name == "ticket_search" else {"id": "T-1"}
    req = _req(tool_name, args, user_id=uid)
    r = await client.post("/internal/tool/execute", headers=INTERNAL, json=req)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "rejected"
    assert "TOOL_NOT_REGISTERED" in (body["reason"] or "")
    audits = await _tool_audits(session, str(req["trace_id"]))
    assert [a["status"] for a in audits] == ["REJECTED"]
    assert audits[0]["tool_name"] == tool_name


async def test_retired_ticket_create_pending_confirm_rejected_zero_outbound(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """存量待确认 ticket_create 行(旧版本挂起):确认时工具解析为 None →
    权限复校拒(TOOL_PERMISSION_DENIED),行推进 REJECTED,零出站。"""
    from datetime import UTC, datetime, timedelta

    uid = await _user_with_tools(session, make_user, "tc_rt_confirm", ["chat:execute"])
    sid = await _mk_session(session, uid)
    tid = (
        await session.execute(
            text("SELECT id FROM public.sys_tenant WHERE tenant_key='tc'"),
        )
    ).scalar_one()
    confirm_id = str(uuid.uuid4())
    await session.execute(
        text(
            "INSERT INTO public.sys_tool_audit"
            " (id, trace_id, user_id, tenant_id, tool_name, args_digest, args_json,"
            "  status, duration_ms, datasource_key, channel, expires_at)"
            " VALUES (:id, :trace, :u, :t, 'ticket_create', :digest,"
            "  CAST(:args AS jsonb), 'PENDING_CONFIRM', 0, 'default', 'chat', :exp)"
        ),
        {
            "id": confirm_id,
            "trace": f"tr-legacy-{uuid.uuid4().hex[:8]}",
            "u": uid,
            "t": str(tid),
            "digest": "legacy",
            "args": json.dumps(
                {"title": "遗留工单", "description": "旧版本挂起", "priority": "high"}
            ),
            "exp": datetime.now(UTC) + timedelta(minutes=30),
        },
    )
    await session.commit()
    token = _login(client)
    r = await client.post(
        "/chat/tool-confirm",
        headers=_auth(token),
        json={"confirm_id": confirm_id, "decision": "confirm", "session_id": sid},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"
    assert "TOOL_PERMISSION_DENIED" in (r.json()["reason"] or "")
    row = (
        await session.execute(
            text("SELECT status FROM public.sys_tool_audit WHERE id=:i"), {"i": confirm_id}
        )
    ).scalar_one()
    assert row == "REJECTED"
