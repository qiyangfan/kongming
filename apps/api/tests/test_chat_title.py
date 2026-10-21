"""验证首个成功问答后的标题改写、人工标题保护和条件回写；失败时保留原截断标题，不影响问答响应。"""
# pyright: reportPrivateUsage=false

from collections.abc import Iterator

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.chat.router import get_agent_client
from kongming.main import app
from tests.conftest import MakeUser
from tests.test_chat_query import (
    FakeAgent,
    _auth,
    _install,
    _pass_reply,
    _user_with_chat_perm,
)


@pytest.fixture
def fake_agent() -> Iterator[dict[str, FakeAgent]]:
    holder: dict[str, FakeAgent] = {}

    def _factory() -> FakeAgent:
        return holder["agent"]

    app.dependency_overrides[get_agent_client] = _factory
    yield holder
    app.dependency_overrides.pop(get_agent_client, None)


async def _title(s: AsyncSession, session_id: str) -> str:
    return str(
        (
            await s.execute(
                text("SELECT title FROM public.chat_session WHERE id=:i"), {"i": session_id}
            )
        ).scalar_one()
    )


async def test_title_rewritten_after_first_success(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, FakeAgent],
) -> None:
    _, token = await _user_with_chat_perm(client, session, make_user, "title-ok")
    agent = FakeAgent(_pass_reply())
    agent.title_reply = "商品库存总览"  # agent 侧已净化(引号/换行在 agent 去除)
    _install(fake_agent, agent)
    r = await client.post(
        "/chat/query", json={"question": "各商品库存分别是多少"}, headers=_auth(token)
    )
    assert r.status_code == 200
    # BackgroundTasks 在 ASGI 响应周期内 await 完成,断言即可见
    assert await _title(session, r.json()["session_id"]) == "商品库存总览"
    assert agent.title_calls == ["各商品库存分别是多少"]  # 以首问改写


async def test_title_rewrite_failure_keeps_truncated(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, FakeAgent],
) -> None:
    _, token = await _user_with_chat_perm(client, session, make_user, "title-deg")
    _install(fake_agent, FakeAgent(_pass_reply()))  # title_reply=None=降级
    question = "这个问句会保留截断标题因为它超过了三十个字符的限制度试"
    r = await client.post("/chat/query", json={"question": question}, headers=_auth(token))
    assert r.status_code == 200
    assert await _title(session, r.json()["session_id"]) == question[:30]


async def test_title_rewrite_skipped_after_manual_rename(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, FakeAgent],
) -> None:
    """人工重命名保护:守卫(当前标题!=首问截断值)在 LLM 调用前短路。"""
    _, token = await _user_with_chat_perm(client, session, make_user, "title-ren")
    agent = FakeAgent(_pass_reply())
    agent.title_reply = "LLM 标题"
    _install(fake_agent, agent)
    r = await client.post("/chat/query", json={"question": "首轮问句"}, headers=_auth(token))
    sid = r.json()["session_id"]
    assert await _title(session, sid) == "LLM 标题"  # 首轮已改写
    assert len(agent.title_calls) == 1

    r = await client.patch(
        f"/chat/sessions/{sid}", json={"title": "我的复盘"}, headers=_auth(token)
    )
    assert r.status_code == 200
    # 次轮成功仍触发后台任务,但守卫跳过(不再调 LLM、标题不被覆盖)
    r = await client.post(
        "/chat/query",
        json={"question": "次轮问句", "session_id": sid},
        headers=_auth(token),
    )
    assert r.status_code == 200
    assert await _title(session, sid) == "我的复盘"
    assert len(agent.title_calls) == 1  # LLM 未被再次调用


