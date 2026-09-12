"""032 team-workspace 测试:角色边界矩阵/可见性过滤/归属变更/团队记忆
(测试先行,安全类)。"""
# pyright: reportPrivateUsage=false

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.seed import seed_all
from kongming.config import get_settings
from kongming.team.models import can_edit, can_manage, visible
from tests.conftest import MakeUser
from tests.test_chat_query import _bind, _login
from tests.test_toolcall import _mk_role_with_perms, _mk_tenant

# ── 可见性纯函数 ──


def test_visibility_matrix() -> None:
    # 个人资源:仅创建者
    assert visible(team_id=None, creator_id="c", user_id="c", member_role=None) is True
    assert visible(team_id=None, creator_id="c", user_id="x", member_role=None) is False
    # 团队资源:成员即可见(角色不分)
    for role in ("VIEWER", "MEMBER", "OWNER"):
        assert visible(team_id="t", creator_id="c", user_id="m", member_role=role) is True
    assert visible(team_id="t", creator_id="c", user_id="m", member_role=None) is False
    # 编辑:MEMBER+
    assert can_edit(team_id="t", creator_id="c", user_id="m", member_role="VIEWER") is False
    assert can_edit(team_id="t", creator_id="c", user_id="m", member_role="MEMBER") is True
    # 全权:OWNER
    assert can_manage(team_id="t", creator_id="c", user_id="m", member_role="MEMBER") is False
    assert can_manage(team_id="t", creator_id="c", user_id="m", member_role="OWNER") is True


# ── HTTP 矩阵 ──


async def _mk_users(
    client: AsyncClient, s: AsyncSession, make_user: MakeUser
) -> dict[str, tuple[str, str]]:
    """owner(超管)/member/viewer/outsider 四角(同租户 tg)。"""
    await seed_all(s, get_settings())
    tid = await _mk_tenant(s, "tg")
    out: dict[str, tuple[str, str]] = {}
    for name, codes in (
        ("tm_owner", ["system:team:manage"]),
        ("tm_member", []),
        ("tm_viewer", []),
        ("tm_out", []),
    ):
        uid = await make_user(name)
        rid = await _mk_role_with_perms(s, f"role_{name}", codes)
        await _bind(s, "sys_user_role", uid, rid)
        await _bind(s, "sys_user_tenant", uid, tid)
        out[name] = (uid, await _login(client, name))
    await s.commit()
    return out


async def _mk_team(client: AsyncClient, token: str, key: str = "alpha") -> str:
    r = await client.post(
        "/system/teams",
        headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": "tg"},
        json={"team_key": key, "name": "阿尔法组"},
    )
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-Tenant-Id": "tg"}


