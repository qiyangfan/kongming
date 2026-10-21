"""覆盖内部 SQL 执行接口的权限、租户隔离和防护规则。"""
# pyright: reportPrivateUsage=false

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.db import get_engine

MakeUser = Callable[..., Awaitable[str]]

INTERNAL = {"X-Internal-Token": "test-internal-token"}
SCHEMA_A = "tenant_ita"
SCHEMA_B = "tenant_itb"


@pytest.fixture(scope="module", autouse=True)
async def _biz() -> AsyncIterator[None]:  # pyright: ignore[reportUnusedFunction]
    """两租户同名表不同数据:search_path 路由隔离的活证。"""
    async with get_engine().begin() as conn:
        for schema, mark in ((SCHEMA_A, "A"), (SCHEMA_B, "B")):
            await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
            await conn.execute(
                text(
                    f'CREATE TABLE IF NOT EXISTS "{schema}".goods ('
                    " id uuid PRIMARY KEY, mark varchar(8), create_by varchar(64))"
                )
            )
            await conn.execute(text(f'DELETE FROM "{schema}".goods'))  # noqa: S608  # 模块常量标识符
            insert_sql = (
                f'INSERT INTO "{schema}".goods (id, mark, create_by)'  # noqa: S608  # 模块常量
                f" VALUES (gen_random_uuid(), '{mark}', 'someone')"
            )
            for _ in range(5):
                await conn.execute(text(insert_sql))
    yield
    async with get_engine().begin() as conn:
        for schema in (SCHEMA_A, SCHEMA_B):
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))


async def _mk_tenant(s: AsyncSession, key: str, status: str = "ACTIVE") -> str:
    tid = (
        await s.execute(
            text(
                "INSERT INTO public.sys_tenant (id, tenant_key, tenant_name, status)"
                " VALUES (gen_random_uuid(), :k, :k, :st) RETURNING id"
            ),
            {"k": key, "st": status},
        )
    ).scalar_one()
    await s.commit()
    return str(tid)


async def _bind_tenant(s: AsyncSession, user_id: str, tenant_id: str) -> None:
    await s.execute(
        text("INSERT INTO public.sys_user_tenant (user_id, tenant_id) VALUES (:u, :t)"),
        {"u": user_id, "t": tenant_id},
    )
    await s.commit()


async def _mk_role(s: AsyncSession, key: str, scope: str) -> str:
    rid = (
        await s.execute(
            text(
                "INSERT INTO public.sys_role (id, role_key, role_name, data_scope)"
                " VALUES (gen_random_uuid(), :k, :k, :ds) RETURNING id"
            ),
            {"k": key, "ds": scope},
        )
    ).scalar_one()
    await s.commit()
    return str(rid)


async def _bind_role(s: AsyncSession, user_id: str, role_id: str) -> None:
    await s.execute(
        text("INSERT INTO public.sys_user_role (user_id, role_id) VALUES (:u, :r)"),
        {"u": user_id, "r": role_id},
    )
    await s.commit()


async def _mk_dept(s: AsyncSession, tenant_id: str) -> str:
    did = (
        await s.execute(
            text(
                "INSERT INTO public.sys_dept (id, dept_name, tenant_id)"
                " VALUES (gen_random_uuid(), :n, :t) RETURNING id"
            ),
            {"n": f"d-{uuid.uuid4().hex[:6]}", "t": tenant_id},
        )
    ).scalar_one()
    await s.commit()
    return str(did)


async def _set_role_depts(s: AsyncSession, role_id: str, dept_ids: list[str]) -> None:
    for d in dept_ids:
        await s.execute(
            text("INSERT INTO public.sys_role_dept (role_id, dept_id) VALUES (:r, :d)"),
            {"r": role_id, "d": d},
        )
    await s.commit()


