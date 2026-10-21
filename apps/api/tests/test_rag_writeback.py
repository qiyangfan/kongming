"""覆盖 query_history 的幂等更新、旧向量保留和数量上限淘汰。"""
# pyright: reportPrivateUsage=false

from typing import cast

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.rag.agent_callback import AgentRagClient
from tests.conftest import MakeUser

from .test_rag_recall import (
    fake_embed,  # noqa: F401  # pyright: ignore[reportUnusedImport]  (fixture 复用)
)

pytestmark = pytest.mark.usefixtures("fake_embed")


def _reply(sql: str):
    """构造会触发历史回写的成功 AgentReply。"""
    from kongming.chat.agent_client import AgentReply

    return AgentReply(
        success=True,
        verdict="PASS",
        summary="ok",
        columns=["n"],
        rows=[[1]],
        sql=sql,
    )


def _patch_agent(monkeypatch: pytest.MonkeyPatch, sql: str) -> None:
    from asyncio import Future

    from kongming.chat.agent_client import AgentClient

    fut: Future[object] = Future()
    fut.set_result(_reply(sql))

    def _fake_query(self: AgentClient, **kw: object) -> Future[object]:  # noqa: ARG001
        return fut

    monkeypatch.setattr(AgentClient, "query", _fake_query)


async def _ask(client: AsyncClient, token: str, question: str) -> None:
    from tests.test_chat_query import _auth

    r = await client.post("/chat/query", json={"question": question}, headers=_auth(token))
    assert r.status_code == 200 and r.json()["success"]


async def _rows(s: AsyncSession) -> list[dict[str, object]]:
    # RowMapping 非 dict 子类,cast 对齐声明(运行时不变)
    return cast(
        "list[dict[str, object]]",
        list(
            (await s.execute(text("SELECT question, sql_text, embedding FROM rag.query_history")))
            .mappings()
            .all(),
        ),
    )


async def test_same_question_upsert_keeps_single_row(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同题两次成功回写:一行、sql_text 为最新(O4 重复沉淀清零)。"""
    from tests.test_chat_query import _user_with_chat_perm

    _, token = await _user_with_chat_perm(client, session, make_user)
    _patch_agent(monkeypatch, "SELECT count(*) FROM goods")
    await _ask(client, token, "有多少商品")
    _patch_agent(monkeypatch, "SELECT count(1) FROM goods")
    await _ask(client, token, "有多少商品")

    rows = await _rows(session)
    assert len(rows) == 1
    assert rows[0]["question"] == "有多少商品"
    assert rows[0]["sql_text"] == "SELECT count(1) FROM goods"  # 最新成功形态
    assert rows[0]["embedding"] is not None


async def test_embedding_failure_keeps_old_vector(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """第二次回写 embedding 失败:行覆盖但保留旧向量(不清空,不退向量召回)。"""
    from kongming.rag.ports import EmbeddingError
    from tests.test_chat_query import _user_with_chat_perm

    _, token = await _user_with_chat_perm(client, session, make_user)
    _patch_agent(monkeypatch, "SELECT count(*) FROM goods")
    await _ask(client, token, "有多少商品")

    async def broken(self: AgentRagClient, texts: list[str]) -> list[list[float]]:
        raise EmbeddingError("embedding 服务不可达")

    monkeypatch.setattr(AgentRagClient, "embed", broken)
    _patch_agent(monkeypatch, "SELECT count(1) FROM goods")
    await _ask(client, token, "有多少商品")

    rows = await _rows(session)
    assert len(rows) == 1
    assert rows[0]["sql_text"] == "SELECT count(1) FROM goods"
    assert rows[0]["embedding"] is not None  # 旧向量保留


async def test_cap_evicts_oldest(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """超过总量上限时淘汰 created_at 最早的记录。"""
    import kongming.rag.repository as repo_mod
    from tests.test_chat_query import _user_with_chat_perm

    from .test_rag_recall import _ensure_default_tenant

    tid = await _ensure_default_tenant(session)
    # 预插 3 行不同问句,created_at 递减(q1 最旧)
    for i, q in enumerate(("q1", "q2", "q3")):
        await session.execute(
            text(
                "INSERT INTO rag.query_history"
                " (id, tenant_id, datasource_key, question, sql_text, status, created_at)"
                " VALUES (gen_random_uuid(), :t, 'default', :q, :sql, 'SUCCESS',"
                " now() - make_interval(secs => :age))"
            ),
            {"t": tid, "q": q, "sql": f"SELECT {i}", "age": 1000 - i * 100},
        )
    await session.commit()

    monkeypatch.setattr(repo_mod, "QUERY_HISTORY_CAP", 3)
    _patch_agent(monkeypatch, "SELECT count(*) FROM goods")
    _, token = await _user_with_chat_perm(client, session, make_user)
    await _ask(client, token, "新问句")

    questions = {r["question"] for r in await _rows(session)}
    assert len(questions) == 3
    assert "q1" not in questions  # 最旧被淘汰
    assert {"q2", "q3", "新问句"} == questions


async def test_different_questions_coexist(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """不同问句互不覆盖(幂等键=问句原文,不误伤相似问句)。"""
    from tests.test_chat_query import _user_with_chat_perm

    _, token = await _user_with_chat_perm(client, session, make_user)
    _patch_agent(monkeypatch, "SELECT count(*) FROM goods")
    await _ask(client, token, "有多少商品")
    await _ask(client, token, "商品有多少")
    # 相似但不同原文:两行共存(相似问句靠向量召回排序,不做唯一键聚类)
    assert len(await _rows(session)) == 2


async def test_overlong_question_writeback_survives(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """question 最多 2000 个字符；唯一索引对长问句使用 md5 表达式，确保回写幂等。"""
    from tests.test_chat_query import _user_with_chat_perm

    _, token = await _user_with_chat_perm(client, session, make_user)
    _patch_agent(monkeypatch, "SELECT count(*) FROM goods")
    # 顶到传输层上限 2000 字(UTF-8 6000 字节,超 btree 2704 上限)
    long_q = ("请帮我统计库存明细" * 400)[:2000]
    await _ask(client, token, long_q)
    await _ask(client, token, long_q)
    rows = await _rows(session)
    assert len(rows) == 1
    assert rows[0]["question"] == long_q
    assert rows[0]["sql_text"] == "SELECT count(*) FROM goods"
