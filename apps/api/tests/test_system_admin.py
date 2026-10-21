"""覆盖用户、角色和部门管理及账号状态保护。"""
# pyright: reportPrivateUsage=false

import uuid as uuidlib
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.seed import seed_all
from kongming.config import get_settings
from tests.conftest import MakeUser
from tests.test_chat_query import _auth, _bind, _default_tenant_id, _login


async def _admin(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser, username: str = "boss"
) -> tuple[str, str]:
    """造 SUPER_ADMIN 用户并登录(全权限管理员)。"""
    await seed_all(session, get_settings())
    uid = await make_user(username)
    rid = (
        await session.execute(text("SELECT id FROM public.sys_role WHERE role_key='SUPER_ADMIN'"))
    ).scalar_one()
    await _bind(session, "sys_user_role", uid, str(rid))
    await _bind(session, "sys_user_tenant", uid, await _default_tenant_id(session))
    return uid, await _login(client, username)


async def _mk_dept(s: AsyncSession, name: str, parent_id: str | None = None) -> str:
    tid = await _default_tenant_id(s)
    did = (
        await s.execute(
            text(
                "INSERT INTO public.sys_dept (id, parent_id, dept_name, tenant_id)"
                " VALUES (:i, :p, :n, :t) RETURNING id"
            ),
            {"i": str(uuidlib.uuid4()), "p": parent_id, "n": name, "t": tid},
        )
    ).scalar_one()
    await s.commit()
    return str(did)


async def _role_id(s: AsyncSession, key: str) -> str:
    return str(
        (
            await s.execute(text("SELECT id FROM public.sys_role WHERE role_key=:k"), {"k": key})
        ).scalar_one()
    )


# ============ 安全:无权限 403 + 审计 ============


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/system/users"),
        ("POST", "/system/users"),
        ("PUT", "/system/users/" + str(uuidlib.uuid4())),
        ("POST", f"/system/users/{uuidlib.uuid4()}/reset-password"),
        ("DELETE", "/system/users/" + str(uuidlib.uuid4())),
        ("POST", f"/system/users/{uuidlib.uuid4()}/enable"),
        ("POST", f"/system/users/{uuidlib.uuid4()}/disable"),
        ("GET", "/system/roles"),
        ("POST", "/system/roles"),
        ("PUT", "/system/roles/" + str(uuidlib.uuid4())),
        ("DELETE", "/system/roles/" + str(uuidlib.uuid4())),
        ("POST", f"/system/roles/{uuidlib.uuid4()}/permissions"),
        ("GET", "/system/permissions"),
        ("GET", "/system/depts"),
        ("POST", "/system/depts"),
        ("PUT", "/system/depts/" + str(uuidlib.uuid4())),
        ("DELETE", "/system/depts/" + str(uuidlib.uuid4())),
        ("GET", "/system/tenants"),
    ],
)
async def test_no_perm_rejected_and_audited(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    method: str,
    path: str,
) -> None:
    """无任何角色的用户访问 system 端点族:一律 403 且落 perm_denied 审计。"""
    await seed_all(session, get_settings())
    await make_user("nobody")
    token = await _login(client, "nobody")
    r = await client.request(method, path, headers=_auth(token), json={})
    assert r.status_code == 403, (method, path, r.text)
    n = await session.execute(
        text("SELECT count(*) FROM public.sys_oper_log WHERE action='perm_denied'")
    )
    assert int(n.scalar_one()) >= 1


@pytest.mark.parametrize(
    "method",
    ["GET", "POST"],
)
async def test_unauthenticated_401(client: AsyncClient, method: str) -> None:
    """新启停端点与列表端点同走 UserClaims 认证闸,未认证一律 401。"""
    ghost = str(uuidlib.uuid4())
    paths = {
        "GET": "/system/users",
        "POST": f"/system/users/{ghost}/disable",
    }
    r = await client.request(method, paths[method])
    assert r.status_code == 401


# ============ 安全:内置数据保护 ============


@pytest.mark.parametrize("key", ["SUPER_ADMIN", "DEVELOPER"])
async def test_builtin_role_delete_rejected(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser, key: str
) -> None:
    _, token = await _admin(client, session, make_user)
    rid = await _role_id(session, key)
    r = await client.delete(f"/system/roles/{rid}", headers=_auth(token))
    assert r.status_code == 409
    assert await _role_id(session, key) == rid  # 角色保留


