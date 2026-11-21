"""覆盖数据源管理接口、凭证保护、租户归属和连接池失效规则。"""
# pyright: reportPrivateUsage=false

import uuid as uuidlib
from collections.abc import Iterator
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.seed import seed_all
from kongming.config import get_settings
from kongming.datasource.cipher import get_cipher
from kongming.main import app
from tests.conftest import MakeUser, db_conn
from tests.test_chat_query import _auth, _bind, _default_tenant_id, _login
from tests.test_system_admin import _admin

# 本机测试库连接参数(环境派生;硬编码端口会在本机端口不同时整批假败)
_CONN = db_conn()

DS = "/system/datasource"


def _body(tenant_id: str, **over: object) -> dict[str, Any]:
    conn = db_conn()  # 连接参数环境派生(不硬编码端口,见 conftest.db_conn)
    body: dict[str, object] = {
        "datasource_key": "extdb",
        "name": "外部库",
        "tenant_id": tenant_id,
        "host": conn["host"],
        "port": conn["port"],
        "database": conn["database"],
        "username": conn["username"],
        "password": "secret-pw-1",
        "default_schema": "public",
    }
    body.update(over)
    return body


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


async def _create(
    client: AsyncClient, token: str, tenant_id: str, **over: object
) -> dict[str, Any]:
    r = await client.post(DS, json=_body(tenant_id, **over), headers=_auth(token))
    assert r.status_code == 201, r.text
    return dict(r.json())


async def _row(s: AsyncSession, key: str) -> dict[str, Any]:
    row = (
        (
            await s.execute(
                text("SELECT * FROM public.sys_datasource WHERE datasource_key=:k"), {"k": key}
            )
        )
        .mappings()
        .one()
    )
    return dict(row)


class FakeEvictor:
    """PoolEvictor 录音替身:断言 commit 后调用时机与目标 key。"""

    def __init__(self) -> None:
        self.evicted: list[str] = []

    async def evict(self, datasource_key: str) -> None:
        self.evicted.append(datasource_key)


@pytest.fixture
def fake_evictor() -> Iterator[FakeEvictor]:
    from kongming.datasource.router import get_pool_evictor

    fake = FakeEvictor()
    app.dependency_overrides[get_pool_evictor] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_pool_evictor, None)


# ============ 安全:权限面 ============


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/system/datasource"),
        ("POST", "/system/datasource"),
        ("PUT", f"/system/datasource/{uuidlib.uuid4()}"),
        ("POST", f"/system/datasource/{uuidlib.uuid4()}/enable"),
        ("POST", f"/system/datasource/{uuidlib.uuid4()}/disable"),
        ("DELETE", f"/system/datasource/{uuidlib.uuid4()}"),
        ("POST", "/system/datasource/test"),
    ],
)
async def test_no_perm_rejected_and_audited(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    method: str,
    path: str,
) -> None:
    """无 system:datasource:manage 的用户访问管理端点:一律 403 且落 perm_denied 审计。"""
    await seed_all(session, get_settings())
    await make_user("ds-nobody")
    token = _login(client)
    r = await client.request(method, path, headers=_auth(token), json={})
    assert r.status_code == 403, (method, path, r.text)
    n = await session.execute(
        text("SELECT count(*) FROM public.sys_oper_log WHERE action='perm_denied'")
    )
    assert int(n.scalar_one()) >= 1


async def test_unauthenticated_401(client: AsyncClient) -> None:
    assert (await client.get(DS)).status_code == 401
    assert (await client.get(f"{DS}/options")).status_code == 401


# ============ 注册与凭证加密 ============


