"""覆盖 Bot 商店发现、收藏、版本化发布、回滚和运行。"""
# pyright: reportPrivateUsage=false

from collections.abc import Iterator
from types import SimpleNamespace
from typing import cast

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.seed import seed_all
from kongming.chat.agent_client import AgentReply
from kongming.chat.router import get_agent_client
from kongming.config import get_settings
from kongming.main import app
from tests.conftest import MakeUser
from tests.test_bot import BOT_PAYLOAD
from tests.test_chat_query import _bind, _default_tenant_id, _login

BOT2 = {**BOT_PAYLOAD, "bot_key": "store_bot", "name": "销售助手"}


class _StoreFakeAgent:
    """scripted 替身:记录 react 调用(直跑快照消费断言用)。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def query_react(self, **kw: object) -> AgentReply:
        self.calls.append(kw)
        return AgentReply(success=True, verdict="PASS", summary="Bot 应答")

    async def rewrite_title(self, *, question: str) -> str | None:
        return None


@pytest.fixture
def store_fake_agent() -> Iterator[dict[str, _StoreFakeAgent]]:
    holder: dict[str, _StoreFakeAgent] = {}
    app.dependency_overrides[get_agent_client] = lambda: holder["agent"]
    yield holder
    app.dependency_overrides.pop(get_agent_client, None)


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
    r0 = await client.get("/store/bots/store_bot", headers={"Authorization": f"Bearer {token_a}"})
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
    r = await client.get("/store/bots?q=销售", headers={"Authorization": f"Bearer {token}"})
    assert any(b["id"] == bot_id for b in r.json())
    r2 = await client.get("/store/bots?q=不存在词xyz", headers={"Authorization": f"Bearer {token}"})
    assert all(b["id"] != bot_id for b in r2.json())
    r3 = await client.get("/store/bots/store_bot", headers={"Authorization": f"Bearer {token}"})
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
    # 收藏幂等(set 语义——重复 POST 不反选,取消走 DELETE)
    r = await client.post(
        "/store/bots/store_bot/favorite", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.json()["favorited"] is True
    favs = await client.get("/store/favorites", headers={"Authorization": f"Bearer {token}"})
    assert len(favs.json()) == 1
    # 收藏列表项同构 StoreBotOut:favorited 恒 TRUE(前端心形填充依据)、
    # fav_count 全局计数
    assert favs.json()[0]["favorited"] is True
    assert favs.json()[0]["fav_count"] == 1
    # 详情接口 favorited 同源真实
    d_fav = (
        await client.get("/store/bots/store_bot", headers={"Authorization": f"Bearer {token}"})
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
            text("SELECT count(*) FROM public.sys_bot_rating WHERE bot_id=CAST(:b AS uuid)"),
            {"b": bot_id},
        )
    ).scalar_one()
    assert n == 1  # 一人一评(upsert 不产生第二条;按 bot 过滤免跨测试计数)
    d = (
        await client.get("/store/bots/store_bot", headers={"Authorization": f"Bearer {token}"})
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
    favs = await client.get("/store/favorites", headers={"Authorization": f"Bearer {token}"})
    assert favs.json() == []
    # 重新上架自动回归
    await client.post(
        f"/system/bots/{bot_id}/publish",
        headers={"Authorization": f"Bearer {token}"},
        json={"greeting": "hi"},
    )
    favs2 = await client.get("/store/favorites", headers={"Authorization": f"Bearer {token}"})
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
                text("SELECT use_count FROM public.sys_bot_definition WHERE id=CAST(:i AS uuid)"),
                {"i": bot_id},
            )
        ).scalar_one()
        assert n == 1
    # 请求中的 use_count 字段会被忽略，不会增加计数。
    before = (
        await session.execute(
            text("SELECT use_count FROM public.sys_bot_definition WHERE id=CAST(:i AS uuid)"),
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
            text("SELECT use_count FROM public.sys_bot_definition WHERE id=CAST(:i AS uuid)"),
            {"i": bot_id},
        )
    ).scalar_one()
    assert after == before


# ── 版本化上架(快照/指针/幂等/回滚/封顶/直跑切快照) ──


async def _publish(client: AsyncClient, token: str, bot_id: str, greeting: str) -> Response:
    return await client.post(
        f"/system/bots/{bot_id}/publish",
        headers={"Authorization": f"Bearer {token}"},
        json={"greeting": greeting, "suggestions": [f"{greeting} 问题"]},
    )


async def test_publish_versioning_lifecycle(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """上架=版本快照:v1→v2 递增、草稿编辑不影响在架、内容一致幂等复用、
    BLOCK 拒绝指针不动、详情版本字段、幂等审计「内容无变化」。"""
    _, token = await _user(client, session, make_user, "st_vl")
    bot_id = await _mk_store_bot(client, token)
    # v1 上架:详情带版本号与上架时间(字段)
    r1 = await _publish(client, token, bot_id, "v1 问候")
    assert r1.status_code == 200, r1.text
    d1 = (
        await client.get("/store/bots/store_bot", headers={"Authorization": f"Bearer {token}"})
    ).json()
    assert d1["version_no"] == 1
    assert d1["version_published_at"] is not None
    assert d1["greeting"] == "v1 问候"
    # 内容一致幂等复用:定义未动时再次上架不产生新版本
    r3 = await _publish(client, token, bot_id, "v1 问候")
    assert r3.status_code == 200
    d3 = (
        await client.get("/store/bots/store_bot", headers={"Authorization": f"Bearer {token}"})
    ).json()
    assert d3["version_no"] == 1
    vers = (
        await client.get(
            f"/system/bots/{bot_id}/versions", headers={"Authorization": f"Bearer {token}"}
        )
    ).json()
    assert len(vers) == 1
    # 幂等审计「内容无变化」(spec 场景)
    logs = (
        (
            await session.execute(
                text(
                    "SELECT detail FROM public.sys_oper_log"
                    " WHERE action='bot.publish' AND target=:t"
                    " ORDER BY created_at"
                ),
                {"t": bot_id},
            )
        )
        .scalars()
        .all()
    )
    assert any(
        isinstance(x, dict) and cast("dict[str, object]", x).get("content_changed") is False
        for x in cast("list[object]", logs)
    )
    # 草稿编辑(名称/系统提示词)不影响在架版本:商店仍 v1 内容
    r_put = await client.put(
        f"/system/bots/{bot_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "改名草稿", "system_prompt": "草稿人设 v9"},
    )
    assert r_put.status_code == 204
    d2 = (
        await client.get("/store/bots/store_bot", headers={"Authorization": f"Bearer {token}"})
    ).json()
    assert d2["name"] == "销售助手"  # 货架名=快照,非草稿
    assert d2["version_no"] == 1
    # 内容有变更重新上架 → v2(草稿成为新货架版本)
    r4 = await _publish(client, token, bot_id, "v2 问候")
    assert r4.status_code == 200
    d4 = (
        await client.get("/store/bots/store_bot", headers={"Authorization": f"Bearer {token}"})
    ).json()
    assert d4["version_no"] == 2 and d4["greeting"] == "v2 问候"
    # BLOCK 拒绝:指针不动(仍 v2),商店不可见违规内容
    r5 = await _publish(client, token, bot_id, "我的密钥是 sk-abcdef1234567890abcdef")
    assert r5.status_code == 400
    d5 = (
        await client.get("/store/bots/store_bot", headers={"Authorization": f"Bearer {token}"})
    ).json()
    assert d5["version_no"] == 2 and d5["greeting"] == "v2 问候"


async def test_version_history_and_rollback(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    """版本历史可查(当前标识明确)/非归属 403/回滚成功切换/no-op 幂等/
    回滚按现行护栏重审(BLOCK 拒绝指针不动)/下架态回滚仅恢复指针。"""
    _, token_a = await _user(client, session, make_user, "st_rb")
    _, token_b = await _user(client, session, make_user, "st_rb_b")
    bot_id = await _mk_store_bot(client, token_a)
    for g in ("g1", "g2", "g3"):
        assert (await _publish(client, token_a, bot_id, g)).status_code == 200
    hist = f"/system/bots/{bot_id}/versions"
    # 历史可查:三条,当前=v3 标识明确,快照内容可读
    r = await client.get(hist, headers={"Authorization": f"Bearer {token_a}"})
    items = r.json()
    assert [v["version_no"] for v in items] == [3, 2, 1]
    assert items[0]["current"] is True and items[1]["current"] is False
    assert items[2]["snapshot"]["greeting"] == "g1"
    # 非归属者查历史/回滚 403
    assert (
        await client.get(hist, headers={"Authorization": f"Bearer {token_b}"})
    ).status_code == 403
    assert (
        await client.post(f"{hist}/1/rollback", headers={"Authorization": f"Bearer {token_b}"})
    ).status_code == 403
    # 回滚成功:当前切回 v2,商店即时消费 v2;v3 保留在历史
    rr = await client.post(f"{hist}/2/rollback", headers={"Authorization": f"Bearer {token_a}"})
    assert rr.status_code == 200, rr.text
    d = (
        await client.get("/store/bots/store_bot", headers={"Authorization": f"Bearer {token_a}"})
    ).json()
    assert d["version_no"] == 2 and d["greeting"] == "g2"
    items2 = (await client.get(hist, headers={"Authorization": f"Bearer {token_a}"})).json()
    assert {v["version_no"] for v in items2 if v["current"]} == {2}
    assert len(items2) == 3  # v3 保留
    # 回滚到当前版本:幂等 no-op
    assert (
        await client.post(f"{hist}/2/rollback", headers={"Authorization": f"Bearer {token_a}"})
    ).status_code == 200

    async def _always_blocked(self: object, **kw: object) -> list[str]:
        texts = cast("list[str]", kw.get("texts") or [])
        return ["内容未通过安全检查,已省略。"] * len(texts)

    # 回滚过现行护栏(规则升级后旧内容违规即拦):拒绝且指针不动
    from kongming.guardrail.service import GuardrailService

    monkeypatch.setattr(GuardrailService, "evaluate_outputs", _always_blocked)
    rb = await client.post(f"{hist}/1/rollback", headers={"Authorization": f"Bearer {token_a}"})
    assert rb.status_code == 400
    d2 = (
        await client.get("/store/bots/store_bot", headers={"Authorization": f"Bearer {token_a}"})
    ).json()
    assert d2["version_no"] == 2  # 指针未动(fail-safe)
    monkeypatch.undo()
    # 下架态回滚:仅恢复指针、不改变下架状态(商店仍不可见)
    assert (
        await client.post(
            f"/system/bots/{bot_id}/unpublish",
            headers={"Authorization": f"Bearer {token_a}"},
        )
    ).status_code == 200
    assert (
        await client.post(f"{hist}/3/rollback", headers={"Authorization": f"Bearer {token_a}"})
    ).status_code == 200
    assert (
        await client.get("/store/bots/store_bot", headers={"Authorization": f"Bearer {token_a}"})
    ).status_code == 404  # 下架态不出现在商店
    n = (
        await session.execute(
            text(
                "SELECT v.version_no FROM public.sys_bot_definition b"
                " JOIN public.sys_bot_version v ON v.id=b.current_version_id"
                " WHERE b.id=CAST(:i AS uuid)"
            ),
            {"i": bot_id},
        )
    ).scalar_one()
    assert n == 3  # 指针已恢复,货架标记仍空


async def test_version_cap_eviction(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    """封顶淘汰保护当前版本:超上限淘汰最旧非当前版本;当前版本恒保留;
    回滚到被淘汰版本 404。上限部署级(测试以 2 收紧验证算法)。"""
    # 仅收紧 store 路由的部署上限(其余 get_settings 引用不受影响)
    import kongming.store.router as store_router

    monkeypatch.setattr(
        store_router, "get_settings", lambda: SimpleNamespace(bot_store_version_cap=2)
    )
    _, token = await _user(client, session, make_user, "st_cap")
    bot_id = await _mk_store_bot(client, token)
    for g in ("c1", "c2", "c3", "c4"):
        assert (await _publish(client, token, bot_id, g)).status_code == 200
    hist = f"/system/bots/{bot_id}/versions"
    items = (await client.get(hist, headers={"Authorization": f"Bearer {token}"})).json()
    # cap=2:非当前保留最新 2 个(v2/v3),最旧 v1 被淘汰,当前 v4 恒保留
    assert [v["version_no"] for v in items] == [4, 3, 2]
    assert items[0]["current"] is True
    # 回滚到被淘汰版本 404
    assert (
        await client.post(f"{hist}/1/rollback", headers={"Authorization": f"Bearer {token}"})
    ).status_code == 404
    # 回滚到保留的历史版本仍可用
    assert (
        await client.post(f"{hist}/2/rollback", headers={"Authorization": f"Bearer {token}"})
    ).status_code == 200


async def test_store_run_consumes_snapshot_public_channel_live(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    store_fake_agent: dict[str, _StoreFakeAgent],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """直跑消费当前上架版本快照(草稿不外溢);公开页渠道消费活定义
    (版本化不外溢既有渠道契约)。"""
    agent = _StoreFakeAgent()
    store_fake_agent["agent"] = agent
    # 商店直跑经组合根直调 get_agent_client(不经 FastAPI DI),补模块属性替身
    import kongming.chat.router as chat_router

    monkeypatch.setattr(chat_router, "get_agent_client", lambda: agent)
    _, token = await _user(client, session, make_user, "st_run")
    bot_id = await _mk_store_bot(client, token)
    assert (await _publish(client, token, bot_id, "上架问候")).status_code == 200
    # 草稿改系统提示词与工具集(不重新上架)
    assert (
        await client.put(
            f"/system/bots/{bot_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"system_prompt": "草稿人设 v9", "tools": ["rag_retrieve"]},
        )
    ).status_code == 204
    # 商店直跑:系统提示词=快照(v1),工具集=快照(求交后含 execute_sql)
    r = await client.post(
        "/store/bots/store_bot/run",
        headers={"Authorization": f"Bearer {token}"},
        json={"question": "你好"},
    )
    assert r.status_code == 200
    assert agent.calls, "商店直跑未触达运行链路"
    call = agent.calls[0]
    assert call["bot_system_prompt"] == "你是售后客服助手"  # 快照,非草稿
    assert "execute_sql" in {str(t["name"]) for t in cast("list[dict[str, object]]", call["tools"])}
    # 公开页渠道(免登录,需 is_public):消费活定义=既有契约不变
    assert (
        await client.put(
            f"/system/bots/{bot_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"is_public": True},
        )
    ).status_code == 204
    rp = await client.post("/bot/store_bot/chat", json={"question": "你好"})
    assert rp.status_code == 200 and rp.json()["success"] is True
    call2 = agent.calls[1]
    assert call2["bot_system_prompt"] == "草稿人设 v9"  # 活定义(非快照)
    assert {str(t["name"]) for t in cast("list[dict[str, object]]", call2["tools"])} == {
        "rag_retrieve"
    }