async def test_title_rewrite_fires_on_first_success_after_failure(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, FakeAgent],
) -> None:
    """首轮失败(boom)不改写;次轮成功补偿触发(首个 SUCCESS 轮语义,自愈)。"""
    _, token = await _user_with_chat_perm(client, session, make_user, "title-heal")
    agent = FakeAgent(_pass_reply(), boom=True)  # 首轮 agent 不可达
    _install(fake_agent, agent)
    r = await client.post("/chat/query", json={"question": "首轮失败问"}, headers=_auth(token))
    sid = r.json()["session_id"]
    assert r.json()["success"] is False
    assert await _title(session, sid) == "首轮失败问"  # 保持截断

    agent.boom = False
    agent.title_reply = "补偿标题"
    r = await client.post(
        "/chat/query",
        json={"question": "次轮成功了", "session_id": sid},
        headers=_auth(token),
    )
    assert r.json()["success"] is True
    assert await _title(session, sid) == "补偿标题"  # 以首问「首轮失败问」改写
    assert agent.title_calls == ["首轮失败问"]


async def test_title_not_rewritten_on_blocked_round(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, FakeAgent],
) -> None:
    """REJECTED 轮(权限拦截)不触发改写——违规问句不作标题素材。"""
    from kongming.chat.agent_client import AgentReply

    _, token = await _user_with_chat_perm(client, session, make_user, "title-blk")
    agent = FakeAgent(
        AgentReply(success=False, verdict="BLOCKED", sql="DELETE FROM goods", error="BLOCKED_ROOT")
    )
    _install(fake_agent, agent)
    r = await client.post("/chat/query", json={"question": "删掉所有商品"}, headers=_auth(token))
    assert r.json()["verdict"] == "BLOCKED"
    assert await _title(session, r.json()["session_id"]) == "删掉所有商品"
    assert agent.title_calls == []  # 未触发


# ============ 条件写竞态测试 ============


async def test_update_title_if_current_rowcount(session: AsyncSession, make_user: MakeUser) -> None:
    """仓储层:expected 匹配更新 1 行;不匹配(已被改名)0 行且标题不动。"""
    from kongming.auth.seed import seed_all
    from kongming.chat.repository import ChatRepository
    from kongming.config import get_settings
    from tests.test_chat_query import _default_tenant_id
    from tests.test_chat_sessions import _mk_session

    await seed_all(session, get_settings())
    uid = await make_user("rowcount")
    repo = ChatRepository(session)
    sid = await _mk_session(
        session, user_id=uid, tenant_id=await _default_tenant_id(session), title="截断值"
    )
    n = await repo.update_title_if_current(session_id=sid, expected="别的值", new_title="LLM 标题")
    assert n == 0
    assert await _title(session, sid) == "截断值"
    n = await repo.update_title_if_current(session_id=sid, expected="截断值", new_title="LLM 标题")
    assert n == 1
    await session.commit()
    assert await _title(session, sid) == "LLM 标题"


async def test_title_rewrite_loses_race_to_concurrent_rename(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, FakeAgent],
) -> None:
    """检查 LLM 改写标题期间用户手动改名时，条件更新不会覆盖人工标题。"""
    from kongming.db import get_sessionmaker

    _, token = await _user_with_chat_perm(client, session, make_user, "title-race")

    class RacingAgent(FakeAgent):
        async def rewrite_title(self, *, question: str) -> str | None:
            await super().rewrite_title(question=question)
            async with get_sessionmaker()() as s:
                await s.execute(
                    text("UPDATE public.chat_session SET title='人工抢先' WHERE title=:t"),
                    {"t": question[:30]},
                )
                await s.commit()
            return self.title_reply

    agent = RacingAgent(_pass_reply())
    agent.title_reply = "LLM 应落败"
    _install(fake_agent, agent)
    r = await client.post("/chat/query", json={"question": "竞态首问"}, headers=_auth(token))
    assert r.status_code == 200
    # 守卫放行(此时标题=截断值)→ 回调内改名成功 → 条件写 expected 失配落败
    assert agent.title_calls == ["竞态首问"]  # LLM 确实被调
    assert await _title(session, r.json()["session_id"]) == "人工抢先"  # 人工胜
