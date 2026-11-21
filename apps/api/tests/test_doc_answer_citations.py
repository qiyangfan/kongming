# pyright: reportPrivateUsage=false
"""-doc-answer-citations api 全栈测试:来源契约/净化同源/SQL 道恒空/回显。

核心契约:响应与消息历史携带 sources(仅文档道);sources 是净化后正文存活
标记的投影(不重不漏无幽灵);SQL 道恒空;老客户端。
"""

from typing import cast

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.seed import seed_all
from kongming.chat.agent_client import AgentReply
from kongming.config import get_settings
from tests.conftest import MakeUser
from tests.test_chat_query import _auth, _user_with_chat_perm
from tests.test_chat_stream import StreamFakeAgent, _install

pytestmark = pytest.mark.usefixtures("fake_agent")


@pytest.fixture
def fake_agent():
    from kongming.chat.dependencies import get_agent_client
    from kongming.main import app

    holder: dict[str, StreamFakeAgent] = {}

    def _factory() -> StreamFakeAgent:
        return holder["agent"]

    app.dependency_overrides[get_agent_client] = _factory
    yield holder
    app.dependency_overrides.pop(get_agent_client, None)


_DOC_REPLY = AgentReply(
    success=True,
    verdict="PASS",
    summary="依据【员工手册-A7F2】执行;另据【手册-A7F2】补充;编造项【假键-0BAD】已被剥。",
    sources=[
        {
            "citation_key": "员工手册-A7F2",
            "source_ref": "员工手册.docx §1 概述",
            "doc_name": "员工手册.docx",
        },
        {
            "citation_key": "手册-A7F2",
            "source_ref": "员工手册.docx §1 概述",
            "doc_name": "员工手册.docx",
        },
    ],
)


async def test_chat_query_sources_contract(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, StreamFakeAgent],
) -> None:
    """响应来源随 AgentReply 返回；SQL 回答的来源列表为空。"""
    await seed_all(session, get_settings())
    _uid, token = await _user_with_chat_perm(session, make_user, "dc1")
    _install(fake_agent, StreamFakeAgent(reply=_DOC_REPLY))
    r = await client.post("/chat/query", json={"question": "报销周期"}, headers=_auth(token))
    assert r.status_code == 200
    body = r.json()
    keys = [s["citation_key"] for s in body["sources"]]
    assert keys == ["员工手册-A7F2", "手册-A7F2"]
    # 投影含元数据(source_ref/doc_name)
    assert body["sources"][0]["source_ref"] == "员工手册.docx §1 概述"
    assert body["sources"][0]["doc_name"] == "员工手册.docx"


async def test_sql_path_sources_empty(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, StreamFakeAgent],
) -> None:
    """SQL 道产出的 sources 恒为空列表(不伪造引用)。"""
    await seed_all(session, get_settings())
    _uid, token = await _user_with_chat_perm(session, make_user, "dc2")
    _install(fake_agent, StreamFakeAgent(reply=None))  # SQL 道(无 sources 字段)
    r = await client.post("/chat/query", json={"question": "有多少商品"}, headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["sources"] == []
    # 四态终态字段(既有字段仍在)
    for f in ("success", "verdict", "summary", "columns", "rows", "sql"):
        assert f in r.json()


async def test_history_replay_and_legacy_empty(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, StreamFakeAgent],
) -> None:
    """消息来源写入 result_json 并在历史记录中回显，旧消息返回空列表。"""
    await seed_all(session, get_settings())
    _uid, token = await _user_with_chat_perm(session, make_user, "dc3")
    _install(fake_agent, StreamFakeAgent(reply=_DOC_REPLY))
    r = await client.post("/chat/query", json={"question": "报销周期"}, headers=_auth(token))
    assert r.status_code == 200
    sid = r.json()["session_id"]
    r2 = await client.get(f"/chat/sessions/{sid}/messages", headers=_auth(token))
    assert r2.status_code == 200
    asst = [m for m in r2.json() if m["role"] == "ASSISTANT"][0]
    assert [s["citation_key"] for s in asst["result"]["sources"]] == ["员工手册-A7F2", "手册-A7F2"]
    # 回显与响应一致(同一序列化点)
    assert asst["result"]["sources"] == r.json()["sources"]
    # 老消息(直插无 sources 键)→ 回空,不报错
    mid = (
        await session.execute(
            text(
                "INSERT INTO public.chat_message"
                " (id, session_id, role, status, result_json, created_at)"
                " VALUES (gen_random_uuid(), :s, 'ASSISTANT', 'SUCCESS',"
                '  \'{"columns": [], "rows": []}\', now()) RETURNING id'
            ),
            {"s": sid},
        )
    ).scalar_one()
    await session.commit()  # 直插行须显式提交(与 _mk_feedback_msg 同范式)
    r3 = await client.get(f"/chat/sessions/{sid}/messages", headers=_auth(token))
    legacy = [m for m in r3.json() if m["id"] == str(mid)][0]
    assert legacy["result"]["sources"] == []


async def test_sse_result_carries_sources(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, StreamFakeAgent],
) -> None:
    """流式终态帧同构携带 sources(不新增事件类型)。"""
    from tests.test_chat_stream import _frames_for, _parse_sse

    await seed_all(session, get_settings())
    _uid, token = await _user_with_chat_perm(session, make_user, "dc4")
    frames = _frames_for(_DOC_REPLY)
    _install(fake_agent, StreamFakeAgent(frames, reply=_DOC_REPLY))
    r = await client.post("/chat/query/stream", json={"question": "报销周期"}, headers=_auth(token))
    assert r.status_code == 200
    parsed = _parse_sse(r.text)
    result = next(d for e, d in parsed if e == "result")
    sources = result["sources"]
    assert isinstance(sources, list)
    keys = [
        str(cast("dict[str, object]", s)["citation_key"]) for s in cast("list[object]", sources)
    ]
    assert keys == ["员工手册-A7F2", "手册-A7F2"]