async def _audits(s: AsyncSession, trace_id: str) -> list[dict[str, Any]]:
    rows = (
        (
            await s.execute(
                text("SELECT * FROM public.sys_sql_audit WHERE trace_id = :t"),
                {"t": trace_id},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


def _payload(tenant_key: str, sql: str, **kw: object) -> dict[str, Any]:
    base: dict[str, Any] = {
        "trace_id": f"tr-{uuid.uuid4().hex[:12]}",
        "tenant_key": tenant_key,
        "sql_text": sql,
    }
    base.update(kw)
    return base


# ============ 请求契约:fail-closed 拒绝面 ============


async def test_unknown_tenant_rejected(client: AsyncClient, session: AsyncSession) -> None:
    resp = await client.post(
        "/internal/sql/execute", json=_payload("ghost", "SELECT 1"), headers=INTERNAL
    )
    assert resp.status_code == 404
    # 请求本身非法(无可审计主体):全表零审计行(按库表级断言,非按 trace_id 恒真断言)
    count = (await session.execute(text("SELECT COUNT(*) FROM public.sys_sql_audit"))).scalar_one()
    assert count == 0


async def test_disabled_tenant_rejected(client: AsyncClient, session: AsyncSession) -> None:
    await _mk_tenant(session, "ita-off", status="DISABLED")
    resp = await client.post(
        "/internal/sql/execute", json=_payload("ita-off", "SELECT 1"), headers=INTERNAL
    )
    assert resp.status_code == 404


async def test_user_not_bound_tenant_forbidden(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    uid = await make_user("exec-nobind")
    await _mk_tenant(session, "ita")
    resp = await client.post(
        "/internal/sql/execute",
        json=_payload("ita", "SELECT 1", user_id=uid),
        headers=INTERNAL,
    )
    assert resp.status_code == 403


async def test_non_default_datasource_rejected(client: AsyncClient, session: AsyncSession) -> None:
    await _mk_tenant(session, "ita")
    resp = await client.post(
        "/internal/sql/execute",
        json=_payload("ita", "SELECT 1", datasource_key="sakila"),
        headers=INTERNAL,
    )
    # 动态池未落地前 fail-closed(不静默回退默认路径)
    assert resp.status_code == 400


async def test_internal_token_required(client: AsyncClient) -> None:
    resp = await client.post("/internal/sql/execute", json=_payload("ita", "SELECT 1"))
    assert resp.status_code == 403


# ============ 执行面:路由/只读/截断 ============


async def test_system_call_executes_in_tenant_schema(
    client: AsyncClient, session: AsyncSession
) -> None:
    """系统调用(无 user_id)豁免绑定复校与 data_scope;search_path 路由到租户 schema。"""
    await _mk_tenant(session, "ita")
    resp = await client.post(
        "/internal/sql/execute",
        json=_payload("ita", "SELECT mark FROM goods ORDER BY mark", question="系统调用"),
        headers=INTERNAL,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["verdict"] == "PASS"
    assert body["row_count"] == 5
    assert {r[0] for r in body["rows"]} == {"A"}  # 命中 ita 而非 itb


async def test_schema_isolation_between_tenants(client: AsyncClient, session: AsyncSession) -> None:
    await _mk_tenant(session, "ita")
    await _mk_tenant(session, "itb")
    for key, mark in (("ita", "A"), ("itb", "B")):
        resp = await client.post(
            "/internal/sql/execute",
            json=_payload(key, "SELECT DISTINCT mark FROM goods"),
            headers=INTERNAL,
        )
        assert resp.json()["rows"] == [[mark]]


async def test_row_limit_truncation(client: AsyncClient, session: AsyncSession) -> None:
    await _mk_tenant(session, "ita")
    resp = await client.post(
        "/internal/sql/execute",
        json=_payload("ita", "SELECT mark FROM goods", limit=2),
        headers=INTERNAL,
    )
    body = resp.json()
    assert body["verdict"] == "PASS"
    assert body["row_count"] == 2 and len(body["rows"]) == 2


async def test_connection_readonly(client: AsyncClient, session: AsyncSession) -> None:
    """连接级只读:transaction_read_only=on(spec 只读连接池)。"""
    await _mk_tenant(session, "ita")
    resp = await client.post(
        "/internal/sql/execute",
        json=_payload("ita", "SELECT current_setting('transaction_read_only')"),
        headers=INTERNAL,
    )
    assert resp.json()["rows"] == [["on"]]


# ============ 三态 verdict + 审计落库 ============


async def test_blocked_write_statement_audited(client: AsyncClient, session: AsyncSession) -> None:
    await _mk_tenant(session, "ita")
    p = _payload("ita", "DELETE FROM goods", question="删数据")
    resp = await client.post("/internal/sql/execute", json=p, headers=INTERNAL)
    body = resp.json()
    assert body["verdict"] == "BLOCKED"
    assert "BLOCKED" in (body["reason"] or "")
    rows = await _audits(session, p["trace_id"])
    assert len(rows) == 1
    assert rows[0]["verdict"] == "BLOCKED"
    assert rows[0]["block_reason"] is not None
    assert rows[0]["question"] == "删数据"


async def test_reserved_table_blocked_and_audited(
    client: AsyncClient, session: AsyncSession
) -> None:
    """保留表拒读:sys_user 经 search_path 落 public 可读的纵深缺口。

    系统调用(豁免 data_scope)也拦——保留表判定与用户身份无关。
    """
    await _mk_tenant(session, "ita")
    for sql in (
        "SELECT username, password_hash FROM sys_user",
        "SELECT * FROM public.sys_user",
        "SELECT * FROM chat_message",
    ):
        p = _payload("ita", sql, question="探测")
        resp = await client.post("/internal/sql/execute", json=p, headers=INTERNAL)
        body = resp.json()
        assert body["verdict"] == "BLOCKED", sql
        assert "RESERVED_TABLE" in (body["reason"] or ""), sql
        rows = await _audits(session, p["trace_id"])
        assert len(rows) == 1 and rows[0]["verdict"] == "BLOCKED"
    # 对照:业务表正常放行(保留表判定不误伤)
    p = _payload("ita", "SELECT mark FROM goods")
    resp = await client.post("/internal/sql/execute", json=p, headers=INTERNAL)
    assert resp.json()["verdict"] == "PASS"


async def test_execution_error_audited_with_db_summary(
    client: AsyncClient, session: AsyncSession
) -> None:
    await _mk_tenant(session, "ita")
    p = _payload("ita", "SELECT nope FROM goods")
    resp = await client.post("/internal/sql/execute", json=p, headers=INTERNAL)
    body = resp.json()
    assert body["verdict"] == "ERROR"
    assert "nope" in (body["reason"] or "")  # DB 错误摘要供 selfheal 喂回
    rows = await _audits(session, p["trace_id"])
    assert len(rows) == 1 and rows[0]["verdict"] == "ERROR"
    assert "nope" in (rows[0]["block_reason"] or "")


async def test_pass_audit_full_fields(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    uid = await make_user("exec-pass")
    tid = await _mk_tenant(session, "ita")
    await _bind_tenant(session, uid, tid)
    await _bind_role(session, uid, await _mk_role(session, "IE_ALL", "ALL"))
    p = _payload("ita", "SELECT mark FROM goods", user_id=uid, question="全部商品", limit=3)
    resp = await client.post("/internal/sql/execute", json=p, headers=INTERNAL)
    assert resp.json()["verdict"] == "PASS"
    rows = await _audits(session, p["trace_id"])
    assert len(rows) == 1
    row = rows[0]
    assert str(row["user_id"]) == uid
    assert str(row["tenant_id"]) == tid
    assert row["datasource_key"] == "default"
    assert row["question"] == "全部商品"
    assert row["sql_text"] == "SELECT mark FROM goods"  # ALL 不改写
    assert row["verdict"] == "PASS"
    assert row["row_count"] == 3
    assert row["duration_ms"] is not None


async def test_self_scope_rewritten_sql_audited(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """SELF 改写:审计 sql_text 为改写后形态(可完整回放);行数按过滤收敛。"""
    uid = await make_user("exec-self")
    tid = await _mk_tenant(session, "ita")
    await _bind_tenant(session, uid, tid)
    await _bind_role(session, uid, await _mk_role(session, "IE_SELF", "SELF"))
    # 造一行 create_by=该用户 id 的数据(SELF 过滤值=用户 id,spec 立约)
    async with get_engine().begin() as conn:
        insert_self = (
            f'INSERT INTO "{SCHEMA_A}".goods (id, mark, create_by)'  # noqa: S608  # 模块常量
            " VALUES (gen_random_uuid(), 'A', :u)"
        )
        await conn.execute(text(insert_self), {"u": uid})
    p = _payload("ita", "SELECT mark FROM goods", user_id=uid)
    resp = await client.post("/internal/sql/execute", json=p, headers=INTERNAL)
    body = resp.json()
    assert body["verdict"] == "PASS"
    assert body["row_count"] == 1  # 只剩本人行
    rows = await _audits(session, p["trace_id"])
    assert "create_by" in rows[0]["sql_text"]  # 改写后形态(注入谓词)落库
    assert uid not in rows[0]["sql_text"]  # 值绑定参数,不入文本


async def test_custom_scope_without_depts_fail_closed_blocked(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """CUSTOM 但未配置角色部门集合(历史数据/旁路写入)→ 仍 fail-closed BLOCKED。"""
    uid = await make_user("exec-custom")
    tid = await _mk_tenant(session, "ita")
    await _bind_tenant(session, uid, tid)
    await _bind_role(session, uid, await _mk_role(session, "IE_CUSTOM", "CUSTOM"))
    p = _payload("ita", "SELECT mark FROM goods", user_id=uid)
    resp = await client.post("/internal/sql/execute", json=p, headers=INTERNAL)
    body = resp.json()
    assert body["verdict"] == "BLOCKED"
    assert "DATA_SCOPE" in (body["reason"] or "")
    rows = await _audits(session, p["trace_id"])
    assert len(rows) == 1 and rows[0]["verdict"] == "BLOCKED"
    assert "DATA_SCOPE" in (rows[0]["block_reason"] or "")


async def test_custom_scope_with_depts_filters_by_in(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """CUSTOM 配置部门集合 → 按 dept_id IN (集合) 放行(等价「显式集合的 DEPT IN」)。"""
    uid = await make_user("exec-custom-in")
    tid = await _mk_tenant(session, "ita")
    await _bind_tenant(session, uid, tid)
    rid = await _mk_role(session, "IE_CUSTOM_IN", "CUSTOM")
    await _bind_role(session, uid, rid)
    d1 = await _mk_dept(session, tid)
    d2 = await _mk_dept(session, tid)
    d3 = await _mk_dept(session, tid)
    await _set_role_depts(session, rid, [d1, d2])
    # 专属业务表(不污染 goods 的既有行数断言):3 行落集合内部门,2 行落集合外
    async with get_engine().begin() as conn:
        await conn.execute(
            text(
                f'CREATE TABLE IF NOT EXISTS "{SCHEMA_A}".dept_order ('  # noqa: S608  # 模块常量
                " id uuid PRIMARY KEY, mark varchar(8), dept_id uuid)"
            )
        )
        await conn.execute(text(f'DELETE FROM "{SCHEMA_A}".dept_order'))  # noqa: S608
        for dept in (d1, d1, d2, d3, d3):
            await conn.execute(
                text(
                    f'INSERT INTO "{SCHEMA_A}".dept_order (id, mark, dept_id)'  # noqa: S608
                    " VALUES (gen_random_uuid(), 'x', :d)"
                ),
                {"d": dept},
            )
    p = _payload("ita", "SELECT mark FROM dept_order", user_id=uid)
    resp = await client.post("/internal/sql/execute", json=p, headers=INTERNAL)
    body = resp.json()
    assert body["verdict"] == "PASS", body
    assert body["row_count"] == 3  # 只返回集合内部门的行
    rows = await _audits(session, p["trace_id"])
    assert "dept_id" in rows[0]["sql_text"]  # 改写后形态落审计
    assert d1 not in rows[0]["sql_text"]  # 值走绑定参数,不入 SQL 文本


async def test_custom_scope_no_dept_column_degrades_like_dept(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """CUSTOM 有集合但表无部门列 → 与 DEPT 同源安全降级(不改写、放行、告警可观测)。"""
    uid = await make_user("exec-custom-nc")
    tid = await _mk_tenant(session, "ita")
    await _bind_tenant(session, uid, tid)
    rid = await _mk_role(session, "IE_CUSTOM_NC", "CUSTOM")
    await _bind_role(session, uid, rid)
    await _set_role_depts(session, rid, [await _mk_dept(session, tid)])
    p = _payload(
        "ita", "SELECT mark FROM goods WHERE create_by = 'someone'", user_id=uid
    )  # goods 无 dept_id
    resp = await client.post("/internal/sql/execute", json=p, headers=INTERNAL)
    body = resp.json()
    assert body["verdict"] == "PASS"
    assert body["row_count"] == 5  # 降级为不过滤(沿用上游裁定)
    rows = await _audits(session, p["trace_id"])
    assert rows[0]["sql_text"] == "SELECT mark FROM goods WHERE create_by = 'someone'"


async def test_cross_tenant_schema_reference_blocked(
    client: AsyncClient, session: AsyncSession
) -> None:
    """schema 边界:显式限定他租户 schema 的引用被拒(search_path 管不了限定名)。"""
    await _mk_tenant(session, "ita")
    await _mk_tenant(session, "itb")
    p = _payload("ita", "SELECT COUNT(*) FROM tenant_itb.goods")
    resp = await client.post("/internal/sql/execute", json=p, headers=INTERNAL)
    body = resp.json()
    assert body["verdict"] == "BLOCKED"
    assert "CROSS_SCHEMA" in (body["reason"] or "")
    rows = await _audits(session, p["trace_id"])
    assert len(rows) == 1 and rows[0]["verdict"] == "BLOCKED"


async def test_orchestration_infra_error_becomes_error_verdict(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """数据范围探测异常时，执行结果为 ERROR 并写审计，不返回 500。"""
    from kongming.internal import service as internal_service

    async def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("pool exploded")

    monkeypatch.setattr(internal_service.applier, "apply", _boom)
    uid = await make_user("exec-infra")
    tid = await _mk_tenant(session, "ita")
    await _bind_tenant(session, uid, tid)
    p = _payload("ita", "SELECT mark FROM goods", user_id=uid)
    resp = await client.post("/internal/sql/execute", json=p, headers=INTERNAL)
    assert resp.status_code == 200  # 三态契约不破
    body = resp.json()
    assert body["verdict"] == "ERROR"
    assert "pool exploded" in (body["reason"] or "")
    rows = await _audits(session, p["trace_id"])
    assert len(rows) == 1 and rows[0]["verdict"] == "ERROR"


async def test_rewrite_recheck_blocks_tainted_sql(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """清单 10 后半:改写产物复检——改写被污染(注DDL)时防火墙复检拒执行。"""
    from kongming.datascope import rewrite

    uid = await make_user("exec-taint")
    tid = await _mk_tenant(session, "ita")
    await _bind_tenant(session, uid, tid)
    await _bind_role(session, uid, await _mk_role(session, "IE_TAINT", "SELF"))

    def _taint(statement: object, sql: str, **kw: object) -> tuple[str, dict[str, object]]:
        return ("DROP TABLE goods", {})

    # 管线改写走 Guarded.apply_filter → apply_filter_statement(单遍复用接缝),
    # 污染点对齐新接缝;复检防线(步骤 6)本身不变。
    monkeypatch.setattr(rewrite, "apply_filter_statement", _taint)
    p = _payload("ita", "SELECT mark FROM goods", user_id=uid)
    resp = await client.post("/internal/sql/execute", json=p, headers=INTERNAL)
    body = resp.json()
    assert body["verdict"] == "BLOCKED"
    rows = await _audits(session, p["trace_id"])
    assert len(rows) == 1 and rows[0]["verdict"] == "BLOCKED"
    # 被污染的 SQL 绝不能触库:goods 表仍在
    async with get_engine().connect() as conn:
        alive = (
            await conn.execute(
                text(f'SELECT COUNT(*) FROM "{SCHEMA_A}".goods')  # noqa: S608  # 模块常量
            )
        ).scalar_one()
    assert alive >= 1


def test_json_cell_decimal_normalization() -> None:
    """numeric 列 JSON 数值化(图表/stats 依赖);NaN/Inf 映射 None(JSON 不可表达)。"""
    from decimal import Decimal

    from kongming.internal.service import _json_cell

    assert _json_cell(Decimal("4.99")) == 4.99
    assert isinstance(_json_cell(Decimal("4.99")), float)
    assert _json_cell(Decimal("NaN")) is None
    assert _json_cell(Decimal("Infinity")) is None
    assert _json_cell(Decimal("-Infinity")) is None
    assert _json_cell(7) == 7 and _json_cell("x") == "x"  # 非 Decimal 原样
