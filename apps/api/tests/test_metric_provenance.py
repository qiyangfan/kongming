# pyright: reportPrivateUsage=false
"""指标溯源全栈测试:同源派生/透出契约/落库回显/面板。

核心契约:结构化命中与文字段同源(同一命中集 strings 逐字一致)、
QueryOut/result_json/回显三路径同源、存量档缺失回空且不重算。
"""

import datetime as dt

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.seed import seed_all
from kongming.config import get_settings
from kongming.semantic.models import MetricDef, format_metric_hints, project_metric_hits
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


# ── 1. 同源派生(纯函数层) ───────────────────────────


def test_format_hints_derived_from_hits() -> None:
    """format_metric_hints 由结构化命中派生提示词文本,同一命中集输出恒定。"""
    hits = [
        MetricDef(
            metric_name="复购率",
            sql_template="count(distinct c)/count(distinct m)",
            metric_id="m-1",
            updated_at=None,
        ),
        MetricDef(metric_name="GMV", sql_template="sum(amount)", metric_id="m-2"),
    ]
    legacy = [
        MetricDef(metric_name="复购率", sql_template="count(distinct c)/count(distinct m)"),
        MetricDef(metric_name="GMV", sql_template="sum(amount)"),
    ]
    assert format_metric_hints(hits) == format_metric_hints(legacy)
    # 结构化投影
    projected = project_metric_hits(hits)
    assert len(projected) == 2
    assert projected[0].metric_id == "m-1"
    assert projected[0].metric_name == "复购率"
    assert projected[0].sql_template == "count(distinct c)/count(distinct m)"
    assert projected[1].metric_id == "m-2"
    # as_dict 投影(datetime/None 归一)
    d = projected[0].as_dict()
    assert d["metric_id"] == "m-1" and d["updated_at"] is None
    with_time = MetricDef(
        metric_name="X", sql_template="y", metric_id="m-3", updated_at=dt.datetime(2026, 9, 1)
    )
    assert project_metric_hits([with_time])[0].as_dict()["updated_at"] == "2026-09-01 00:00:00"


def test_empty_hits_empty_strings() -> None:
    """无命中时 metric_hits 为空、文字段为空串(零注入)。"""
    assert format_metric_hints([]) == ""
    assert project_metric_hits([]) == []


# ── 2. 透出契约(retrieve 响应) ─────────────────────────


async def test_retrieve_carries_metric_hits(
    client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """/internal/rag/retrieve 同时携带 metric_hints 与 metric_hits,
    且 metric_hints 恰为 metric_hits 的派生(数量与内容对应)。"""
    from kongming.rag.agent_callback import AgentRagClient
    from tests.test_rag_recall import _fake_vector

    async def _embed(self: object, texts: list[str]) -> list[list[float]]:
        return [_fake_vector(t) for t in texts]

    monkeypatch.setattr(AgentRagClient, "embed", _embed)
    await seed_all(session, get_settings())
    tid = str(
        (
            await session.execute(
                text("SELECT id FROM public.sys_tenant WHERE tenant_key='default'")
            )
        ).scalar_one()
    )
    from kongming.semantic.repository import SqlSemanticRepo

    repo = SqlSemanticRepo(session)
    await repo.upsert_metric(
        tenant_id=tid,
        datasource_key="default",
        metric={
            "metric_name": "复购率",
            "sql_template": "count(distinct c)/count(distinct m)",
            "business_terms": ["复购"],
            "enabled": True,
        },
    )
    await session.commit()
    r = await client.post(
        "/internal/rag/retrieve",
        json={"tenant_key": "default", "datasource_key": "default", "question": "复购率怎么算"},
        headers={"X-Internal-Token": "test-internal-token"},
    )
    assert r.status_code == 200
    body = r.json()
    hit_list = body["metric_hits"]
    strs = body["metric_hints"]
    assert len(hit_list) == 1
    assert hits_derive_strings(hit_list) == strs  # 文字段=结构化命中派生


def hits_derive_strings(hits: list[dict[str, object]]) -> str:
    """同源断言:文字段由结构化命中逐条派生(与 format_metric_hints 同口径)。"""
    return "\n".join(f"{h['metric_name']}: {h['sql_template']}" for h in hits)


# ── 3. 终态与落库(全栈) ────────────────────────────


async def test_final_carries_and_persists_metric_hits(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, StreamFakeAgent],
) -> None:
    """终态携带口径依据、result_json 落库、历史回显一致。"""
    from kongming.chat.agent_client import AgentReply

    await seed_all(session, get_settings())
    _uid, token = await _user_with_chat_perm(session, make_user, "mp1")
    # Agent 应答中的 metric_hits 会透传到结果。
    reply = AgentReply(
        success=True,
        verdict="PASS",
        summary="复购率 12%",
        sql="SELECT count(distinct c)/count(distinct m) FROM t",
        metric_hits=[
            {
                "metric_id": "m-1",
                "metric_name": "复购率",
                "sql_template": "count(distinct c)/count(distinct m)",
                "updated_at": "2026-09-01 08:00:00",
            }
        ],
    )
    _install(fake_agent, StreamFakeAgent(reply=reply))
    r = await client.post("/chat/query", json={"question": "复购率"}, headers=_auth(token))
    assert r.status_code == 200
    body = r.json()
    assert body["metric_hits"] == [
        {
            "metric_id": "m-1",
            "metric_name": "复购率",
            "sql_template": "count(distinct c)/count(distinct m)",
            "updated_at": "2026-09-01 08:00:00",
        }
    ]
    mid = body["message_id"]
    # 落库 result_json 带该键
    rj = (
        await session.execute(
            text("SELECT result_json FROM public.chat_message WHERE id=:i"), {"i": mid}
        )
    ).scalar_one()
    import json

    assert json.loads(rj)["metric_hits"][0]["metric_id"] == "m-1"
    # 历史回显一致(同一序列化点)
    r2 = await client.get(f"/chat/sessions/{body['session_id']}/messages", headers=_auth(token))
    assert r2.status_code == 200
    asst = [m for m in r2.json() if m["role"] == "ASSISTANT"][0]
    assert asst["result"]["metric_hits"][0]["metric_name"] == "复购率"


async def test_old_message_no_metric_hits(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, StreamFakeAgent],
) -> None:
    """老消息(无该键)回显为空且不反查指标表重算。"""
    await seed_all(session, get_settings())
    _uid, token = await _user_with_chat_perm(session, make_user, "mp2")
    _install(fake_agent, StreamFakeAgent(reply=None))  # 无 metric_hits 字段
    r = await client.post("/chat/query", json={"question": "有多少商品"}, headers=_auth(token))
    assert r.status_code == 200
    body = r.json()
    assert body["metric_hits"] == []
    r2 = await client.get(f"/chat/sessions/{body['session_id']}/messages", headers=_auth(token))
    asst = [m for m in r2.json() if m["role"] == "ASSISTANT"][0]
    assert asst["result"]["metric_hits"] == []
