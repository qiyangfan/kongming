# pyright: reportPrivateUsage=false
"""兜底轮持久化与呈现全栈测试(spec「兜底轮持久化与呈现」):
CHITCHAT/CLARIFY 诊断标记落库、CHECK 约束拒写、防循环判据读取、
诊断字段不进用户响应。"""

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.seed import seed_all
from kongming.chat.agent_client import AgentReply
from kongming.chat.repository import ChatRepository
from kongming.chat.router import get_agent_client
from kongming.config import get_settings
from kongming.main import app
from kongming.uuid7 import uuid7
from tests.conftest import MakeUser
from tests.test_chat_query import FakeAgent, _auth, _user_with_chat_perm


@pytest.fixture
def fake_agent():
    holder: dict[str, FakeAgent] = {}

    def _factory() -> FakeAgent:
        return holder["agent"]

    app.dependency_overrides[get_agent_client] = _factory
    yield holder
    app.dependency_overrides.pop(get_agent_client, None)


async def _mk_message(s: AsyncSession, uid: str, tid: str, *, fallback_type: object) -> str:
    """直插最小轮次链(绕过服务层,专测约束)。"""
    sid = str(uuid7())
    mid = str(uuid7())
    await s.execute(
        text("INSERT INTO public.chat_session (id, user_id, tenant_id) VALUES (:id, :u, :t)"),
        {"id": sid, "u": uid, "t": tid},
    )
    await s.execute(
        text(
            "INSERT INTO public.chat_message (id, session_id, role, status, fallback_type)"
            " VALUES (:id, :sid, 'ASSISTANT', 'NO_DATA', :fb)"
        ),
        {"id": mid, "sid": sid, "fb": fallback_type},
    )
    await s.commit()
    return mid


class TestConstraintAndRepo:
    async def test_valid_values_persist(self, session: AsyncSession, make_user: MakeUser) -> None:
        from tests.test_chat_query import _default_tenant_id

        await seed_all(session, get_settings())
        uid = await make_user("fb-c1")
        tid = await _default_tenant_id(session)
        m1 = await _mk_message(session, uid, tid, fallback_type="CHITCHAT")
        m2 = await _mk_message(session, uid, tid, fallback_type=None)
        rows = (
            await session.execute(
                text("SELECT id, fallback_type FROM public.chat_message WHERE id IN (:a, :b)"),
                {"a": m1, "b": m2},
            )
        ).all()
        got = {str(r[0]): r[1] for r in rows}
        assert got[m1] == "CHITCHAT" and got[m2] is None

    async def test_invalid_value_rejected_by_check(
        self, session: AsyncSession, make_user: MakeUser
    ) -> None:
        from sqlalchemy.exc import IntegrityError

        from tests.test_chat_query import _default_tenant_id

        await seed_all(session, get_settings())
        uid = await make_user("fb-c2")
        tid = await _default_tenant_id(session)
        with pytest.raises(IntegrityError):
            await _mk_message(session, uid, tid, fallback_type="BOGUS")
        await session.rollback()

    async def test_last_fallback_type_reads_latest_assistant(
        self, session: AsyncSession, make_user: MakeUser
    ) -> None:
        from tests.test_chat_query import _default_tenant_id

        await seed_all(session, get_settings())
        uid = await make_user("fb-c3")
        tid = await _default_tenant_id(session)
        sid = str(uuid7())
        await session.execute(
            text("INSERT INTO public.chat_session (id, user_id, tenant_id) VALUES (:i,:u,:t)"),
            {"i": sid, "u": uid, "t": tid},
        )
        for idx, fb in enumerate(("CHITCHAT", None, "CLARIFY")):
            # created_at 前推偏移错开时序:同毫秒插入会落入 id tiebreak(随机),制造假时序
            await session.execute(
                text(
                    "INSERT INTO public.chat_message"
                    " (id, session_id, role, status, fallback_type, created_at)"
                    " VALUES (:i, :s, 'ASSISTANT', 'NO_DATA', :fb,"
                    "          now() - make_interval(secs => :off))"
                ),
                {"i": str(uuid7()), "s": sid, "fb": fb, "off": (2 - idx) * 10},
            )
        await session.commit()
        # 最近一条 assistant 决定判据:上一轮为 CLARIFY → 本轮不再命中澄清
        assert await ChatRepository(session).last_fallback_type(sid) == "CLARIFY"