async def test_create_encrypts_password_and_dto_clean(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    _, token = await _admin(client, session, make_user)
    tid = await _default_tenant_id(session)
    created = await _create(client, token, tid)
    assert set(created.keys()) == {"id"}  # 创建响应仅回 id

    row = await _row(session, "extdb")
    assert row["password_enc"] != "secret-pw-1"  # 永不存明文
    # 使用应用密钥解密已保存的密文。
    cipher = get_cipher(get_settings().datasource_encrypt_key)
    assert cipher.decrypt(str(row["password_enc"])) == "secret-pw-1"
    assert row["name"] == "外部库"
    assert row["default_schema"] == "public"
    assert row["status"] == "ACTIVE"

    # 分页 DTO 不含任何敏感字段(全局视角:X-Tenant-Id 无语义,他租户数据源同样列出)
    r = await client.get(DS, headers={**_auth(token), "X-Tenant-Id": "nonexistent-tenant"})
    assert r.status_code == 200
    page = r.json()
    assert page["total"] == 1
    item = page["items"][0]
    assert "password" not in item and "password_enc" not in item
    assert item["datasource_key"] == "extdb"
    assert item["tenant_key"] == "default"


async def test_key_unique_including_disabled(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    _, token = await _admin(client, session, make_user)
    tid = await _default_tenant_id(session)
    created = await _create(client, token, tid)
    # 同 key 再注册 → 409
    r = await client.post(DS, json=_body(tid), headers=_auth(token))
    assert r.status_code == 409
    # 禁用后仍占key(含禁用记录校验,与硬 UNIQUE 同语义)
    r = await client.post(f"{DS}/{created['id']}/disable", headers=_auth(token))
    assert r.status_code == 204
    r = await client.post(DS, json=_body(tid), headers=_auth(token))
    assert r.status_code == 409


@pytest.mark.parametrize(
    "bad_key",
    ["default", "Sakila", "sa-kila", "sa kila", "s", "sakila!", "sakila\n", "default\n"],
)
async def test_reserved_and_bad_key_rejected(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser, bad_key: str
) -> None:
    _, token = await _admin(client, session, make_user)
    tid = await _default_tenant_id(session)
    r = await client.post(DS, json=_body(tid, datasource_key=bad_key), headers=_auth(token))
    assert r.status_code == 400, bad_key
    n = await session.execute(text("SELECT count(*) FROM public.sys_datasource"))
    assert int(n.scalar_one()) == 0  # 不落库


@pytest.mark.parametrize(
    "bad_schema",
    ['pub"; DROP TABLE x;--', "public;x", "my schema", 'q"q', "-x", "public\n"],
)
async def test_default_schema_injection_rejected(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser, bad_schema: str
) -> None:
    _, token = await _admin(client, session, make_user)
    tid = await _default_tenant_id(session)
    r = await client.post(DS, json=_body(tid, default_schema=bad_schema), headers=_auth(token))
    assert r.status_code == 400, bad_schema
    n = await session.execute(text("SELECT count(*) FROM public.sys_datasource"))
    assert int(n.scalar_one()) == 0


async def test_key_and_tenant_immutable(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    _, token = await _admin(client, session, make_user)
    tid = await _default_tenant_id(session)
    other_tid = await _mk_tenant(session, "ds-other")
    created = await _create(client, token, tid)
    put_url = f"{DS}/{created['id']}"

    # 改 key → 400;改租户归属 → 400;原值提交 → 204
    r = await client.put(put_url, json=_body(tid, datasource_key="renamed"), headers=_auth(token))
    assert r.status_code == 400
    r = await client.put(put_url, json=_body(other_tid), headers=_auth(token))
    assert r.status_code == 400
    r = await client.put(put_url, json=_body(tid), headers=_auth(token))
    assert r.status_code == 204, r.text


async def test_create_tenant_explicit_not_implicit(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """租户归属取表单显式指定:即使 X-Tenant-Id 指向别的租户也按表单落库;
    指定租户不存在/已禁用 → 400。"""
    _, token = await _admin(client, session, make_user)
    default_tid = await _default_tenant_id(session)
    other_tid = await _mk_tenant(session, "ds-explicit")
    off_tid = await _mk_tenant(session, "ds-off", status="DISABLED")

    r = await client.post(
        DS,
        json=_body(other_tid),
        headers={**_auth(token), "X-Tenant-Id": "default"},  # 隐式上下文不生效
    )
    assert r.status_code == 201
    row = await _row(session, "extdb")
    assert str(row["tenant_id"]) == other_tid
    assert str(row["tenant_id"]) != default_tid

    r = await client.post(
        DS, json=_body(str(uuidlib.uuid4()), datasource_key="ghost"), headers=_auth(token)
    )
    assert r.status_code == 400
    r = await client.post(DS, json=_body(off_tid, datasource_key="off"), headers=_auth(token))
    assert r.status_code == 400


# ============ 编辑/启停/删除:审计 + evictor 时机 ============


async def test_update_password_blank_keeps_ciphertext(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser, fake_evictor: FakeEvictor
) -> None:
    _, token = await _admin(client, session, make_user)
    tid = await _default_tenant_id(session)
    created = await _create(client, token, tid)
    before = (await _row(session, "extdb"))["password_enc"]

    # 密码留空(不传)=不改;仅改 name 不触发 evict
    r = await client.put(
        f"{DS}/{created['id']}", json=_body(tid, name="改名", password=None), headers=_auth(token)
    )
    assert r.status_code == 204
    assert (await _row(session, "extdb"))["password_enc"] == before
    assert (await _row(session, "extdb"))["name"] == "改名"
    assert fake_evictor.evicted == []


async def test_update_conn_change_evicts(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser, fake_evictor: FakeEvictor
) -> None:
    _, token = await _admin(client, session, make_user)
    tid = await _default_tenant_id(session)
    created = await _create(client, token, tid)

    # 改密码 → 密文变更 + evict;改 host → evict(每次变更一次定向失效)
    r = await client.put(
        f"{DS}/{created['id']}", json=_body(tid, password="new-secret-pw"), headers=_auth(token)
    )
    assert r.status_code == 204
    cipher = get_cipher(get_settings().datasource_encrypt_key)
    assert cipher.decrypt(str((await _row(session, "extdb"))["password_enc"])) == "new-secret-pw"
    assert fake_evictor.evicted == ["extdb"]

    r = await client.put(
        f"{DS}/{created['id']}", json=_body(tid, host="192.168.1.1"), headers=_auth(token)
    )
    assert r.status_code == 204
    assert fake_evictor.evicted == ["extdb", "extdb"]


async def test_enable_disable_evict_and_audit(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser, fake_evictor: FakeEvictor
) -> None:
    _, token = await _admin(client, session, make_user)
    tid = await _default_tenant_id(session)
    created = await _create(client, token, tid)

    r = await client.post(f"{DS}/{created['id']}/disable", headers=_auth(token))
    assert r.status_code == 204
    assert (await _row(session, "extdb"))["status"] == "DISABLED"
    r = await client.post(f"{DS}/{created['id']}/enable", headers=_auth(token))
    assert r.status_code == 204
    assert (await _row(session, "extdb"))["status"] == "ACTIVE"
    assert fake_evictor.evicted == ["extdb", "extdb"]

    rows = (
        (
            await session.execute(
                text(
                    "SELECT action FROM public.sys_oper_log"
                    " WHERE action LIKE 'datasource.%' ORDER BY created_at, id"
                )
            )
        )
        .scalars()
        .all()
    )
    assert [str(a) for a in rows] == [
        "datasource.create",
        "datasource.disable",
        "datasource.enable",
    ]


async def test_delete_physical_and_audit(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser, fake_evictor: FakeEvictor
) -> None:
    _, token = await _admin(client, session, make_user)
    tid = await _default_tenant_id(session)
    created = await _create(client, token, tid)
    r = await client.delete(f"{DS}/{created['id']}", headers=_auth(token))
    assert r.status_code == 204
    n = await session.execute(
        text("SELECT count(*) FROM public.sys_datasource WHERE datasource_key='extdb'")
    )
    assert int(n.scalar_one()) == 0  # 物理删除
    assert fake_evictor.evicted == ["extdb"]
    n = await session.execute(
        text("SELECT count(*) FROM public.sys_oper_log WHERE action='datasource.delete'")
    )
    assert int(n.scalar_one()) == 1
    # 物理删除后 key 可再注册(硬 UNIQUE 随行释放)
    r = await client.post(DS, json=_body(tid), headers=_auth(token))
    assert r.status_code == 201


async def test_not_found_uniform_404(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    _, token = await _admin(client, session, make_user)
    ghost = str(uuidlib.uuid4())
    put = await client.put(f"{DS}/{ghost}", json=_body(str(uuidlib.uuid4())), headers=_auth(token))
    assert put.status_code == 404
    assert (await client.post(f"{DS}/{ghost}/enable", headers=_auth(token))).status_code == 404
    assert (await client.post(f"{DS}/{ghost}/disable", headers=_auth(token))).status_code == 404
    assert (await client.delete(f"{DS}/{ghost}", headers=_auth(token))).status_code == 404


async def test_page_search_keyword(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    _, token = await _admin(client, session, make_user)
    tid = await _default_tenant_id(session)
    await _create(client, token, tid, datasource_key="extdb", name="外部库")
    await _create(client, token, tid, datasource_key="erp", name="ERP 库")
    # ILIKE 元字符按字面(转义不失真)
    r = await client.get(DS, params={"keyword": "%"}, headers=_auth(token))
    assert r.json()["total"] == 0
    r = await client.get(DS, params={"keyword": "erp"}, headers=_auth(token))
    page = r.json()
    assert page["total"] == 1 and page["items"][0]["datasource_key"] == "erp"
    r = await client.get(DS, params={"keyword": "外部"}, headers=_auth(token))
    assert r.json()["total"] == 1


# ============ 选项接口(登录即可;首位合成 default) ============


async def test_options_default_first_and_scoped(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    _, admin_token = await _admin(client, session, make_user)
    tid = await _default_tenant_id(session)
    other_tid = await _mk_tenant(session, "ds-opt")
    a = await _create(client, admin_token, tid, datasource_key="extdb", name="外部库")
    await _create(client, admin_token, tid, datasource_key="erp", name="ERP 库")
    await _create(client, admin_token, other_tid, datasource_key="otherdb", name="他租户库")
    # 禁用的不出现在选项
    r = await client.post(f"{DS}/{a['id']}/disable", headers=_auth(admin_token))
    assert r.status_code == 204

    # 普通用户(仅绑定 default 租户,无 manage 权限)也可拉取
    uid = await make_user("ds-viewer")
    await _bind(session, "sys_user_tenant", uid, tid)
    token = _login(client)
    r = await client.get(f"{DS}/options", headers=_auth(token))
    assert r.status_code == 200
    opts = r.json()
    assert opts[0] == {"datasource_key": "default", "name": "默认数据源"}  # 首位合成项
    assert [o["datasource_key"] for o in opts] == ["default", "erp"]  # 仅当前租户启用行
    assert all("host" not in o and "username" not in o for o in opts)  # 不含连接信息

    # 显式切换到未绑定租户 → 403(与 chat 租户白名单同构)
    r = await client.get(f"{DS}/options", headers={**_auth(token), "X-Tenant-Id": "ds-opt"})
    assert r.status_code == 403


async def test_options_default_tenant_matches_chat_semantics(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """回归:缺省租户解析与 chat 同构——default 优先,否则**最早授予**
    (非最早创建);授予顺序 ≠ 创建顺序时,options 与 /chat/query 必须解析到同一租户,
    否则用户选中选项即被 chat 预检误拒。"""
    _, admin_token = await _admin(client, session, make_user)
    # tenant_old:先创建、后授予;tenant_new:后创建、先授予 → chat 语义默认=tenant_new
    old_tid = await _mk_tenant(session, "ds-old")
    new_tid = await _mk_tenant(session, "ds-new")
    uid = await make_user("ds-multi")
    await session.execute(
        text(
            "INSERT INTO public.sys_user_tenant (user_id, tenant_id, granted_at)"
            " VALUES (:u, :t, now() - interval '1 hour')"  # 先授 new
        ),
        {"u": uid, "t": new_tid},
    )
    await session.execute(
        text(
            "INSERT INTO public.sys_user_tenant (user_id, tenant_id, granted_at)"
            " VALUES (:u, :t, now())"  # 后授 old
        ),
        {"u": uid, "t": old_tid},
    )
    await session.commit()
    await _create(client, admin_token, old_tid, datasource_key="olds", name="旧租户源")
    await _create(client, admin_token, new_tid, datasource_key="newds", name="新租户源")

    token = _login(client)
    # 不带 X-Tenant-Id:解析结果必须是「最早授予」的 ds-new(与 chat 同一租户)
    r = await client.get(f"{DS}/options", headers=_auth(token))
    assert r.status_code == 200
    assert [o["datasource_key"] for o in r.json()] == ["default", "newds"]
    # 对照:chat 语义直连验证(选中 newds 经 /chat/query 预检应放行;
    # 补 chat:execute 角色过权限闸,agent 走替身保持确定性)
    from kongming.chat.dependencies import get_agent_client
    from tests.test_chat_query import FakeAgent

    rid = (
        await session.execute(text("SELECT id FROM public.sys_role WHERE role_key='DEVELOPER'"))
    ).scalar_one()
    await _bind(session, "sys_user_role", uid, str(rid))
    # 零参工厂:类本身作 override 会被 FastAPI 解析 __init__ 签名(422 坑)
    app.dependency_overrides[get_agent_client] = lambda: FakeAgent()  # noqa: PLW0108 —— 需零参可调用
    try:
        r = await client.post(
            "/chat/query",
            json={"question": "q", "datasource_key": "newds", "persist": False},
            headers=_auth(token),
        )
        assert r.status_code == 200, r.text  # 预检放行(400 唯一来源是 InvalidDatasource)
    finally:
        app.dependency_overrides.pop(get_agent_client, None)


# ============ 测试连接(无状态,不落库/不建池/不落审计) ============


async def test_test_connection_ok(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """真实 PG 直连(测试库自身)成功返回 ok;不落库/不建池/不落审计。"""
    _, token = await _admin(client, session, make_user)
    r = await client.post(
        f"{DS}/test",
        json={
            "host": _CONN["host"],
            "port": _CONN["port"],
            "database": _CONN["database"],
            "username": _CONN["username"],
            "password": _CONN["password"],
        },
        headers=_auth(token),
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True, "reason": None}
    # 无状态三不:不落库/不落审计/不建池
    n = await session.execute(text("SELECT count(*) FROM public.sys_datasource"))
    assert int(n.scalar_one()) == 0
    n = await session.execute(
        text("SELECT count(*) FROM public.sys_oper_log WHERE action LIKE 'datasource.%'")
    )
    assert int(n.scalar_one()) == 0
    from kongming.internal import executor

    assert executor._ENGINES == {}


async def test_test_connection_auth_failure_hides_password(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    _, token = await _admin(client, session, make_user)
    r = await client.post(
        f"{DS}/test",
        json={
            "host": _CONN["host"],
            "port": _CONN["port"],
            "database": _CONN["database"],
            "username": _CONN["username"],
            "password": "wrong-secret-9",
        },
        headers=_auth(token),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "认证失败" in (body["reason"] or "")
    assert "wrong-secret-9" not in (body["reason"] or "")  # 不暴露密码


async def test_test_connection_timeout(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """不可达地址 3s 超时收敛为「连接超时」类原因(真实计时,~3s)。"""
    _, token = await _admin(client, session, make_user)
    r = await client.post(
        f"{DS}/test",
        json={
            "host": "10.255.255.1",  # 不可路由地址,连接挂起至超时
            "port": 5432,
            "database": "x",
            "username": "x",
            "password": "x",
        },
        headers=_auth(token),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "超时" in (body["reason"] or "") or "无法连接" in (body["reason"] or "")


async def test_test_connection_edit_mode_backfills_password(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """编辑态密码留空:服务端取库存密文解密补位后直连(全程不回显)。"""
    _, token = await _admin(client, session, make_user)
    tid = await _default_tenant_id(session)
    # 注册一个指向测试库自身的真数据源(密码为测试库真实口令)
    created = await _create(client, token, tid, password=str(_CONN["password"]))
    r = await client.post(
        f"{DS}/test",
        json={
            "id": created["id"],
            "host": _CONN["host"],
            "port": _CONN["port"],
            "database": _CONN["database"],
            "username": _CONN["username"],
            "password": None,  # 留空 → 库存密文补位
        },
        headers=_auth(token),
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True, "reason": None}


async def test_test_connection_undecryptable_credential(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """密文不可解(模拟密钥轮换/损坏)→「凭证不可用」类失败,不暴露细节。"""
    _, token = await _admin(client, session, make_user)
    tid = await _default_tenant_id(session)
    created = await _create(client, token, tid)
    await session.execute(
        text("UPDATE public.sys_datasource SET password_enc='!!!tampered!!!' WHERE id=:i"),
        {"i": created["id"]},
    )
    await session.commit()
    r = await client.post(
        f"{DS}/test",
        json={
            "id": created["id"],
            "host": _CONN["host"],
            "port": _CONN["port"],
            "database": _CONN["database"],
            "username": _CONN["username"],
            "password": None,
        },
        headers=_auth(token),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "凭证不可用" in (body["reason"] or "")
    assert "tampered" not in (body["reason"] or "")  # 不暴露密文细节


async def test_test_connection_edit_mode_missing_datasource(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    _, token = await _admin(client, session, make_user)
    r = await client.post(
        f"{DS}/test",
        json={
            "id": str(uuidlib.uuid4()),
            "host": "127.0.0.1",
            "port": 5432,
            "database": "x",
            "username": "x",
            "password": None,
        },
        headers=_auth(token),
    )
    assert r.status_code == 404
