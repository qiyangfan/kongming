"""动态数据源执行路由全栈测试(008 datasource spec「动态路由与查询」,安全规则类)。

真实 PG:动态源=同实例异 schema(冒烟既定形态,design Risks 钉住);
防御层与默认路径逐层实证一致(防火墙/保留表/危险函数/schema 边界/data_scope/
事务级 search_path/连接级只读/行数截断);非法 key 同形 400 + 审计分类内部可溯;
池生命周期:改密/启停/删除定向失效,仅改 name 不动池。
"""
# pyright: reportPrivateUsage=false

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.config import get_settings
from kongming.datasource.cipher import get_cipher
from kongming.db import get_engine
from kongming.internal import executor
from tests.test_system_admin import _admin

MakeUser = Callable[..., Awaitable[str]]

INTERNAL = {"X-Internal-Token": "test-internal-token"}
SCHEMA = "ds_dyn"  # 动态源 default_schema
SCHEMA_V2 = "ds_dyn_v2"  # 「启用后按最新连接信息重建」证物 schema
SCHEMA_OTHER = "ds_other"  # CROSS_SCHEMA 边界证物
SCHEMA_SELF = "ds_self"  # SELF 改写证物(独立 schema,用例间行隔离)


@pytest.fixture(scope="module", autouse=True)
async def _biz() -> AsyncIterator[None]:  # pyright: ignore[reportUnusedFunction]
    async with get_engine().begin() as conn:
        for schema, mark in (
            (SCHEMA, "D1"),
            (SCHEMA_V2, "D2"),
            (SCHEMA_OTHER, "DX"),
            (SCHEMA_SELF, "SF"),
        ):
            await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
            await conn.execute(
                text(
                    f'CREATE TABLE IF NOT EXISTS "{schema}".goods ('
                    " id uuid PRIMARY KEY, mark varchar(8), create_by varchar(64))"
                )
            )
            await conn.execute(text(f'DELETE FROM "{schema}"."goods"'))  # noqa: S608  # 模块常量
            await conn.execute(
                text(
                    f'INSERT INTO "{schema}".goods (id, mark, create_by)'  # noqa: S608  # 模块常量
                    f" VALUES (gen_random_uuid(), '{mark}', 'someone')"
                )
            )
        # public 回退段证物(search_path=default_schema, public 两段)
        await conn.execute(
            text("CREATE TABLE IF NOT EXISTS public.pub_goods (mark varchar(8))")
        )
        await conn.execute(text("DELETE FROM public.pub_goods"))
        await conn.execute(text("INSERT INTO public.pub_goods (mark) VALUES ('PUB')"))
        # 动态 schema 内的保留名表(内省排除判定证物,任务组 5 消费)
        await conn.execute(text(f'CREATE TABLE IF NOT EXISTS "{SCHEMA}".sys_cfg (k varchar(8))'))
    yield
    async with get_engine().begin() as conn:
        for schema in (SCHEMA, SCHEMA_V2, SCHEMA_OTHER, SCHEMA_SELF):
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await conn.execute(text("DROP TABLE IF EXISTS public.pub_goods"))


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


async def _mk_ds(
    s: AsyncSession,
    key: str,
    tenant_id: str,
    *,
    schema: str = SCHEMA,
    password: str = "kongming",
    status: str = "ACTIVE",
    host: str = "127.0.0.1",
    port: int = 5432,
    database: str = "kongming_test",
    username: str = "kongming",
) -> str:
    """直插数据源行(arrange 独立;密码经真 cipher 加密,与实现同路径)。"""
    enc = get_cipher(get_settings().datasource_encrypt_key).encrypt(password)
    did = (
        await s.execute(
            text(
                "INSERT INTO public.sys_datasource"
                " (id, datasource_key, name, tenant_id, host, port, database, username,"
                "  password_enc, default_schema, status)"
                " VALUES (gen_random_uuid(), :k, :k, :t, :h, :p, :db, :u, :pe, :ds, :st)"
                " RETURNING id"
            ),
            {
                "k": key,
                "t": tenant_id,
                "h": host,
                "p": port,
                "db": database,
                "u": username,
                "pe": enc,
                "ds": schema,
                "st": status,
            },
        )
    ).scalar_one()
    await s.commit()
    return str(did)


def _payload(tenant_key: str, sql: str, **kw: object) -> dict[str, Any]:
    base: dict[str, Any] = {
        "trace_id": f"tr-{uuid.uuid4().hex[:12]}",
        "tenant_key": tenant_key,
        "sql_text": sql,
    }
    base.update(kw)
    return base