async def test_role_key_immutable(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    _, token = await _admin(client, session, make_user)
    rid = await _role_id(session, "DEVELOPER")
    r = await client.put(
        f"/system/roles/{rid}",
        json={
            "role_key": "RENAMED",
            "role_name": "开发者",
            "data_scope": "SELF",
            "sql_scope": "SELECT_ONLY",
            "sort": 2,
        },
        headers=_auth(token),
    )
    assert r.status_code == 400
    assert await _role_id(session, "DEVELOPER") == rid


async def test_admin_account_delete_rejected(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    _, token = await _admin(client, session, make_user)
    settings = get_settings()
    admin_uid = str(
        (
            await session.execute(
                text("SELECT id FROM public.sys_user WHERE username=:u"),
                {"u": settings.admin_username},
            )
        ).scalar_one()
    )
    r = await client.delete(f"/system/users/{admin_uid}", headers=_auth(token))
    assert r.status_code == 409
    kept = await session.execute(
        text("SELECT deleted_at FROM public.sys_user WHERE id=:i"), {"i": admin_uid}
    )
    assert kept.scalar_one() is None


async def test_self_delete_rejected(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    uid, token = await _admin(client, session, make_user)
    r = await client.delete(f"/system/users/{uid}", headers=_auth(token))
    assert r.status_code == 409


async def test_self_disable_rejected(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    uid, token = await _admin(client, session, make_user)
    tid = await _default_tenant_id(session)
    r = await client.put(
        f"/system/users/{uid}",
        json={
            "nickname": "自己",
            "email": None,
            "status": "DISABLED",
            "dept_id": None,
            "tenant_id": tid,
            "role_ids": [],
        },
        headers=_auth(token),
    )
    assert r.status_code == 409
    status = await session.execute(
        text("SELECT status FROM public.sys_user WHERE id=:i"), {"i": uid}
    )
    assert status.scalar_one() == "ACTIVE"


async def test_disabled_user_token_rejected(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """禁用用户后,其既有令牌在下一次受权限点校验的请求中返回 401。"""
    await seed_all(session, get_settings())
    uid = await make_user("victim")
    token = await _login(client, "victim")
    await session.execute(
        text("UPDATE public.sys_user SET status='DISABLED' WHERE id=:i"), {"i": uid}
    )
    await session.commit()
    r = await client.get("/system/users", headers=_auth(token))
    assert r.status_code == 401


# ============ 安全:删除前置校验 ============


async def _mk_role(s: AsyncSession, key: str) -> str:
    rid = (
        await s.execute(
            text(
                "INSERT INTO public.sys_role (id, role_key, role_name)"
                " VALUES (:i, :k, :k) RETURNING id"
            ),
            {"i": str(uuidlib.uuid4()), "k": key},
        )
    ).scalar_one()
    await s.commit()
    return str(rid)


async def test_role_in_use_delete_rejected(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    _, token = await _admin(client, session, make_user)
    uid = await make_user("bound-user")
    rid = await _mk_role(session, "TEMP_ROLE")
    await _bind(session, "sys_user_role", uid, rid)
    r = await client.delete(f"/system/roles/{rid}", headers=_auth(token))
    assert r.status_code == 409


async def test_role_with_only_deleted_user_deletable(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    _, token = await _admin(client, session, make_user)
    uid = await make_user("gone-user")
    rid = await _mk_role(session, "TEMP_ROLE2")
    await _bind(session, "sys_user_role", uid, rid)
    await session.execute(
        text("UPDATE public.sys_user SET deleted_at=now() WHERE id=:i"), {"i": uid}
    )
    await session.commit()
    r = await client.delete(f"/system/roles/{rid}", headers=_auth(token))
    assert r.status_code == 204
    gone = await session.execute(
        text("SELECT count(*) FROM public.sys_role WHERE id=:i"), {"i": rid}
    )
    assert int(gone.scalar_one()) == 0
    # 绑定已级联清理
    binds = await session.execute(
        text("SELECT count(*) FROM public.sys_user_role WHERE role_id=:i"), {"i": rid}
    )
    assert int(binds.scalar_one()) == 0


async def test_dept_with_child_delete_rejected(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    _, token = await _admin(client, session, make_user)
    parent = await _mk_dept(session, "总部")
    await _mk_dept(session, "分部", parent_id=parent)
    r = await client.delete(f"/system/depts/{parent}", headers=_auth(token))
    assert r.status_code == 409


async def test_dept_with_user_delete_rejected(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    _, token = await _admin(client, session, make_user)
    did = await _mk_dept(session, "有人部门")
    uid = await make_user("dept-user")
    await session.execute(
        text("UPDATE public.sys_user SET dept_id=:d WHERE id=:i"), {"d": did, "i": uid}
    )
    await session.commit()
    r = await client.delete(f"/system/depts/{did}", headers=_auth(token))
    assert r.status_code == 409


async def test_dept_cycle_parent_rejected(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """父不可为自身或其后代(防成环)。"""
    _, token = await _admin(client, session, make_user)
    tid = await _default_tenant_id(session)
    a = await _mk_dept(session, "A")
    b = await _mk_dept(session, "B", parent_id=a)
    c = await _mk_dept(session, "C", parent_id=b)
    for new_parent, label in ((c, "后代"), (b, "子级")):
        r = await client.put(
            f"/system/depts/{a}",
            json={
                "parent_id": new_parent,
                "dept_name": "A",
                "tenant_id": tid,
                "sort": 0,
                "status": "ACTIVE",
            },
            headers=_auth(token),
        )
        assert r.status_code == 400, label
    # 自身为父
    r = await client.put(
        f"/system/depts/{a}",
        json={
            "parent_id": a,
            "dept_name": "A",
            "tenant_id": tid,
            "sort": 0,
            "status": "ACTIVE",
        },
        headers=_auth(token),
    )
    assert r.status_code == 400
    # 「父设为祖先」是合法树上移,不属成环
    r = await client.put(
        f"/system/depts/{c}",
        json={
            "parent_id": a,
            "dept_name": "C",
            "tenant_id": tid,
            "sort": 0,
            "status": "ACTIVE",
        },
        headers=_auth(token),
    )
    assert r.status_code == 204


# ============ 履约:用户管理 ============


async def test_user_crud_flow(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    _, token = await _admin(client, session, make_user)
    tid = await _default_tenant_id(session)
    dept = await _mk_dept(session, "研发部")
    dev_role = await _role_id(session, "DEVELOPER")

    # 创建:argon2 落库 + 首登标志未改 + 角色/租户/部门绑定
    r = await client.post(
        "/system/users",
        json={
            "username": "newbie",
            "password": "Initial#2026",
            "nickname": "新人",
            "email": "newbie@example.com",
            "status": "ACTIVE",
            "dept_id": dept,
            "tenant_id": tid,
            "role_ids": [dev_role],
        },
        headers=_auth(token),
    )
    assert r.status_code == 201, r.text
    uid = r.json()["id"]
    row = (
        await session.execute(
            text(
                "SELECT password_hash, password_changed_at, dept_id FROM public.sys_user"
                " WHERE id=:i"
            ),
            {"i": uid},
        )
    ).one()
    assert row.password_hash and row.password_hash.startswith("$argon2")
    assert row.password_changed_at is None  # 首登强制改密
    assert str(row.dept_id) == dept
    roles = await session.execute(
        text("SELECT role_id FROM public.sys_user_role WHERE user_id=:i"), {"i": uid}
    )
    assert [str(x[0]) for x in roles] == [dev_role]

    # 列表:聚合行可见(角色键+部门名),关键字过滤
    r = await client.get("/system/users", params={"keyword": "新人"}, headers=_auth(token))
    assert r.status_code == 200
    page = r.json()
    assert page["total"] == 1
    item = page["items"][0]
    assert item["username"] == "newbie"
    assert item["role_keys"] == ["DEVELOPER"]
    assert item["dept_name"] == "研发部"
    # 编辑回显所需 id 字段(缺失会导致编辑对话框静默清空部门绑定)
    assert item["dept_id"] == dept
    assert item["tenant_id"] == tid
    # 关键字大小写不敏感(ILIKE)
    r = await client.get("/system/users", params={"keyword": "NEWBIE"}, headers=_auth(token))
    assert r.json()["total"] == 1
    r = await client.get("/system/users", params={"keyword": "不存在"}, headers=_auth(token))
    assert r.json()["total"] == 0

    # 编辑:不传密码不改哈希;状态/部门/角色覆盖
    r = await client.put(
        f"/system/users/{uid}",
        json={
            "nickname": "新人改名",
            "email": None,
            "status": "ACTIVE",
            "dept_id": None,
            "tenant_id": tid,
            "role_ids": [],
        },
        headers=_auth(token),
    )
    assert r.status_code == 204
    after = (
        await session.execute(
            text("SELECT password_hash, nickname, dept_id, email FROM public.sys_user WHERE id=:i"),
            {"i": uid},
        )
    ).one()
    assert after.password_hash == row.password_hash
    assert after.nickname == "新人改名"
    assert after.dept_id is None and after.email is None
    binds = await session.execute(
        text("SELECT count(*) FROM public.sys_user_role WHERE user_id=:i"), {"i": uid}
    )
    assert int(binds.scalar_one()) == 0

    # 重置密码:标志复位
    r = await client.post(
        f"/system/users/{uid}/reset-password",
        json={"new_password": "Reset#2026x"},
        headers=_auth(token),
    )
    assert r.status_code == 204
    reset = (
        await session.execute(
            text("SELECT password_hash, password_changed_at FROM public.sys_user WHERE id=:i"),
            {"i": uid},
        )
    ).one()
    assert reset.password_hash != row.password_hash
    assert reset.password_changed_at is None

    # 删除:软删
    r = await client.delete(f"/system/users/{uid}", headers=_auth(token))
    assert r.status_code == 204
    soft = await session.execute(
        text("SELECT deleted_at FROM public.sys_user WHERE id=:i"), {"i": uid}
    )
    assert soft.scalar_one() is not None
    # 软删后列表不可见
    r = await client.get("/system/users", params={"keyword": "newbie"}, headers=_auth(token))
    assert r.json()["total"] == 0


async def test_username_uniqueness_with_soft_delete(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """用户名唯一=过滤软删(软删记录不占名,rbac spec)。"""
    _, token = await _admin(client, session, make_user)
    tid = await _default_tenant_id(session)
    # "role_ids": [] 使推断含 list[Unknown],显式 Any 值域
    body: dict[str, Any] = {
        "username": "dup",
        "password": "Initial#2026",
        "nickname": "重复",
        "tenant_id": tid,
        "role_ids": [],
    }
    r = await client.post("/system/users", json=body, headers=_auth(token))
    assert r.status_code == 201
    # 未软删同名 → 409
    r = await client.post("/system/users", json=body, headers=_auth(token))
    assert r.status_code == 409
    # 软删后同名可建
    await session.execute(text("UPDATE public.sys_user SET deleted_at=now() WHERE username='dup'"))
    await session.commit()
    r = await client.post("/system/users", json=body, headers=_auth(token))
    assert r.status_code == 201


async def test_create_user_unknown_refs_rejected(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    _, token = await _admin(client, session, make_user)
    base: dict[str, Any] = {
        "username": "badrefs",
        "password": "Initial#2026",
        "nickname": "坏引用",
        "tenant_id": str(uuidlib.uuid4()),  # 未知租户
        "role_ids": [],
    }
    r = await client.post("/system/users", json=base, headers=_auth(token))
    assert r.status_code == 400
    tid = await _default_tenant_id(session)
    r = await client.post(
        "/system/users",
        json={**base, "tenant_id": tid, "role_ids": [str(uuidlib.uuid4())]},
        headers=_auth(token),
    )
    assert r.status_code == 400
    r = await client.post(
        "/system/users",
        json={**base, "tenant_id": tid, "dept_id": str(uuidlib.uuid4())},
        headers=_auth(token),
    )
    assert r.status_code == 400
    # 不存在用户编辑/重置/删除 → 404
    ghost = str(uuidlib.uuid4())
    r = await client.put(
        f"/system/users/{ghost}",
        json={
            "nickname": "x",
            "status": "ACTIVE",
            "tenant_id": tid,
            "role_ids": [],
        },
        headers=_auth(token),
    )
    assert r.status_code == 404
    r = await client.post(
        f"/system/users/{ghost}/reset-password",
        json={"new_password": "Whatever#2026"},
        headers=_auth(token),
    )
    assert r.status_code == 404
    r = await client.delete(f"/system/users/{ghost}", headers=_auth(token))
    assert r.status_code == 404


# ============ 履约:角色与权限绑定 ============


async def _role_by_key(client: AsyncClient, token: str, key: str) -> dict[str, Any]:
    r = await client.get("/system/roles", headers=_auth(token))
    return next(x for x in r.json() if x["role_key"] == key)


async def test_role_crud_and_perm_binding(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    _, token = await _admin(client, session, make_user)
    # 清单:内置标记 + perm 聚合
    r = await client.get("/system/roles", headers=_auth(token))
    assert r.status_code == 200
    roles = {x["role_key"]: x for x in r.json()}
    assert roles["SUPER_ADMIN"]["builtin"] is True
    assert roles["DEVELOPER"]["builtin"] is True
    assert len(roles["SUPER_ADMIN"]["perm_ids"]) >= 3  # 种子全权限点

    # 创建
    r = await client.post(
        "/system/roles",
        json={
            "role_key": "ANALYST",
            "role_name": "分析师",
            "data_scope": "DEPT",
            "sql_scope": "SELECT_ONLY",
            "sort": 5,
        },
        headers=_auth(token),
    )
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    # role_key 冲突 409
    r = await client.post(
        "/system/roles",
        json={"role_key": "ANALYST", "role_name": "又", "sort": 0},
        headers=_auth(token),
    )
    assert r.status_code == 409

    # 权限点清单
    r = await client.get("/system/permissions", headers=_auth(token))
    perms = r.json()
    assert [p["sort"] for p in perms] == sorted(p["sort"] for p in perms)
    by_code = {p["perm_code"]: p["id"] for p in perms}
    user_manage = by_code["system:user:manage"]
    chat_exec = by_code["chat:execute"]

    # 覆盖式绑定:绑两个 → 改绑一个 → 只剩一个;空数组=清空
    r = await client.post(
        f"/system/roles/{rid}/permissions",
        json={"perm_ids": [user_manage, chat_exec]},
        headers=_auth(token),
    )
    assert r.status_code == 204
    cur = await _role_by_key(client, token, "ANALYST")
    assert set(cur["perm_ids"]) == {user_manage, chat_exec}
    r = await client.post(
        f"/system/roles/{rid}/permissions",
        json={"perm_ids": [chat_exec]},
        headers=_auth(token),
    )
    assert r.status_code == 204
    assert (await _role_by_key(client, token, "ANALYST"))["perm_ids"] == [chat_exec]
    # 键缺失 400(防呆:清空须显式空数组)
    r = await client.post(f"/system/roles/{rid}/permissions", json={}, headers=_auth(token))
    assert r.status_code == 400
    r = await client.post(
        f"/system/roles/{rid}/permissions", json={"perm_ids": []}, headers=_auth(token)
    )
    assert r.status_code == 204
    assert (await _role_by_key(client, token, "ANALYST"))["perm_ids"] == []
    # 未知权限点 400
    r = await client.post(
        f"/system/roles/{rid}/permissions",
        json={"perm_ids": [str(uuidlib.uuid4())]},
        headers=_auth(token),
    )
    assert r.status_code == 400

    # 编辑(role_key 原值)
    r = await client.put(
        f"/system/roles/{rid}",
        json={
            "role_key": "ANALYST",
            "role_name": "首席分析师",
            "data_scope": "ALL",
            "sql_scope": "READ_DDL",
            "sort": 6,
        },
        headers=_auth(token),
    )
    assert r.status_code == 204
    role = await _role_by_key(client, token, "ANALYST")
    assert role["role_name"] == "首席分析师" and role["data_scope"] == "ALL"

    # 删除自建角色
    r = await client.delete(f"/system/roles/{rid}", headers=_auth(token))
    assert r.status_code == 204


# ============ 履约:角色 CUSTOM 自定义部门集合(datascope CUSTOM) ============


async def _dept_rows(session: AsyncSession, role_id: str) -> list[str]:
    rows = await session.execute(
        text("SELECT dept_id FROM public.sys_role_dept WHERE role_id=:r ORDER BY dept_id"),
        {"r": role_id},
    )
    return [str(x[0]) for x in rows]


async def test_role_custom_dept_ids_roundtrip_and_replace(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """CUSTOM 读写往返保部门集合;再提交即整包替换(与权限绑定覆盖式语义同构)。"""
    _, token = await _admin(client, session, make_user)
    d1, d2 = await _mk_dept(session, "自定义部1"), await _mk_dept(session, "自定义部2")
    r = await client.post(
        "/system/roles",
        json={
            "role_key": "CUSTOMER_OPS",
            "role_name": "客户运营",
            "data_scope": "CUSTOM",
            "sql_scope": "SELECT_ONLY",
            "sort": 3,
            "data_scope_dept_ids": [d1, d2],
        },
        headers=_auth(token),
    )
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    role = await _role_by_key(client, token, "CUSTOMER_OPS")
    assert set(role["data_scope_dept_ids"]) == {d1, d2}
    assert set(await _dept_rows(session, rid)) == {d1, d2}

    # 整包替换:只留 d2,d1 必须消失(先删后插,不留半个集合)
    r = await client.put(
        f"/system/roles/{rid}",
        json={
            "role_key": "CUSTOMER_OPS",
            "role_name": "客户运营",
            "data_scope": "CUSTOM",
            "sql_scope": "SELECT_ONLY",
            "sort": 3,
            "data_scope_dept_ids": [d2],
        },
        headers=_auth(token),
    )
    assert r.status_code == 204, r.text
    assert (await _role_by_key(client, token, "CUSTOMER_OPS"))["data_scope_dept_ids"] == [d2]
    assert await _dept_rows(session, rid) == [d2]

    # 切走 CUSTOM:集合清空(不留「配了部门却不生效」的隐性状态)
    r = await client.put(
        f"/system/roles/{rid}",
        json={
            "role_key": "CUSTOMER_OPS",
            "role_name": "客户运营",
            "data_scope": "DEPT",
            "sql_scope": "SELECT_ONLY",
            "sort": 3,
        },
        headers=_auth(token),
    )
    assert r.status_code == 204, r.text
    assert (await _role_by_key(client, token, "CUSTOMER_OPS"))["data_scope_dept_ids"] == []
    assert await _dept_rows(session, rid) == []


async def test_role_custom_requires_non_empty_dept_set(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """CUSTOM 缺集合(空数组或键缺失)→ 400 显式拒绝,不落一个「查询必被拒」的角色。"""
    _, token = await _admin(client, session, make_user)
    base = {"role_key": "EMPTY_CUSTOM", "role_name": "空自定义", "data_scope": "CUSTOM"}
    r = await client.post("/system/roles", json={**base, "sort": 0}, headers=_auth(token))
    assert r.status_code == 400
    r = await client.post(
        "/system/roles", json={**base, "sort": 0, "data_scope_dept_ids": []}, headers=_auth(token)
    )
    assert r.status_code == 400
    assert (
        await session.execute(text("SELECT 1 FROM public.sys_role WHERE role_key='EMPTY_CUSTOM'"))
    ).scalar_one_or_none() is None


async def test_role_custom_unknown_dept_rejected(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    _, token = await _admin(client, session, make_user)
    d1 = await _mk_dept(session, "存在的部")
    r = await client.post(
        "/system/roles",
        json={
            "role_key": "GHOST_DEPT",
            "role_name": "幽灵部门",
            "data_scope": "CUSTOM",
            "sort": 0,
            "data_scope_dept_ids": [d1, str(uuidlib.uuid4())],
        },
        headers=_auth(token),
    )
    assert r.status_code == 400
    assert "不存在" in r.json()["detail"]
    assert (
        await session.execute(text("SELECT 1 FROM public.sys_role WHERE role_key='GHOST_DEPT'"))
    ).scalar_one_or_none() is None


async def test_role_non_custom_with_dept_ids_rejected(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """非 CUSTOM 却带非空部门集合 → 显式拒绝(不静默忽略)。"""
    _, token = await _admin(client, session, make_user)
    d1 = await _mk_dept(session, "本部门")
    r = await client.post(
        "/system/roles",
        json={
            "role_key": "MIXED_SCOPE",
            "role_name": "错配",
            "data_scope": "DEPT",
            "sort": 0,
            "data_scope_dept_ids": [d1],
        },
        headers=_auth(token),
    )
    assert r.status_code == 400
    rid = await _role_id(session, "DEVELOPER")
    r = await client.put(
        f"/system/roles/{rid}",
        json={
            "role_key": "DEVELOPER",
            "role_name": "开发者",
            "data_scope": "SELF",
            "sql_scope": "SELECT_ONLY",
            "sort": 1,
            "data_scope_dept_ids": [d1],
        },
        headers=_auth(token),
    )
    assert r.status_code == 400


async def test_dept_referenced_by_role_delete_conflicts(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """FK 完整性优先:被 CUSTOM 角色引用的部门不可删(409,部门保留)。"""
    _, token = await _admin(client, session, make_user)
    d1 = await _mk_dept(session, "被引用的部")
    r = await client.post(
        "/system/roles",
        json={
            "role_key": "REF_DEPT",
            "role_name": "引用部门",
            "data_scope": "CUSTOM",
            "sort": 0,
            "data_scope_dept_ids": [d1],
        },
        headers=_auth(token),
    )
    assert r.status_code == 201, r.text
    r = await client.delete(f"/system/depts/{d1}", headers=_auth(token))
    assert r.status_code == 409
    assert (
        await session.execute(text("SELECT 1 FROM public.sys_dept WHERE id=:i"), {"i": d1})
    ).scalar_one_or_none() is not None


async def test_role_delete_clears_custom_dept_rows(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """删角色级联清理部门集合(与既有 user_role/role_permission 显式清理同构)。"""
    _, token = await _admin(client, session, make_user)
    d1 = await _mk_dept(session, "待清理的部")
    r = await client.post(
        "/system/roles",
        json={
            "role_key": "TO_PURGE",
            "role_name": "待删",
            "data_scope": "CUSTOM",
            "sort": 0,
            "data_scope_dept_ids": [d1],
        },
        headers=_auth(token),
    )
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    r = await client.delete(f"/system/roles/{rid}", headers=_auth(token))
    assert r.status_code == 204
    assert await _dept_rows(session, rid) == []


# ============ 履约:部门与租户 ============


async def test_dept_tree_and_crud(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    _, token = await _admin(client, session, make_user)
    tid = await _default_tenant_id(session)
    # 建 A → B(子)
    r = await client.post(
        "/system/depts",
        json={"dept_name": "总部A", "tenant_id": tid, "sort": 1},
        headers=_auth(token),
    )
    assert r.status_code == 201, r.text
    a = r.json()["id"]
    r = await client.post(
        "/system/depts",
        json={"parent_id": a, "dept_name": "子部B", "tenant_id": tid, "sort": 2},
        headers=_auth(token),
    )
    b = r.json()["id"]
    # 树:嵌套
    r = await client.get("/system/depts", headers=_auth(token))
    tree = r.json()
    assert len(tree) >= 1
    node_a = next(n for n in tree if n["id"] == a)
    assert [c["id"] for c in node_a["children"]] == [b]
    # 编辑
    r = await client.put(
        f"/system/depts/{b}",
        json={
            "parent_id": a,
            "dept_name": "子部B改",
            "tenant_id": tid,
            "sort": 3,
            "status": "ACTIVE",
        },
        headers=_auth(token),
    )
    assert r.status_code == 204
    # 删除叶子
    r = await client.delete(f"/system/depts/{b}", headers=_auth(token))
    assert r.status_code == 204
    r = await client.delete(f"/system/depts/{a}", headers=_auth(token))
    assert r.status_code == 204
    # 不存在 404
    r = await client.delete(f"/system/depts/{a}", headers=_auth(token))
    assert r.status_code == 404


async def test_tenant_list(client: AsyncClient, session: AsyncSession, make_user: MakeUser) -> None:
    _, token = await _admin(client, session, make_user)
    r = await client.get("/system/tenants", headers=_auth(token))
    assert r.status_code == 200
    keys = {t["tenant_key"] for t in r.json()}
    assert "default" in keys


# ============ 超级管理员保护与密码重置 ============


async def test_reset_password_clears_changed_flag_after_self_change(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """已自助改密(pca 有值)的用户被重置后,首登改密标志必须复位为 NULL。"""
    _, token = await _admin(client, session, make_user)
    uid = await make_user("changed-user")  # make_user 默认 pca=now(已改密)
    r = await client.post(
        f"/system/users/{uid}/reset-password",
        json={"new_password": "Reset#2026x"},
        headers=_auth(token),
    )
    assert r.status_code == 204
    pca = await session.execute(
        text("SELECT password_changed_at FROM public.sys_user WHERE id=:i"), {"i": uid}
    )
    assert pca.scalar_one() is None


async def test_admin_disable_and_role_strip_rejected(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """超管账号不可禁用，也不能解除 SUPER_ADMIN 绑定。"""
    _, token = await _admin(client, session, make_user)
    settings = get_settings()
    admin_uid = str(
        (
            await session.execute(
                text("SELECT id FROM public.sys_user WHERE username=:u"),
                {"u": settings.admin_username},
            )
        ).scalar_one()
    )
    tid = await _default_tenant_id(session)
    super_rid = await _role_id(session, "SUPER_ADMIN")
    base = {
        "nickname": "超管",
        "email": None,
        "dept_id": None,
        "tenant_id": tid,
        "role_ids": [super_rid],
    }
    # 禁用被拒
    r = await client.put(
        f"/system/users/{admin_uid}",
        json={**base, "status": "DISABLED"},
        headers=_auth(token),
    )
    assert r.status_code == 409
    # 剥离 SUPER_ADMIN 被拒
    r = await client.put(
        f"/system/users/{admin_uid}",
        json={**base, "status": "ACTIVE", "role_ids": []},
        headers=_auth(token),
    )
    assert r.status_code == 409
    # 正常编辑(保留 SUPER_ADMIN)放行
    r = await client.put(
        f"/system/users/{admin_uid}",
        json={**base, "status": "ACTIVE", "nickname": "超管改"},
        headers=_auth(token),
    )
    assert r.status_code == 204


async def test_role_key_format_rejected(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """role_key 不接受空白或小写形式，并在传输层返回 422。"""
    _, token = await _admin(client, session, make_user)
    for bad in [" super_admin", "Super_Admin", "lower_case", "1ABC", "A"]:
        r = await client.post(
            "/system/roles",
            json={"role_key": bad, "role_name": "x"},
            headers=_auth(token),
        )
        assert r.status_code == 422, bad


async def test_write_operations_audited(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """管理面写操作与业务同事务落操作审计(动作+目标可追溯)。"""
    _, token = await _admin(client, session, make_user)
    tid = await _default_tenant_id(session)
    # 用户三写
    r = await client.post(
        "/system/users",
        json={
            "username": "audit-user",
            "password": "Initial#2026",
            "nickname": "审计",
            "tenant_id": tid,
            "role_ids": [],
        },
        headers=_auth(token),
    )
    uid = r.json()["id"]
    r = await client.put(
        f"/system/users/{uid}",
        json={"nickname": "审计改", "status": "ACTIVE", "tenant_id": tid, "role_ids": []},
        headers=_auth(token),
    )
    assert r.status_code == 204
    r = await client.post(
        f"/system/users/{uid}/reset-password",
        json={"new_password": "Reset#2026x"},
        headers=_auth(token),
    )
    assert r.status_code == 204
    # 启停动作:专用端点动作进清单断言
    r = await client.post(f"/system/users/{uid}/enable", headers=_auth(token))
    assert r.status_code == 204
    r = await client.post(f"/system/users/{uid}/disable", headers=_auth(token))
    assert r.status_code == 204
    r = await client.delete(f"/system/users/{uid}", headers=_auth(token))
    assert r.status_code == 204
    # 角色与部门
    rid_resp = await client.post(
        "/system/roles",
        json={"role_key": "AUDIT_TMP", "role_name": "审计角色"},
        headers=_auth(token),
    )
    assert rid_resp.status_code == 201
    rid = rid_resp.json()["id"]
    perms = await client.get("/system/permissions", headers=_auth(token))
    assert perms.status_code == 200
    r = await client.post(
        f"/system/roles/{rid}/permissions",
        json={"perm_ids": [perms.json()[0]["id"]]},
        headers=_auth(token),
    )
    assert r.status_code == 204
    r = await client.delete(f"/system/roles/{rid}", headers=_auth(token))
    assert r.status_code == 204
    dept_resp = await client.post(
        "/system/depts",
        json={"dept_name": "审计部", "tenant_id": tid},
        headers=_auth(token),
    )
    assert dept_resp.status_code == 201
    did = dept_resp.json()["id"]
    r = await client.delete(f"/system/depts/{did}", headers=_auth(token))
    assert r.status_code == 204
    # 断言:全部动作都有审计行
    rows = await session.execute(
        text(  # noqa: S608  # 测试常量
            "SELECT action FROM public.sys_oper_log"
            " WHERE action LIKE 'user.%' OR action LIKE 'role.%' OR action LIKE 'dept.%'"
        )
    )
    actions = {str(r[0]) for r in rows}
    expected = {
        "user.create",
        "user.update",
        "user.reset_password",
        "user.delete",
        "user.enable",
        "user.disable",
        "role.create",
        "role.bind_perms",
        "role.delete",
        "dept.create",
        "dept.delete",
    }
    assert expected <= actions, f"缺失审计动作:{expected - actions}"


# ============ 密码修改、令牌吊销与查询语义 ============


async def test_reset_password_revokes_refresh_families(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """管理员重置密码后，用户已有的 refresh token 立即失效。"""
    await seed_all(session, get_settings())
    uid = await make_user("victim2")
    _, token = await _admin(client, session, make_user, "boss2")
    # victim2 登录拿真 refresh token
    victim_pair = await client.post(
        "/auth/login", json={"username": "victim2", "password": "Passw0rd!"}
    )
    refresh = victim_pair.json()["refresh_token"]
    # 管理员重置其密码
    r = await client.post(
        f"/system/users/{uid}/reset-password",
        json={"new_password": "Reset#2026x"},
        headers=_auth(token),
    )
    assert r.status_code == 204
    # 旧 refresh token 不可再换新 access(家族已吊销)
    r = await client.post("/auth/refresh", json={"refresh_token": refresh})
    assert r.status_code == 401


async def test_update_user_password_changes_hash_and_revokes(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """编辑时传入密码会更新哈希并吊销令牌；未传密码时两者都不变。"""
    await seed_all(session, get_settings())
    uid = await make_user("victim3")
    old_hash = (
        await session.execute(
            text("SELECT password_hash FROM public.sys_user WHERE id=:i"), {"i": uid}
        )
    ).scalar_one()
    victim_pair = await client.post(
        "/auth/login", json={"username": "victim3", "password": "Passw0rd!"}
    )
    refresh = victim_pair.json()["refresh_token"]
    _, token = await _admin(client, session, make_user, "boss3")
    tid = await _default_tenant_id(session)
    # 编辑传密码:哈希变 + refresh 失效
    r = await client.put(
        f"/system/users/{uid}",
        json={
            "nickname": "改密",
            "status": "ACTIVE",
            "tenant_id": tid,
            "role_ids": [],
            "password": "Edited#2026x",
        },
        headers=_auth(token),
    )
    assert r.status_code == 204
    new_hash = (
        await session.execute(
            text("SELECT password_hash FROM public.sys_user WHERE id=:i"), {"i": uid}
        )
    ).scalar_one()
    assert new_hash != old_hash
    r = await client.post("/auth/refresh", json={"refresh_token": refresh})
    assert r.status_code == 401


async def test_keyword_like_metachars_escaped(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """关键字中的 %/_/\\ 按字面匹配,不做通配符。"""
    await seed_all(session, get_settings())
    await make_user("sale50pct")  # 无特殊字符的对照行
    await make_user("sale50%")
    await make_user("user_a")
    await make_user("userxa")
    _, token = await _admin(client, session, make_user, "boss4")
    # 「50%」不转义时 % 是通配符,会同时命中 sale50pct/sale50%;
    # 转义后仅字面含「50%」的行命中
    r = await client.get("/system/users", params={"keyword": "50%"}, headers=_auth(token))
    assert r.status_code == 200
    names = [i["username"] for i in r.json()["items"]]
    assert names == ["sale50%"]
    # 「user_a」不转义时 _ 通配任意单字符,命中 user_a/userxa;转义后仅字面命中
    r = await client.get("/system/users", params={"keyword": "user_a"}, headers=_auth(token))
    names = [i["username"] for i in r.json()["items"]]
    assert names == ["user_a"]


async def test_error_detail_semantics(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """404/409/400 的 detail 文案非空(web 直接透传展示,空文案=用户不可读)。"""
    _, token = await _admin(client, session, make_user)
    ghost = str(uuidlib.uuid4())
    r = await client.delete(f"/system/users/{ghost}", headers=_auth(token))
    assert r.status_code == 404
    assert r.json()["detail"]  # noqa: PT018  # 非空文案断言
    rid = await _role_id(session, "SUPER_ADMIN")
    r = await client.delete(f"/system/roles/{rid}", headers=_auth(token))
    assert r.status_code == 409
    assert r.json()["detail"]


# ============ 管理台状态启停范式(专用端点+禁用吊销) ============


async def test_disable_user_revokes_refresh(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """专用禁用端点 = 状态翻转 + 踢下线(旧 refresh 家族吊销)+ 审计。"""
    await seed_all(session, get_settings())
    uid = await make_user("victim4")
    victim_pair = await client.post(
        "/auth/login", json={"username": "victim4", "password": "Passw0rd!"}
    )
    refresh = victim_pair.json()["refresh_token"]
    _, token = await _admin(client, session, make_user, "boss5")
    r = await client.post(f"/system/users/{uid}/disable", headers=_auth(token))
    assert r.status_code == 204
    # 状态已翻转
    status = (
        await session.execute(text("SELECT status FROM public.sys_user WHERE id=:i"), {"i": uid})
    ).scalar_one()
    assert status == "DISABLED"
    # 禁用即吊销:旧 refresh 不可续换(与改密踢下线对称)
    r = await client.post("/auth/refresh", json={"refresh_token": refresh})
    assert r.status_code == 401
    # 判别步:401 也可能仅由禁用状态导致(refresh 有 is_active 闸);
    # 重新启用后旧 refresh 仍 401 才证明「家族吊销」独立成立,防 revoke 被静默删掉
    r = await client.post(f"/system/users/{uid}/enable", headers=_auth(token))
    assert r.status_code == 204
    r = await client.post("/auth/refresh", json={"refresh_token": refresh})
    assert r.status_code == 401
    # 审计动作落库
    n = await session.execute(
        text("SELECT count(*) FROM public.sys_oper_log WHERE action='user.disable'")
    )
    assert n.scalar_one() == 1


async def test_enable_user_restores_login(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """启用端点恢复登录,不涉及吊销;审计 user.enable。"""
    await seed_all(session, get_settings())
    uid = await make_user("victim5")
    _, token = await _admin(client, session, make_user, "boss6")
    r = await client.post(f"/system/users/{uid}/disable", headers=_auth(token))
    assert r.status_code == 204
    r = await client.post("/auth/login", json={"username": "victim5", "password": "Passw0rd!"})
    assert r.status_code == 403  # 禁用后登录被拒
    r = await client.post(f"/system/users/{uid}/enable", headers=_auth(token))
    assert r.status_code == 204
    r = await client.post("/auth/login", json={"username": "victim5", "password": "Passw0rd!"})
    assert r.status_code == 200  # 启用后恢复(密码未动)
    n = await session.execute(
        text("SELECT count(*) FROM public.sys_oper_log WHERE action='user.enable'")
    )
    assert n.scalar_one() == 1


async def test_update_user_status_disabled_also_revokes(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """将用户状态改为 DISABLED 后，已有令牌也会失效。"""
    await seed_all(session, get_settings())
    uid = await make_user("victim6")
    victim_pair = await client.post(
        "/auth/login", json={"username": "victim6", "password": "Passw0rd!"}
    )
    refresh = victim_pair.json()["refresh_token"]
    _, token = await _admin(client, session, make_user, "boss7")
    tid = await _default_tenant_id(session)
    r = await client.put(
        f"/system/users/{uid}",
        json={
            "nickname": "禁用",
            "status": "DISABLED",
            "tenant_id": tid,
            "role_ids": [],
        },
        headers=_auth(token),
    )
    assert r.status_code == 204
    r = await client.post("/auth/refresh", json={"refresh_token": refresh})
    assert r.status_code == 401
    # 判别步:重新启用后旧 refresh 仍 401 才锁住「编辑路径同吊销」
    r = await client.put(
        f"/system/users/{uid}",
        json={
            "nickname": "解禁",
            "status": "ACTIVE",
            "tenant_id": tid,
            "role_ids": [],
        },
        headers=_auth(token),
    )
    assert r.status_code == 204
    r = await client.post("/auth/refresh", json={"refresh_token": refresh})
    assert r.status_code == 401


async def test_status_toggle_protections(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """专用端点保护与编辑路径同构——自禁 409/超管禁 409/不存在 404。"""
    await seed_all(session, get_settings())
    admin_uid, token = await _admin(client, session, make_user, "boss8")
    # 自禁
    r = await client.post(f"/system/users/{admin_uid}/disable", headers=_auth(token))
    assert r.status_code == 409
    # 超管账号(settings.admin_username)禁用
    real_admin_id = (
        await session.execute(text("SELECT id FROM public.sys_user WHERE username='admin'"))
    ).scalar_one()
    r = await client.post(f"/system/users/{real_admin_id}/disable", headers=_auth(token))
    assert r.status_code == 409
    # 不存在
    r = await client.post(f"/system/users/{uuidlib.uuid4()}/disable", headers=_auth(token))
    assert r.status_code == 404
    r = await client.post(f"/system/users/{uuidlib.uuid4()}/enable", headers=_auth(token))
    assert r.status_code == 404