async def test_team_crud_and_member_management(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    users = await _mk_users(client, session, make_user)
    _, owner_token = users["tm_owner"]
    member_uid, member_token = users["tm_member"]
    viewer_uid, viewer_token = users["tm_viewer"]
    tid = await _mk_team(client, owner_token)
    # 加成员(OWNER 操作)
    r = await client.post(
        f"/system/teams/{tid}/members",
        headers=_h(owner_token),
        json={"user_id": member_uid, "role": "MEMBER"},
    )
    assert r.status_code == 200
    await client.post(
        f"/system/teams/{tid}/members",
        headers=_h(owner_token),
        json={"user_id": viewer_uid, "role": "VIEWER"},
    )
    # VIEWER 加成员被拒(角色不足)
    r2 = await client.post(
        f"/system/teams/{tid}/members",
        headers=_h(viewer_token),
        json={"user_id": users["tm_out"][0], "role": "VIEWER"},
    )
    assert r2.status_code == 403
    # MEMBER 管理成员被拒
    r3 = await client.post(
        f"/system/teams/{tid}/members",
        headers=_h(member_token),
        json={"user_id": users["tm_out"][0], "role": "VIEWER"},
    )
    assert r3.status_code == 403
    # 末位 OWNER 保护:先移除自己(唯一 OWNER)→ 409
    owner_uid = users["tm_owner"][0]
    r4 = await client.delete(f"/system/teams/{tid}/members/{owner_uid}", headers=_h(owner_token))
    assert r4.status_code == 409
    # 我所在的团队(成员关系过滤)
    r5 = await client.get("/system/teams", headers=_h(viewer_token))
    assert any(t["id"] == tid for t in r5.json())
    r6 = await client.get("/system/teams", headers=_h(users["tm_out"][1]))
    assert all(t["id"] != tid for t in r6.json())


async def test_team_isolation_cross_tenant(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    users = await _mk_users(client, session, make_user)
    owner_uid, owner_token = users["tm_owner"]
    tid = await _mk_team(client, owner_token, key="iso")
    # owner 兼绑 default 租户,显式 default 视角 → 团队在 tg,404 同形
    from tests.test_chat_query import _default_tenant_id

    await _bind(session, "sys_user_tenant", owner_uid, await _default_tenant_id(session))
    await session.commit()
    r = await client.get(
        f"/system/teams/{tid}",
        headers={"Authorization": f"Bearer {owner_token}", "X-Tenant-Id": "default"},
    )
    assert r.status_code == 404


async def test_bot_visibility_by_team(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec 矩阵:团队 Bot VIEWER 可见/MEMBER 可编辑/非成员不可见;个人零回归。"""
    from tests.test_bot import BOT_PAYLOAD

    users = await _mk_users(client, session, make_user)
    _owner_uid, owner_token = users["tm_owner"]
    member_uid, member_token = users["tm_member"]
    _, viewer_token = users["tm_viewer"]
    _, out_token = users["tm_out"]
    tid = await _mk_team(client, owner_token)
    await client.post(
        f"/system/teams/{tid}/members",
        headers=_h(owner_token),
        json={"user_id": member_uid, "role": "MEMBER"},
    )
    await client.post(
        f"/system/teams/{tid}/members",
        headers=_h(owner_token),
        json={"user_id": users["tm_viewer"][0], "role": "VIEWER"},
    )
    # 给 owner 建 bot(需 DEVELOPER 权限——tm_owner 的角色只有 team:manage,
    # 补 chat:execute 系列)
    rid = (
        await session.execute(text("SELECT id FROM public.sys_role WHERE role_key='DEVELOPER'"))
    ).scalar_one()
    for uid, _ in users.values():
        await _bind(session, "sys_user_role", uid, str(rid))
    await session.commit()
    r = await client.post("/system/bots", headers=_h(owner_token), json=BOT_PAYLOAD)
    bot_id = r.json()["id"]
    # 归属变更到团队
    r2 = await client.post(
        f"/system/teams/{tid}/attach",
        headers=_h(owner_token),
        json={"resource_type": "bot", "resource_id": bot_id},
    )
    assert r2.status_code == 200, r2.text
    # 归属生效核验(调试锚:team_id 必须已落)
    bot_team = (
        await session.execute(
            text("SELECT team_id FROM public.sys_bot_definition WHERE id=CAST(:i AS uuid)"),
            {"i": bot_id},
        )
    ).scalar_one()
    assert str(bot_team) == tid, f"attach 未生效: {bot_team!r}"
    # 团队列:VIEWER 可见
    r3 = await client.get("/system/bots", headers=_h(viewer_token))
    assert any(b["id"] == bot_id for b in r3.json())
    # VIEWER 编辑被拒(403)
    r4 = await client.put(f"/system/bots/{bot_id}", headers=_h(viewer_token), json={"name": "改名"})
    assert r4.status_code == 403
    # MEMBER 可编辑
    r5 = await client.put(
        f"/system/bots/{bot_id}", headers=_h(member_token), json={"name": "改名后"}
    )
    assert r5.status_code == 204, r5.text
    # 非成员列表不含+编辑 404 同形
    r6 = await client.get("/system/bots", headers=_h(out_token))
    assert all(b["id"] != bot_id for b in r6.json())
    r7 = await client.put(f"/system/bots/{bot_id}", headers=_h(out_token), json={"name": "x"})
    assert r7.status_code in (403, 404)


async def test_workflow_visibility_by_team(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec 矩阵:团队工作流 MEMBER 可见可运行/非成员触发 404。"""
    users = await _mk_users(client, session, make_user)
    owner_uid, owner_token = users["tm_owner"]
    member_uid, member_token = users["tm_member"]
    _, out_token = users["tm_out"]
    rid = (
        await session.execute(text("SELECT id FROM public.sys_role WHERE role_key='DEVELOPER'"))
    ).scalar_one()
    # outsider 也绑(workflow:execute 是运行端点门槛;可见性 404 须先过权限闸)
    for uid in (owner_uid, member_uid, users["tm_out"][0]):
        await _bind(session, "sys_user_role", uid, str(rid))
    # 加 system:workflow:manage 给 owner
    prid = await _mk_role_with_perms(session, "role_wf_mgr", ["system:workflow:manage"])
    await _bind(session, "sys_user_role", owner_uid, prid)
    await session.commit()
    r = await client.post(
        "/system/workflow",
        headers=_h(owner_token),
        json={
            "wf_key": "tv",
            "name": "可见性",
            "definition": {
                "nodes": [{"id": "t", "type": "transform", "config": {"template": "x"}}]
            },
        },
    )
    assert r.status_code == 201, r.text
    tid = await _mk_team(client, owner_token)
    await client.post(
        f"/system/teams/{tid}/members",
        headers=_h(owner_token),
        json={"user_id": member_uid, "role": "MEMBER"},
    )
    # 归属
    wf_row = (
        await session.execute(text("SELECT id FROM public.sys_workflow_def WHERE wf_key='tv'"))
    ).scalar_one()
    r2 = await client.post(
        f"/system/teams/{tid}/attach",
        headers=_h(owner_token),
        json={"resource_type": "workflow", "resource_id": str(wf_row)},
    )
    assert r2.status_code == 200
    # 管理面列表(owner 持 manage):归属团队后仍可见
    r3 = await client.get("/system/workflow", headers=_h(owner_token))
    assert any(w["wf_key"] == "tv" for w in r3.json())
    # MEMBER(workflow:execute)可运行团队工作流(可见性放行)
    r4 = await client.post("/system/workflow/tv/run", headers=_h(member_token), json={"inputs": {}})
    assert r4.status_code == 200, r4.text
    assert r4.json()["status"] == "SUCCESS"
    # 非成员触发 404 同形
    r6 = await client.post("/system/workflow/tv/run", headers=_h(out_token), json={"inputs": {}})
    assert r6.status_code == 404


async def test_attach_cross_tenant_rejected(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """R1-S1 回归:attach 其他租户的资源 → 404 同形。"""
    users = await _mk_users(client, session, make_user)
    owner_uid, owner_token = users["tm_owner"]
    tid = await _mk_team(client, owner_token)
    # 在 default 租户造资源(不属于 tg)
    from tests.test_chat_query import _default_tenant_id

    dtid = await _default_tenant_id(session)
    rid = (
        await session.execute(text("SELECT id FROM public.sys_role WHERE role_key='DEVELOPER'"))
    ).scalar_one()
    await _bind(session, "sys_user_role", owner_uid, str(rid))
    await _bind(session, "sys_user_tenant", owner_uid, dtid)
    await session.commit()
    r = await client.post(
        "/system/bots",
        headers={"Authorization": f"Bearer {owner_token}"},  # default 视角
        json={
            "bot_key": "other_tenant_bot",
            "name": "他租户",
            "system_prompt": "x",
            "tools": [],
            "datasource_key": "default",
        },
    )
    bot_id = r.json()["id"]
    # 把 default 租户的 bot 挂到 tg 团队 → 404(资源不属当前租户)
    r2 = await client.post(
        f"/system/teams/{tid}/attach",
        headers=_h(owner_token),
        json={"resource_type": "bot", "resource_id": bot_id},
    )
    assert r2.status_code == 404


async def test_workflow_new_version_gate_and_inherit(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """R1-S2 回归:非成员对他人团队工作流存新版本 → 403;MEMBER 存
    新版本继承 team_id(不失守)。"""
    users = await _mk_users(client, session, make_user)
    owner_uid, owner_token = users["tm_owner"]
    member_uid, member_token = users["tm_member"]
    _, out_token = users["tm_out"]
    prid = await _mk_role_with_perms(session, "role_wfm2", ["system:workflow:manage"])
    for uid in (owner_uid, member_uid, users["tm_out"][0]):
        await _bind(session, "sys_user_role", uid, prid)
    rid = (
        await session.execute(text("SELECT id FROM public.sys_role WHERE role_key='DEVELOPER'"))
    ).scalar_one()
    for uid in (owner_uid, member_uid, users["tm_out"][0]):
        await _bind(session, "sys_user_role", uid, str(rid))
    await session.commit()
    # owner 建工作流并挂团队
    r = await client.post(
        "/system/workflow",
        headers=_h(owner_token),
        json={
            "wf_key": "gate",
            "name": "闸",
            "definition": {
                "nodes": [{"id": "t", "type": "transform", "config": {"template": "x"}}]
            },
        },
    )
    assert r.status_code == 201, r.text
    tid = await _mk_team(client, owner_token)
    await client.post(
        f"/system/teams/{tid}/members",
        headers=_h(owner_token),
        json={"user_id": member_uid, "role": "MEMBER"},
    )
    wf_row = (
        await session.execute(text("SELECT id FROM public.sys_workflow_def WHERE wf_key='gate'"))
    ).scalar_one()
    await client.post(
        f"/system/teams/{tid}/attach",
        headers=_h(owner_token),
        json={"resource_type": "workflow", "resource_id": str(wf_row)},
    )
    # 非成员存新版本 → 403(编辑闸)
    r2 = await client.post(
        "/system/workflow",
        headers=_h(out_token),
        json={
            "wf_key": "gate",
            "name": "闸v2",
            "definition": {
                "nodes": [{"id": "t", "type": "transform", "config": {"template": "y"}}]
            },
        },
    )
    assert r2.status_code == 403
    # MEMBER 存新版本 → 201 且继承 team_id
    r3 = await client.post(
        "/system/workflow",
        headers=_h(member_token),
        json={
            "wf_key": "gate",
            "name": "闸v2",
            "definition": {
                "nodes": [{"id": "t", "type": "transform", "config": {"template": "y"}}]
            },
        },
    )
    assert r3.status_code == 201, r3.text
    new_team = (
        await session.execute(
            text("SELECT team_id FROM public.sys_workflow_def WHERE wf_key='gate' AND version=2")
        )
    ).scalar_one()
    assert str(new_team) == tid


async def test_member_ops_cross_tenant_rejected(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """R1-S3 回归:manage 持点者跨租户改/删成员 → 404。"""
    users = await _mk_users(client, session, make_user)
    owner_uid, owner_token = users["tm_owner"]
    member_uid, _ = users["tm_member"]
    tid = await _mk_team(client, owner_token)
    from tests.test_chat_query import _default_tenant_id

    await _bind(session, "sys_user_tenant", owner_uid, await _default_tenant_id(session))
    await session.commit()
    # default 视角(manage 权限点在身)改 tg 团队成员 → 404
    h_default = {"Authorization": f"Bearer {owner_token}", "X-Tenant-Id": "default"}
    r = await client.put(
        f"/system/teams/{tid}/members/{member_uid}",
        headers=h_default,
        json={"user_id": member_uid, "role": "OWNER"},
    )
    assert r.status_code == 404
    r2 = await client.delete(f"/system/teams/{tid}/members/{member_uid}", headers=h_default)
    assert r2.status_code == 404


async def test_left_team_member_not_recalled(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """R1-J6 回归:离队成员不再召回自己写的团队记忆(个人分支限
    team_id IS NULL)。"""
    users = await _mk_users(client, session, make_user)
    _, owner_token = users["tm_owner"]
    member_uid, member_token = users["tm_member"]
    rid = (
        await session.execute(text("SELECT id FROM public.sys_role WHERE role_key='DEVELOPER'"))
    ).scalar_one()
    await _bind(session, "sys_user_role", member_uid, str(rid))
    await session.commit()
    tid = await _mk_team(client, owner_token)
    await client.post(
        f"/system/teams/{tid}/members",
        headers=_h(owner_token),
        json={"user_id": member_uid, "role": "MEMBER"},
    )
    r = await client.post(
        "/memory",
        headers=_h(member_token),
        json={"content": "离队后不应召回的口径", "team_id": tid},
    )
    assert r.status_code == 201
    # 降级 VIEWER 仍成员——仍在召回面;移除成员后个人分支不再命中
    await client.post(
        f"/system/teams/{tid}/members",
        headers=_h(owner_token),
        json={"user_id": users["tm_owner"][0], "role": "OWNER"},
    )
    r2 = await client.delete(f"/system/teams/{tid}/members/{member_uid}", headers=_h(owner_token))
    assert r2.status_code == 200
    # SQL 语义直证:个人分支(team_id IS NULL)不再命中该行
    n = (
        await session.execute(
            text(
                "SELECT count(*) FROM public.user_memory m"
                " WHERE m.user_id=CAST(:u AS uuid) AND m.team_id IS NULL"
                " AND m.content LIKE '离队后%'"
            ),
            {"u": member_uid},
        )
    ).scalar_one()
    assert n == 0


async def test_team_memory_write_and_recall(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec:VIEWER 写入拒;MEMBER 写入可;召回双路(团队记忆进 member 召回,
    不进 outsider)。"""
    users = await _mk_users(client, session, make_user)
    _, owner_token = users["tm_owner"]
    member_uid, member_token = users["tm_member"]
    viewer_uid, viewer_token = users["tm_viewer"]
    out_uid, _out_token = users["tm_out"]
    rid = (
        await session.execute(text("SELECT id FROM public.sys_role WHERE role_key='DEVELOPER'"))
    ).scalar_one()
    for uid in (member_uid, viewer_uid, out_uid):
        await _bind(session, "sys_user_role", uid, str(rid))
    await session.commit()
    tid = await _mk_team(client, owner_token)
    await client.post(
        f"/system/teams/{tid}/members",
        headers=_h(owner_token),
        json={"user_id": member_uid, "role": "MEMBER"},
    )
    await client.post(
        f"/system/teams/{tid}/members",
        headers=_h(owner_token),
        json={"user_id": viewer_uid, "role": "VIEWER"},
    )
    # VIEWER 写入拒
    r = await client.post(
        "/memory",
        headers=_h(viewer_token),
        json={"content": "GMV 口径不含退款", "team_id": tid},
    )
    assert r.status_code == 403
    # MEMBER 写入可
    r2 = await client.post(
        "/memory",
        headers=_h(member_token),
        json={"content": "GMV 口径不含退款", "team_id": tid},
    )
    assert r2.status_code == 201, r2.text
    # 落库 team_id
    tm = (
        await session.execute(
            text("SELECT team_id FROM public.user_memory WHERE content LIKE 'GMV%'")
        )
    ).scalar_one()
    assert str(tm) == tid
    # 召回双路直证(service 级,向量无 embedding 时走降级——此处直接查
    # recall SQL 语义:member 的团队记忆可见,outsider 不可见)

    rows = (
        await session.execute(
            text(
                "SELECT count(*) FROM public.user_memory m"
                " WHERE (m.user_id=CAST(:u AS uuid) OR m.team_id=CAST(:t AS uuid))"
            ),
            {"u": member_uid, "t": tid},
        )
    ).scalar_one()
    assert rows == 1
    rows_out = (
        await session.execute(
            text(
                "SELECT count(*) FROM public.user_memory m"
                " WHERE m.user_id=CAST(:u AS uuid) AND m.team_id IS NULL"
            ),
            {"u": out_uid},
        )
    ).scalar_one()
    assert rows_out == 0  # outsider 无个人记忆,团队记忆不可达


async def test_team_bot_key_revoke_requires_can_manage(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """审查循环 F2:revoke_key 曾缺 can_manage 闸——团队 VIEWER 可吊销团队
    Bot 的 api-key(与 create/list 两端点自相矛盾);补闸后 VIEWER 403。"""
    from tests.test_bot import BOT_PAYLOAD

    users = await _mk_users(client, session, make_user)
    _owner_uid, owner_token = users["tm_owner"]
    _, viewer_token = users["tm_viewer"]
    tid = await _mk_team(client, owner_token)
    await client.post(
        f"/system/teams/{tid}/members",
        headers=_h(owner_token),
        json={"user_id": users["tm_viewer"][0], "role": "VIEWER"},
    )
    rid = (
        await session.execute(text("SELECT id FROM public.sys_role WHERE role_key='DEVELOPER'"))
    ).scalar_one()
    for uid, _ in users.values():
        await _bind(session, "sys_user_role", uid, str(rid))
    await session.commit()
    bot_id = (await client.post("/system/bots", headers=_h(owner_token), json=BOT_PAYLOAD)).json()[
        "id"
    ]
    await client.post(
        f"/system/teams/{tid}/attach",
        headers=_h(owner_token),
        json={"resource_type": "bot", "resource_id": bot_id},
    )
    key_id = (
        await client.post(f"/system/bots/{bot_id}/keys?name=k", headers=_h(owner_token))
    ).json()["id"]
    # VIEWER 可见团队 Bot(get_mine 命中),但吊销须 can_manage → 403
    r = await client.delete(f"/system/bots/keys/{key_id}", headers=_h(viewer_token))
    assert r.status_code == 403
    # 创建者吊销照常
    r2 = await client.delete(f"/system/bots/keys/{key_id}", headers=_h(owner_token))
    assert r2.status_code == 204