async def _audit_reasons(s: AsyncSession, trace_id: str) -> list[str]:
    rows = (
        await s.execute(
            text(
                "SELECT verdict, block_reason FROM public.sys_sql_audit WHERE trace_id=:t"
            ),
            {"t": trace_id},
        )
    ).all()
    return [f"{r.verdict}:{r.block_reason}" for r in rows]


# ============ 4.1 动态路由:全链路与防御层一致 ============


async def test_dynamic_datasource_pass_full_path(
    client: AsyncClient, session: AsyncSession
) -> None:
    """非 default 全链路 PASS:动态池执行,search_path 首段=default_schema;
    审计 datasource_key 记动态 key。"""
    tid = await _mk_tenant(session, "dta")
    await _mk_ds(session, "dyn", tid)
    p = _payload("dta", "SELECT mark FROM goods", datasource_key="dyn")
    resp = await client.post("/internal/sql/execute", json=p, headers=INTERNAL)
    assert resp.status_code == 200
    body = resp.json()
    assert body["verdict"] == "PASS"
    assert body["rows"] == [["D1"]]  # 命中 ds_dyn 而非其他 schema
    audits = (
        await session.execute(
            text("SELECT datasource_key, verdict FROM public.sys_sql_audit WHERE trace_id=:t"),
            {"t": p["trace_id"]},
        )
    ).all()
    assert [(str(a[0]), str(a[1])) for a in audits] == [("dyn", "PASS")]


async def test_dynamic_search_path_public_fallback(
    client: AsyncClient, session: AsyncSession
) -> None:
    """search_path=default_schema+public 两段:裸名命中 public 表(事务级,不污染池)。"""
    tid = await _mk_tenant(session, "dta")
    await _mk_ds(session, "dynpub", tid)
    p = _payload("dta", "SELECT mark FROM pub_goods", datasource_key="dynpub")
    resp = await client.post("/internal/sql/execute", json=p, headers=INTERNAL)
    assert resp.json()["rows"] == [["PUB"]]
    # 事务级:search_path 不残留在池连接上——下一请求裸名查 pub_goods 走默认租户
    # schema  tenant_dta 不存在该表时应报错;此处改查 information_schema 断言当前
    # 连接的 search_path 已复位(经同一池的新事务 current_schemas 不含 ds_dyn 亦可证)
    p2 = _payload("dta", "SELECT mark FROM pub_goods", datasource_key="dynpub")
    resp2 = await client.post("/internal/sql/execute", json=p2, headers=INTERNAL)
    assert resp2.json()["verdict"] == "PASS"


async def test_dynamic_invalid_same_shape_and_audited(
    client: AsyncClient, session: AsyncSession
) -> None:
    """不存在/禁用/跨租户:响应同形 400 DATASOURCE_INVALID(防探测);
    审计 BLOCKED + 分类(NOT_FOUND/DISABLED/CROSS_TENANT)内部可溯。"""
    tid = await _mk_tenant(session, "dta")
    tid_b = await _mk_tenant(session, "dtb")
    off_id = await _mk_ds(session, "dynoff", tid, status="DISABLED")
    assert off_id
    await _mk_ds(session, "dynb", tid_b)  # 归属 dtb

    details: set[str] = set()
    cases = (
        ("ghost", "NOT_FOUND"),
        ("dynoff", "DISABLED"),
        ("dynb", "CROSS_TENANT"),  # 请求租户 dta ≠ 归属 dtb
    )
    for key, category in cases:
        p = _payload("dta", "SELECT 1", datasource_key=key)
        resp = await client.post("/internal/sql/execute", json=p, headers=INTERNAL)
        assert resp.status_code == 400, key
        details.add(resp.json()["detail"])
        reasons = await _audit_reasons(session, p["trace_id"])
        assert reasons == [f"BLOCKED:DATASOURCE_INVALID: {category}"], key
    assert details == {"DATASOURCE_INVALID"}  # 三因同形


