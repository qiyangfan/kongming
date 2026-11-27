"""验证 Bot 用量随响应返回并在 API 请求中落库；记录失败不影响应答，其他渠道保持后台上报。"""
# pyright: reportPrivateUsage=false

from collections.abc import Iterator

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.seed import seed_all
from kongming.chat.agent_client import AgentReply
from kongming.chat.dependencies import get_agent_client
from kongming.config import get_settings
from kongming.main import app
from tests.conftest import MakeUser
from tests.test_bot import BOT_PAYLOAD
from tests.test_chat_query import _bind, _default_tenant_id, _login


class FakeAgentUsage:
    """scripted 替身:query_react 回传两条 usage_records(模拟 ReAct 两轮)。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def query_react(self, **kw: object) -> AgentReply:
        self.calls.append(kw)
        tid = str(kw.get("trace_id") or "")
        return AgentReply(
            success=True,
            verdict="PASS",
            summary="Bot 应答",
            usage_records=[
                {
                    "trace_id": tid,
                    "tenant_key": "sakila",
                    "model": "qwen-plus",
                    "task": "REACT",
                    "prompt_tokens": 100,
                    "completion_tokens": 40,
                    "total_tokens": 140,
                    "seq": 0,
                },
                {
                    "trace_id": tid,
                    "tenant_key": "sakila",
                    "model": "qwen-plus",
                    "task": "SUMMARIZE",
                    "prompt_tokens": 60,
                    "completion_tokens": 20,
                    "total_tokens": 80,
                    "seq": 1,
                },
            ],
        )

    async def rewrite_title(self, *, question: str) -> str | None:
        return None


@pytest.fixture
def fake_agent_usage() -> Iterator[dict[str, FakeAgentUsage]]:
    holder: dict[str, FakeAgentUsage] = {}
    app.dependency_overrides[get_agent_client] = lambda: holder["agent"]
    yield holder
    app.dependency_overrides.pop(get_agent_client, None)


async def _user_with_key(
    client: AsyncClient, s: AsyncSession, make_user: MakeUser, name: str
) -> tuple[str, str, str]:
    """用户 + Bot + api-key(返回 uid, jwt token, api_key 明文)。"""
    await seed_all(s, get_settings())
    uid = await make_user(name)
    rid = (
        await s.execute(text("SELECT id FROM public.sys_role WHERE role_key='DEVELOPER'"))
    ).scalar_one()
    await _bind(s, "sys_user_role", uid, str(rid))
    tid = await _default_tenant_id(s)
    await _bind(s, "sys_user_tenant", uid, tid)
    await s.commit()
    token = _login(client)
    r = await client.post(
        "/system/bots", headers={"Authorization": f"Bearer {token}"}, json=BOT_PAYLOAD
    )
    bot_id = r.json()["id"]
    r2 = await client.post(
        f"/system/bots/{bot_id}/keys?name=q",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r2.status_code == 201
    return uid, token, str(r2.json()["api_key"])


async def test_bot_run_usage_synced_with_bot_id(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent_usage: dict[str, FakeAgentUsage],
) -> None:
    """验证响应返回时 Bot 用量已落库，且 bot_id 与 seq 正确。"""
    fake_agent_usage["agent"] = FakeAgentUsage()
    _, _, api_key = await _user_with_key(client, session, make_user, "sync_u1")
    r = await client.post(
        "/v1/chat/completions",
        headers={"X-Api-Key": api_key},
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 200, r.text
    # Bot 运行时启用用量采集。
    assert fake_agent_usage["agent"].calls[0].get("collect_usage") is True
    rows = (
        (
            await session.execute(
                text(
                    "SELECT model, task, seq, bot_id IS NOT NULL AS has_bot,"
                    " tenant_id IS NOT NULL AS has_tenant,"
                    " prompt_tokens, completion_tokens"
                    " FROM public.sys_llm_usage WHERE bot_id IS NOT NULL"
                    " ORDER BY seq"
                )
            )
        )
        .mappings()
        .all()
    )
    assert len(rows) == 2
    assert rows[0]["task"] == "REACT" and rows[0]["seq"] == 0
    assert rows[1]["task"] == "SUMMARIZE" and rows[1]["seq"] == 1
    assert all(r["has_bot"] and r["has_tenant"] for r in rows)
    assert int(rows[0]["prompt_tokens"]) == 100


async def test_bot_usage_insert_idempotent_on_retry(
    client: AsyncClient,
    session: AsyncSession,
) -> None:
    """spec「重试冲突按唯一约束静默去重」:同 (trace_id,seq) 重插不炸不重复。"""
    import uuid as _uuid

    from kongming.auth.seed import seed_all
    from kongming.config import get_settings
    from kongming.db import get_sessionmaker
    from kongming.llmusage.repository import SqlLlmUsageRepo
    from tests.test_chat_query import _default_tenant_id

    await seed_all(session, get_settings())
    tid = await _default_tenant_id(session)
    records = [
        {
            "trace_id": "tr-idem-1",
            "model": "qwen-plus",
            "task": "REACT",
            "prompt_tokens": 1,
            "completion_tokens": 2,
            "total_tokens": 3,
            "seq": 0,
        },
    ]
    bot_id = str(_uuid.uuid4())
    async with get_sessionmaker()() as s:
        repo = SqlLlmUsageRepo(s)
        n1 = await repo.insert_bot_usage_batch(records, tenant_id=str(tid), bot_id=bot_id)
        n2 = await repo.insert_bot_usage_batch(records, tenant_id=str(tid), bot_id=bot_id)
    assert n1 == 1 and n2 == 0  # 第二次全去重
    cnt = int(
        (
            await session.execute(
                text("SELECT count(*) FROM public.sys_llm_usage WHERE trace_id='tr-idem-1'")
            )
        ).scalar_one()
    )
    assert cnt == 1


async def test_bot_usage_sink_failure_never_blocks(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent_usage: dict[str, FakeAgentUsage],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """spec「落库失败不阻断」:sink 异常,应答照常(失败仅日志)。"""
    from kongming.llmusage.repository import SqlLlmUsageRepo

    async def _boom(self: object, *a: object, **kw: object) -> int:
        raise RuntimeError("DB 抖动")

    monkeypatch.setattr(SqlLlmUsageRepo, "insert_bot_usage_batch", _boom)
    fake_agent_usage["agent"] = FakeAgentUsage()
    _, _, api_key = await _user_with_key(client, session, make_user, "sync_u2")
    r = await client.post(
        "/v1/chat/completions",
        headers={"X-Api-Key": api_key},
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["choices"][0]["message"]["content"] == "Bot 应答"
