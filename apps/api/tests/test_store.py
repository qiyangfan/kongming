"""033 bot-store 测试:上架治理(归属/护栏)/发现/互动/直跑计数
(测试先行,安全类)。"""
# pyright: reportPrivateUsage=false

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.seed import seed_all
from kongming.config import get_settings
from tests.conftest import MakeUser
from tests.test_bot import BOT_PAYLOAD
from tests.test_chat_query import _bind, _default_tenant_id, _login

BOT2 = {**BOT_PAYLOAD, "bot_key": "store_bot", "name": "销售助手"}


async def _user(
    client: AsyncClient, s: AsyncSession, make_user: MakeUser, name: str
) -> tuple[str, str]:
    await seed_all(s, get_settings())
    uid = await make_user(name)
    rid = (
        await s.execute(text("SELECT id FROM public.sys_role WHERE role_key='DEVELOPER'"))
    ).scalar_one()
    await _bind(s, "sys_user_role", uid, str(rid))
    await _bind(s, "sys_user_tenant", uid, await _default_tenant_id(s))
    await s.commit()
    return uid, await _login(client, name)


async def _mk_store_bot(client: AsyncClient, token: str) -> str:
    r = await client.post(
        "/system/bots",
        headers={"Authorization": f"Bearer {token}"},
        json=BOT2,
    )
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def test_publish_governance(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec:归属者可上架;非归属者 403;未上架商店不可见;下架即时。"""
    _, token_a = await _user(client, session, make_user, "st_a")
    _, token_b = await _user(client, session, make_user, "st_b")
    bot_id = await _mk_store_bot(client, token_a)
    # 新建默认未上架:列表与详情均不可见(含创建者视角)
    r = await client.get("/store/bots", headers={"Authorization": f"Bearer {token_a}"})
    assert all(b["id"] != bot_id for b in r.json())
    r0 = await client.get(
        "/store/bots/store_bot", headers={"Authorization": f"Bearer {token_a}"}
    )
    assert r0.status_code == 404
    # 非归属者上架 403
    r2 = await client.post(
        f"/system/bots/{bot_id}/publish",
        headers={"Authorization": f"Bearer {token_b}"},
        json={"greeting": "你好"},
    )
    assert r2.status_code == 403
    # 归属者上架成功
    r3 = await client.post(
        f"/system/bots/{bot_id}/publish",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"greeting": "我是销售助手", "suggestions": ["本月销售如何"]},
    )
    assert r3.status_code == 200, r3.text
    # 上架后 B 可见
    r4 = await client.get("/store/bots", headers={"Authorization": f"Bearer {token_b}"})
    assert any(b["id"] == bot_id for b in r4.json())
    # 下架即时不可见
    r5 = await client.post(
        f"/system/bots/{bot_id}/unpublish",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert r5.status_code == 200
    r6 = await client.get("/store/bots", headers={"Authorization": f"Bearer {token_b}"})
    assert all(b["id"] != bot_id for b in r6.json())


async def test_publish_guardrail_blocks(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「违规内容拦截」:greeting 含凭证形态 → 拒上架。"""
    _, token = await _user(client, session, make_user, "st_g")
    bot_id = await _mk_store_bot(client, token)
    r = await client.post(
        f"/system/bots/{bot_id}/publish",
        headers={"Authorization": f"Bearer {token}"},
        json={"greeting": "我的密钥是 sk-abcdef1234567890abcdef"},
    )
    assert r.status_code == 400
    # 仍未上架
    r2 = await client.get("/store/bots", headers={"Authorization": f"Bearer {token}"})
    assert all(b["id"] != bot_id for b in r2.json())


async def test_publish_empty_tools_rejected(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """零工具上架闸:求交后工具集为空的 Bot 拒上架(货架不摆必败商品)。"""
    _, token = await _user(client, session, make_user, "st_nt")
    r = await client.post(
        "/system/bots",
        headers={"Authorization": f"Bearer {token}"},
        json={**BOT2, "tools": []},
    )
    assert r.status_code == 201
    bot_id = str(r.json()["id"])
    r2 = await client.post(
        f"/system/bots/{bot_id}/publish",
        headers={"Authorization": f"Bearer {token}"},
        json={"greeting": "你好"},
    )
    assert r2.status_code == 400
    assert "未配置可用工具" in r2.json()["detail"]
    # 仍未上架
    r3 = await client.get("/store/bots", headers={"Authorization": f"Bearer {token}"})
    assert all(b["id"] != bot_id for b in r3.json())


async def test_discovery_search_sort_detail(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec:搜索命中名称;详情全要素;hot 排序按使用量。"""
    _, token = await _user(client, session, make_user, "st_d")
    bot_id = await _mk_store_bot(client, token)
    await client.post(
        f"/system/bots/{bot_id}/publish",
        headers={"Authorization": f"Bearer {token}"},
        json={"greeting": "销售问题问我", "suggestions": ["本月 GMV"]},
    )
    r = await client.get(
        "/store/bots?q=销售", headers={"Authorization": f"Bearer {token}"}
    )
    assert any(b["id"] == bot_id for b in r.json())
    r2 = await client.get(
        "/store/bots?q=不存在词xyz", headers={"Authorization": f"Bearer {token}"}
    )
    assert all(b["id"] != bot_id for b in r2.json())
    r3 = await client.get(
        "/store/bots/store_bot", headers={"Authorization": f"Bearer {token}"}
    )
    d = r3.json()
    assert d["greeting"] == "销售问题问我"
    assert d["suggestions"] == ["本月 GMV"]
    assert d["use_count"] == 0 and d["fav_count"] == 0
    assert d["my_rating"] is None


async def test_favorite_and_rating(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec:收藏幂等+列表仅在架;改分 upsert;非法分值 400(传输层)。"""
    _, token = await _user(client, session, make_user, "st_f")
    bot_id = await _mk_store_bot(client, token)
    await client.post(
        f"/system/bots/{bot_id}/publish",
        headers={"Authorization": f"Bearer {token}"},
        json={"greeting": "hi"},
    )
    # 收藏幂等(审查循环 F6:set 语义——重复 POST 不反选,取消走 DELETE)
    r = await client.post(
        "/store/bots/store_bot/favorite", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.json()["favorited"] is True
    favs = await client.get(
        "/store/favorites", headers={"Authorization": f"Bearer {token}"}
    )
    assert len(favs.json()) == 1
    # 收藏列表项同构 StoreBotOut:favorited 恒 TRUE(前端心形填充依据)、
    # fav_count 全局计数(缺陷修复:曾缺字段致「我的收藏」页红心不亮)
    assert favs.json()[0]["favorited"] is True
    assert favs.json()[0]["fav_count"] == 1
    # 详情接口 favorited 同源真实(曾恒 false 的同根缺陷)
    d_fav = (
        await client.get(
            "/store/bots/store_bot", headers={"Authorization": f"Bearer {token}"}
        )
    ).json()
    assert d_fav["favorited"] is True
    r2 = await client.post(
        "/store/bots/store_bot/favorite", headers={"Authorization": f"Bearer {token}"}
    )
    assert r2.json()["favorited"] is True  # 幂等:不反选(spec「重复操作幂等」)
    # 取消幂等:DELETE 后 favorited=false,重复 DELETE 同态
    rd1 = await client.delete(
        "/store/bots/store_bot/favorite", headers={"Authorization": f"Bearer {token}"}
    )
    rd2 = await client.delete(
        "/store/bots/store_bot/favorite", headers={"Authorization": f"Bearer {token}"}
    )
    assert rd1.json()["favorited"] is False
    assert rd2.json()["favorited"] is False
    # 评分 upsert
    r3 = await client.put(
        "/store/bots/store_bot/rating",
        headers={"Authorization": f"Bearer {token}"},
        json={"score": 5},
    )
    assert r3.status_code == 200
    r4 = await client.put(
        "/store/bots/store_bot/rating",
        headers={"Authorization": f"Bearer {token}"},
        json={"score": 3},
    )
    assert r4.status_code == 200
    n = (
        await session.execute(
            text(
                "SELECT count(*) FROM public.sys_bot_rating WHERE bot_id=CAST(:b AS uuid)"
            ),
            {"b": bot_id},
        )
    ).scalar_one()
    assert n == 1  # 一人一评(upsert 不产生第二条;按 bot 过滤免跨测试计数)
    d = (
        await client.get(
            "/store/bots/store_bot", headers={"Authorization": f"Bearer {token}"}
        )
    ).json()
    assert d["my_rating"] == 3 and d["avg_score"] == 3.0 and d["rating_count"] == 1
    # 非法分值 422(传输层 ge/le 闸;领域层 400 双闸在 service)
    r5 = await client.put(
        "/store/bots/store_bot/rating",
        headers={"Authorization": f"Bearer {token}"},
        json={"score": 6},
    )
    assert r5.status_code == 422


async def test_favorite_list_only_published(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「收藏列表仅在架」:下架即移出,重新上架回归。"""
    _, token = await _user(client, session, make_user, "st_fp")
    bot_id = await _mk_store_bot(client, token)
    await client.post(
        f"/system/bots/{bot_id}/publish",
        headers={"Authorization": f"Bearer {token}"},
        json={"greeting": "hi"},
    )
    await client.post(
        "/store/bots/store_bot/favorite", headers={"Authorization": f"Bearer {token}"}
    )
    await client.post(
        f"/system/bots/{bot_id}/unpublish",
        headers={"Authorization": f"Bearer {token}"},
    )
    favs = await client.get(
        "/store/favorites", headers={"Authorization": f"Bearer {token}"}
    )
    assert favs.json() == []
    # 重新上架自动回归
    await client.post(
        f"/system/bots/{bot_id}/publish",
        headers={"Authorization": f"Bearer {token}"},
        json={"greeting": "hi"},
    )
    favs2 = await client.get(
        "/store/favorites", headers={"Authorization": f"Bearer {token}"}
    )
    assert len(favs2.json()) == 1


async def test_store_run_counts_usage(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「直跑计数自增」:成功运行 use_count+1;无客户端上报通道。"""
    _, token = await _user(client, session, make_user, "st_r")
    bot_id = await _mk_store_bot(client, token)
    await client.post(
        f"/system/bots/{bot_id}/publish",
        headers={"Authorization": f"Bearer {token}"},
        json={"greeting": "hi"},
    )
    r = await client.post(
        "/store/bots/store_bot/run",
        headers={"Authorization": f"Bearer {token}"},
        json={"question": "你好"},
    )
    # agent 可达性决定 success;无论如何成功才计数 —— 此处 dev 有真 agent
    assert r.status_code == 200
    if r.json()["success"]:
        n = (
            await session.execute(
                text(
                    "SELECT use_count FROM public.sys_bot_definition"
                    " WHERE id=CAST(:i AS uuid)"
                ),
                {"i": bot_id},
            )
        ).scalar_one()
        assert n == 1
    # 无 use_count 上报参数(契约面直证:塞入字段被忽略而非计数)
    before = (
        await session.execute(
            text(
                "SELECT use_count FROM public.sys_bot_definition"
                " WHERE id=CAST(:i AS uuid)"
            ),
            {"i": bot_id},
        )
    ).scalar_one()
    await client.put(
        "/store/bots/store_bot/rating",
        headers={"Authorization": f"Bearer {token}"},
        json={"score": 4, "use_count": 999},
    )
    after = (
        await session.execute(
            text(
                "SELECT use_count FROM public.sys_bot_definition"
                " WHERE id=CAST(:i AS uuid)"
            ),
            {"i": bot_id},
        )
    ).scalar_one()
    assert after == before