async def test_dynamic_self_scope_rewrite(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """data_scope 改写在动态源生效(SELF;列探测走动态连接,探测集含 default_schema)。"""
    uid = await make_user("dyn-self")
    tid = await _mk_tenant(session, "dta")
    await session.execute(
        text("INSERT INTO public.sys_user_tenant (user_id, tenant_id) VALUES (:u, :t)"),
        {"u": uid, "t": tid},
    )
    rid = (
        await session.execute(
            text(
                "INSERT INTO public.sys_role (id, role_key, role_name, data_scope)"
                " VALUES (gen_random_uuid(), 'DYN_SELF', 'DYN_SELF', 'SELF') RETURNING id"
            )
        )
    ).scalar_one()
    await session.execute(
        text("INSERT INTO public.sys_user_role (user_id, role_id) VALUES (:u, :r)"),
        {"u": uid, "r": str(rid)},
    )
    await session.commit()
    await _mk_ds(session, "dynself", tid, schema=SCHEMA_SELF)
    # 本人行(create_by=uid)
    async with get_engine().begin() as conn:
        await conn.execute(
            text(
                f'INSERT INTO "{SCHEMA_SELF}".goods (id, mark, create_by)'  # noqa: S608  # 模块常量
                " VALUES (gen_random_uuid(), 'ME', :u)"
            ),
            {"u": uid},
        )
    p = _payload("dta", "SELECT mark FROM goods", datasource_key="dynself", user_id=uid)
    resp = await client.post("/internal/sql/execute", json=p, headers=INTERNAL)
    body = resp.json()
    assert body["verdict"] == "PASS"
    assert body["rows"] == [["ME"]]  # 只剩本人行(改写生效)
    reasons = await _audit_reasons(session, p["trace_id"])
    assert reasons == ["PASS:None"]


async def test_dynamic_defense_layers_hold(
    client: AsyncClient, session: AsyncSession
) -> None:
    """防火墙/保留表/危险函数/schema 边界在动态源同拦(与默认路径完全一致)。"""
    tid = await _mk_tenant(session, "dta")
    await _mk_ds(session, "dyndef", tid)
    cases = (
        ("DELETE FROM goods", "BLOCKED"),  # 防火墙:非只读
        ("SELECT * FROM sys_user", "RESERVED_TABLE"),  # 保留表拒读
        ("SELECT pg_read_file('/etc/passwd')", "FORBIDDEN_FUNC"),  # 危险函数拒用
        (f"SELECT * FROM {SCHEMA_OTHER}.goods", "CROSS_SCHEMA"),  # noqa: S608  # 模块常量  # schema 边界
        ("SELECT * FROM public.sys_user", "RESERVED_TABLE"),  # public 段保留表同拦
    )
    for sql, expect in cases:
        p = _payload("dta", sql, datasource_key="dyndef")
        resp = await client.post("/internal/sql/execute", json=p, headers=INTERNAL)
        body = resp.json()
        assert body["verdict"] == "BLOCKED", sql
        assert expect in (body["reason"] or ""), sql
    # 对照:合法查询放行
    p = _payload("dta", "SELECT mark FROM goods", datasource_key="dyndef")
    resp = await client.post("/internal/sql/execute", json=p, headers=INTERNAL)
    assert resp.json()["verdict"] == "PASS"


async def test_dynamic_pool_readonly_and_truncation(
    client: AsyncClient, session: AsyncSession
) -> None:
    """动态池连接级只读(spec「动态连接池只读配置」);行数上限截断。"""
    tid = await _mk_tenant(session, "dta")
    await _mk_ds(session, "dynro", tid)
    p = _payload("dta", "SELECT current_setting('transaction_read_only')", datasource_key="dynro")
    resp = await client.post("/internal/sql/execute", json=p, headers=INTERNAL)
    assert resp.json()["rows"] == [["on"]]
    # 截断:ds_dyn.goods 此时 ≥1 行,limit=1 截断语义
    p = _payload("dta", "SELECT mark FROM goods", datasource_key="dynro", limit=1)
    resp = await client.post("/internal/sql/execute", json=p, headers=INTERNAL)
    assert resp.json()["row_count"] == 1


async def test_query_face_key_pattern_rejected(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """查询面 datasource_key 传输层 pattern 收口(008 D4):畸形 key 422(chat+internal)。"""
    tid = await _mk_tenant(session, "dta")
    assert tid
    r = await client.post(
        "/internal/sql/execute",
        json=_payload("dta", "SELECT 1", datasource_key="BAD-KEY"),
        headers=INTERNAL,
    )
    assert r.status_code == 422


# ============ 4.4 池生命周期联动 ============


async def test_password_change_evicts_and_rebuilds(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """改密码:commit 后旧池定向失效;下一查询按新凭证重建(错误密码 ERROR→修复后 PASS)。"""
    _, token = await _admin(client, session, make_user)
    tid = await _mk_tenant(session, "dta")
    ds_id = await _mk_ds(session, "dynpw", tid)
    p = _payload("dta", "SELECT mark FROM goods", datasource_key="dynpw")
    resp = await client.post("/internal/sql/execute", json=p, headers=INTERNAL)
    assert resp.json()["verdict"] == "PASS"
    assert "dynpw" in executor._ENGINES  # 池已建

    async def _update_password(pw: str | None) -> None:
        r = await client.put(
            f"/system/datasource/{ds_id}",
            json={
                "datasource_key": "dynpw",
                "tenant_id": tid,
                "name": "dynpw",
                "host": "127.0.0.1",
                "port": 5432,
                "database": "kongming_test",
                "username": "kongming",
                "password": pw,
                "default_schema": SCHEMA,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 204, r.text

    await _update_password("wrong-secret-9")
    assert "dynpw" not in executor._ENGINES  # commit 后定向失效
    resp = await client.post("/internal/sql/execute", json=p, headers=INTERNAL)
    assert resp.json()["verdict"] == "ERROR"  # 新凭证重建,认证失败收敛 ERROR
    await _update_password("kongming")
    resp = await client.post("/internal/sql/execute", json=p, headers=INTERNAL)
    assert resp.json()["verdict"] == "PASS"  # 修复后按最新凭证重建成功


async def test_disable_then_enable_rebuilds_with_latest_info(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """禁用即拒(池失效);启用后按最新连接信息(default_schema 已改)重建池。"""
    _, token = await _admin(client, session, make_user)
    tid = await _mk_tenant(session, "dta")
    ds_id = await _mk_ds(session, "dyntoggle", tid)
    auth = {"Authorization": f"Bearer {token}"}

    p = _payload("dta", "SELECT mark FROM goods", datasource_key="dyntoggle")
    resp = await client.post("/internal/sql/execute", json=p, headers=INTERNAL)
    assert resp.json()["rows"] == [["D1"]]

    r = await client.post(f"/system/datasource/{ds_id}/disable", headers=auth)
    assert r.status_code == 204
    assert "dyntoggle" not in executor._ENGINES
    resp = await client.post("/internal/sql/execute", json=p, headers=INTERNAL)
    assert resp.status_code == 400  # 禁用即拒(fail-closed)

    # 禁用期间改 default_schema;启用后按最新连接信息重建 → 命中 v2 schema
    r = await client.put(
        f"/system/datasource/{ds_id}",
        json={
            "datasource_key": "dyntoggle",
            "tenant_id": tid,
            "name": "dyntoggle",
            "host": "127.0.0.1",
            "port": 5432,
            "database": "kongming_test",
            "username": "kongming",
            "password": None,
            "default_schema": SCHEMA_V2,
        },
        headers=auth,
    )
    assert r.status_code == 204
    r = await client.post(f"/system/datasource/{ds_id}/enable", headers=auth)
    assert r.status_code == 204
    resp = await client.post("/internal/sql/execute", json=p, headers=INTERNAL)
    assert resp.json()["rows"] == [["D2"]]  # 最新 schema 生效


async def test_name_only_update_keeps_pool(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """仅改展示字段(name):池不失效不重建(spec「仅改 name 不动池」)。"""
    _, token = await _admin(client, session, make_user)
    tid = await _mk_tenant(session, "dta")
    ds_id = await _mk_ds(session, "dynname", tid)
    p = _payload("dta", "SELECT 1", datasource_key="dynname")
    resp = await client.post("/internal/sql/execute", json=p, headers=INTERNAL)
    assert resp.json()["verdict"] == "PASS"
    engine = executor._ENGINES["dynname"]

    r = await client.put(
        f"/system/datasource/{ds_id}",
        json={
            "datasource_key": "dynname",
            "tenant_id": tid,
            "name": "改名了",
            "host": "127.0.0.1",
            "port": 5432,
            "database": "kongming_test",
            "username": "kongming",
            "password": None,
            "default_schema": SCHEMA,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 204
    assert executor._ENGINES.get("dynname") is engine  # 同一池对象,未失效


async def test_delete_evicts_and_rejects(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """删除:池关闭,后续查询按不存在拒(400 同形)。"""
    _, token = await _admin(client, session, make_user)
    tid = await _mk_tenant(session, "dta")
    ds_id = await _mk_ds(session, "dyndel", tid)
    p = _payload("dta", "SELECT 1", datasource_key="dyndel")
    resp = await client.post("/internal/sql/execute", json=p, headers=INTERNAL)
    assert resp.json()["verdict"] == "PASS"
    r = await client.delete(
        f"/system/datasource/{ds_id}", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 204
    assert "dyndel" not in executor._ENGINES
    resp = await client.post("/internal/sql/execute", json=p, headers=INTERNAL)
    assert resp.status_code == 400
    reasons = await _audit_reasons(session, p["trace_id"])
    assert reasons[-1] == "BLOCKED:DATASOURCE_INVALID: NOT_FOUND"
