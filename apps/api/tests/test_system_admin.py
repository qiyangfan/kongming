"""system 管理端点全栈测试(007,安全规则类:保护规则先行验证)。

真实 PG/Redis;数据直插 SQL(arrange 独立于被测实现)。
安全面:无权限 403+审计、内置角色防删、role_key 不可改、超管/自删/自禁
保护、角色/部门删除前置校验、禁用用户令牌即时失效、部门防成环。
"""

import uuid as uuidlib

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
        (await s.execute(text("SELECT id FROM public.sys_role WHERE role_key=:k"), {"k": key}))
        .scalar_one()
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


async def test_unauthenticated_401(client: AsyncClient) -> None:
    r = await client.get("/system/users")
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
    """禁用用户后既有令牌在下一次受权限点校验的请求即 401(rbac delta 007)。"""
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
    # 编辑回显所需 id 字段(缺失曾致编辑对话框静默清空部门绑定)
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
            text(
                "SELECT password_hash, nickname, dept_id, email FROM public.sys_user"
                " WHERE id=:i"
            ),
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
    body = {
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
    await session.execute(
        text("UPDATE public.sys_user SET deleted_at=now() WHERE username='dup'")
    )
    await session.commit()
    r = await client.post("/system/users", json=body, headers=_auth(token))
    assert r.status_code == 201


async def test_create_user_unknown_refs_rejected(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    _, token = await _admin(client, session, make_user)
    base = {
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

async def _role_by_key(client: AsyncClient, token: str, key: str) -> dict:
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


# ============ 评审修复锁定(007 R1:严重缺陷回归锚) ============


async def test_reset_password_clears_changed_flag_after_self_change(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """S1:已自助改密(pca 有值)的用户被重置后,首登改密标志必须复位为 NULL。"""
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
    """R1:超管账号不可禁用、不可剥离 SUPER_ADMIN 绑定(锁定/接管向量)。"""
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
    """R4:role_key 空白/小写近形变体在传输层即拒(422)。"""
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
    """S2:管理面写操作与业务同事务落操作审计(动作+目标可追溯)。"""
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
        "user.create", "user.update", "user.reset_password", "user.delete",
        "role.create", "role.bind_perms", "role.delete",
        "dept.create", "dept.delete",
    }
    assert expected <= actions, f"缺失审计动作:{expected - actions}"


# ============ R1 审查修复锁定(改密吊销/编辑改密/关键字转义/detail 语义) ============


async def test_reset_password_revokes_refresh_families(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """R1 严重 1:管理面重置密码 = 强制踢下线,既有 refresh token 立即失效。"""
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
    """R1 严重 1:编辑传密码 → 哈希变更 + 吊销家族;不传密码 → 均不动。"""
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
    """R1-3:关键字中的 %/_/\\ 按字面匹配,不做通配符。"""
    await seed_all(session, get_settings())
    await make_user("sale50pct")  # 无特殊字符的对照行
    await make_user("sale50%")
    await make_user("user_a")
    await make_user("userxa")
    _, token = await _admin(client, session, make_user, "boss4")
    # 「50%」不转义时 % 是通配符,会同时命中 sale50pct/sale50%;
    # 转义后仅字面含「50%」的行命中
    r = await client.get(
        "/system/users", params={"keyword": "50%"}, headers=_auth(token)
    )
    assert r.status_code == 200
    names = [i["username"] for i in r.json()["items"]]
    assert names == ["sale50%"]
    # 「user_a」不转义时 _ 通配任意单字符,命中 user_a/userxa;转义后仅字面命中
    r = await client.get(
        "/system/users", params={"keyword": "user_a"}, headers=_auth(token)
    )
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
