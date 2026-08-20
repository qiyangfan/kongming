"""/query 端点测试:内部令牌守门、请求校验、响应形态(nl2sql spec)。"""

import pytest
from httpx import ASGITransport, AsyncClient

from kongming_agent import deps
from kongming_agent.apiclient import (
    MetaColumn,
    MetaTable,
    RagContext,
    SchemaMeta,
    SqlExecuteOutcome,
)
from kongming_agent.main import app

TOKEN = {"X-Internal-Token": "test-internal-token"}


class _Llm:
    def __init__(self, answers: list[str]) -> None:
        self.answers = list(answers)

    async def chat(self, system: str, user: str, *, temperature: float = 0.2) -> str:
        return self.answers.pop(0)


class _Api:
    async def rag_retrieve(self, **kw: object) -> RagContext | None:
        return None  # 知识库召回零命中路径(回退实时内省,005 形态回归)

    async def fetch_schema(self, **kw: object) -> SchemaMeta:
        return SchemaMeta(
            tables=[MetaTable(schema="tenant_default", name="goods", comment="", columns=[
                MetaColumn(name="qty", data_type="int", comment="")])],
            truncated=False,
        )

    async def sql_execute(self, **kw: object) -> SqlExecuteOutcome:
        return SqlExecuteOutcome(verdict="PASS", columns=["qty"], rows=[[30]])


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    llm = _Llm(["商品总库存", "SELECT sum(qty) FROM goods", '{"summary":"共 30"}'])
    api = _Api()
    monkeypatch.setattr(deps, "get_llm", lambda: llm)
    monkeypatch.setattr(deps, "get_api", lambda: api)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_healthz_exempt_from_token() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        assert (await c.get("/healthz")).status_code == 200


async def test_query_requires_token(client: AsyncClient) -> None:  # type: ignore[valid-type]
    async with client as c:
        resp = await c.post("/query", json={})
    assert resp.status_code == 403


async def test_query_bad_request_422(client: AsyncClient) -> None:  # type: ignore[valid-type]
    async with client as c:
        resp = await c.post("/query", json={"tenant_key": "default"}, headers=TOKEN)
    assert resp.status_code == 422  # 缺 trace_id/question


async def test_query_happy_path(client: AsyncClient) -> None:  # type: ignore[valid-type]
    async with client as c:
        resp = await c.post(
            "/query",
            json={
                "trace_id": "t-e2e",
                "tenant_key": "default",
                "question": "商品总库存是多少",
                "history": [],
            },
            headers=TOKEN,
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["verdict"] == "PASS"
    assert data["summary"] == "共 30"
    assert data["columns"] == ["qty"] and data["rows"] == [[30]]
    assert data["sql"] == "SELECT sum(qty) FROM goods"
    assert data["retry_count"] == 0