class TestTurnPersistence:
    async def test_chitchat_turn_persists_marker_and_neutral_summary(
        self,
        client: AsyncClient,
        session: AsyncSession,
        fake_agent: dict[str, FakeAgent],
        make_user: MakeUser,
    ) -> None:
        await seed_all(session, get_settings())
        uid, token = await _user_with_chat_perm(client, session, make_user, "fb-u1")
        fake_agent["agent"] = FakeAgent(
            AgentReply(
                success=False,
                verdict="NO_DATA",
                error="你好!请描述你想分析的数据问题,例如「上月销售额是多少」。",
                fallback_type="CHITCHAT",
            )
        )
        resp = await client.post("/chat/query", json={"question": "你好"}, headers=_auth(token))
        assert resp.status_code == 200
        row = (
            await session.execute(
                text(
                    "SELECT status, fallback_type, summary, result_json"
                    " FROM public.chat_message"
                    " WHERE session_id = (SELECT id FROM public.chat_session"
                    "                     WHERE user_id = :u ORDER BY created_at DESC LIMIT 1)"
                    "   AND role = 'ASSISTANT' ORDER BY created_at DESC, id DESC LIMIT 1"
                ),
                {"u": uid},
            )
        ).one()
        status, fb, summary, result_json = row
        assert status == "NO_DATA"
        assert fb == "CHITCHAT"
        assert summary is not None and "你好" in summary and "未找到" not in summary
        # 诊断字段不进用户响应与 result_json
        assert "fallback_type" not in str(result_json)
        assert "fallback_type" not in resp.text

    async def test_clarify_turn_persists_marker(
        self,
        client: AsyncClient,
        session: AsyncSession,
        fake_agent: dict[str, FakeAgent],
        make_user: MakeUser,
    ) -> None:
        await seed_all(session, get_settings())
        uid, token = await _user_with_chat_perm(client, session, make_user, "fb-u2")
        fake_agent["agent"] = FakeAgent(
            AgentReply(
                success=False,
                verdict="NO_DATA",
                error="想看哪个时间范围、哪个口径的销售数据?",
                fallback_type="CLARIFY",
            )
        )
        resp = await client.post(
            "/chat/query", json={"question": "帮我看看销售情况"}, headers=_auth(token)
        )
        assert resp.status_code == 200
        fb = (
            await session.execute(
                text(
                    "SELECT m.fallback_type FROM public.chat_message m"
                    " WHERE m.role='ASSISTANT' AND m.session_id IN ("
                    "   SELECT id FROM public.chat_session WHERE user_id = :u)"
                    " ORDER BY m.created_at DESC, m.id DESC LIMIT 1"
                ),
                {"u": uid},
            )
        ).scalar()
        assert fb == "CLARIFY"

    async def test_last_fallback_sent_to_agent_when_session_exists(
        self,
        client: AsyncClient,
        session: AsyncSession,
        fake_agent: dict[str, FakeAgent],
        make_user: MakeUser,
    ) -> None:
        """会话内已有兜底轮 → 下一轮载荷携带 last_fallback(防循环判据)。"""
        await seed_all(session, get_settings())
        uid, token = await _user_with_chat_perm(client, session, make_user, "fb-u3")
        agent = FakeAgent(
            AgentReply(success=True, verdict="PASS", summary="共 3 行。", sql="SELECT 1")
        )
        fake_agent["agent"] = agent
        await client.post("/chat/query", json={"question": "有多少商品"}, headers=_auth(token))
        # 首轮无会话历史,last_fallback 为空
        first = agent.calls[0]
        assert first.get("last_fallback", "") == ""
        # 第二轮(同会话)读取最近 assistant 兜底分类(无分类 → 空)
        sid = (
            await session.execute(
                text(
                    "SELECT id FROM public.chat_session WHERE user_id = :u"
                    " ORDER BY created_at DESC LIMIT 1"
                ),
                {"u": uid},
            )
        ).scalar()
        await session.execute(
            text(
                "UPDATE public.chat_message SET fallback_type='CLARIFY'"
                " WHERE session_id = :s AND role='ASSISTANT'"
            ),
            {"s": sid},
        )
        await session.commit()
        await client.post(
            "/chat/query",
            json={"question": "再看看", "session_id": str(sid)},
            headers=_auth(token),
        )
        second = agent.calls[1]
        assert second.get("last_fallback") == "CLARIFY"
