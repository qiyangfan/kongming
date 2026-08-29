"""036 清偿包 api 侧测试(测试先行):孤儿附件清理/team_id 回传/
收藏切换原子化。"""
# pyright: reportPrivateUsage=false

import asyncio

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.seed import seed_all
from kongming.config import get_settings
from tests.conftest import MakeUser
from tests.test_chat_query import _default_tenant_id
from tests.test_store import BOT2, _mk_store_bot, _user


async def _mk_team_full(client: AsyncClient, session: AsyncSession, key: str) -> tuple[str, str]:
    """超管建团队(team:manage;创建者自动 OWNER);返回 (team_id, admin_token)。"""
    from tests.test_rag_admin import _admin_token

    admin = await _admin_token(client, session)
    r = await client.post(
        "/system/teams",
        headers={"Authorization": f"Bearer {admin}"},
        json={"team_key": key, "name": "清偿组"},
    )
    assert r.status_code == 201, r.text
    return str(r.json()["id"]), admin


# ── ⑤孤儿附件清理 ──


async def test_orphan_attachment_cleanup(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「孤儿附件定期清理」:超 24h UPLOADED 行+文件双删;新附件不动。"""
    from kongming.attachment.repository import ATTACH_DIR, SqlAttachmentRepo

    await seed_all(session, get_settings())
    tid = await _default_tenant_id(session)
    uid = await make_user("d36_att")
    repo = SqlAttachmentRepo(session)
    old_id = await repo.insert(
        tenant_id=str(tid), user_id=uid, filename="old.png",
        mime="image/png", size_bytes=4,
    )
    await repo.write_file(old_id, "image/png", b"1234")
    new_id = await repo.insert(
        tenant_id=str(tid), user_id=uid, filename="new.png",
        mime="image/png", size_bytes=4,
    )
    await repo.write_file(new_id, "image/png", b"5678")
    await session.execute(
        text(
            "UPDATE public.sys_attachment SET created_at=now()-interval '25 hours'"
            " WHERE id=CAST(:i AS uuid)"
        ),
        {"i": old_id},
    )
    await session.commit()
    old_path = ATTACH_DIR / f"{old_id}.png"
    new_path = ATTACH_DIR / f"{new_id}.png"
    assert old_path.exists() and new_path.exists()

    from kongming.attachment.cleanup import cleanup_orphan_attachments

    async with __import__("kongming.db", fromlist=["get_sessionmaker"]).get_sessionmaker()() as s2:
        n = await cleanup_orphan_attachments(s2)
    assert n == 1  # 仅超期那件
    assert not old_path.exists() and new_path.exists()
    rows = (
        await session.execute(
            text("SELECT id FROM public.sys_attachment WHERE id=CAST(:i AS uuid)"),
            {"i": old_id},
        )
    ).all()
    assert rows == []


# ── ⑥team_id 回传 ──


async def test_bot_list_out_has_team_id(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「归属字段回传」:Bot 列表响应含 team_id(团队资源=团队 id,
    个人=null)。"""
    _uid, token = await _user(client, session, make_user, "d36_b")
    team_id, admin = await _mk_team_full(client, session, key="d36b")
    bot_id = await _mk_store_bot(client, token)
    r = await client.post(
        f"/system/teams/{team_id}/attach",
        headers={"Authorization": f"Bearer {admin}"},
        json={"resource_type": "bot", "resource_id": bot_id},
    )
    assert r.status_code == 200, r.text
    r2 = await client.get(
        "/system/bots", headers={"Authorization": f"Bearer {token}"}
    )
    mine = [b for b in r2.json() if b["id"] == bot_id]
    assert mine and mine[0]["team_id"] == team_id
    # 个人 Bot 回传 null(换键防撞)
    r_new = await client.post(
        "/system/bots",
        headers={"Authorization": f"Bearer {token}"},
        json={**BOT2, "bot_key": "store_bot2"},
    )
    assert r_new.status_code == 201, r_new.text
    bot2 = str(r_new.json()["id"])
    r3 = await client.get(
        "/system/bots", headers={"Authorization": f"Bearer {token}"}
    )
    personal = [b for b in r3.json() if b["id"] == bot2]
    assert personal and personal[0]["team_id"] is None


async def test_memory_list_out_has_team_id(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「归属字段回传」:记忆列表响应含 team_id。"""
    uid, token = await _user(client, session, make_user, "d36_m")
    team_id, admin = await _mk_team_full(client, session, key="d36m")
    # 用户加为 MEMBER 才可写团队记忆(032 端点语义)
    rm = await client.post(
        f"/system/teams/{team_id}/members",
        headers={"Authorization": f"Bearer {admin}"},
        json={"user_id": uid, "role": "MEMBER"},
    )
    assert rm.status_code == 200, rm.text
    r = await client.post(
        "/memory",
        headers={"Authorization": f"Bearer {token}"},
        json={"content": "团队偏好:回答带表格", "team_id": team_id},
    )
    assert r.status_code == 201, r.text
    r2 = await client.post(
        "/memory",
        headers={"Authorization": f"Bearer {token}"},
        json={"content": "个人偏好:简短回答"},
    )
    assert r2.status_code == 201, r2.text
    r3 = await client.get("/memory", headers={"Authorization": f"Bearer {token}"})
    items = r3.json()["items"]
    team_items = [i for i in items if i.get("team_id") == team_id]
    personal = [i for i in items if i.get("team_id") is None]
    assert team_items, "团队记忆应回传 team_id"
    assert personal, "个人记忆 team_id 应为 null"


# ── ⑥收藏切换原子化 ──


async def test_favorite_set_semantics_concurrent(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """审查循环 F6:收藏改 set 幂等语义(原 toggle 并发双击会互相抵消,
    HTTP POST 非幂等违 spec「重复操作幂等」)——并发双 POST 终态恒为已收藏。"""
    _, token = await _user(client, session, make_user, "d36_f")
    bot_id = await _mk_store_bot(client, token)
    await client.post(
        f"/system/bots/{bot_id}/publish",
        headers={"Authorization": f"Bearer {token}"},
        json={"greeting": "hi"},
    )
    h = {"Authorization": f"Bearer {token}"}
    # 并发双击收藏:两次响应均 favorited=true,终态恰一行
    results = await asyncio.gather(
        client.post("/store/bots/store_bot/favorite", headers=h),
        client.post("/store/bots/store_bot/favorite", headers=h),
    )
    assert all(r.json()["favorited"] is True for r in results)
    final = (await client.get("/store/favorites", headers=h)).json()
    assert len(final) == 1
    # 并发双击取消:终态无行
    results2 = await asyncio.gather(
        client.delete("/store/bots/store_bot/favorite", headers=h),
        client.delete("/store/bots/store_bot/favorite", headers=h),
    )
    assert all(r.json()["favorited"] is False for r in results2)
    assert (await client.get("/store/favorites", headers=h)).json() == []

