"""ApiClient HTTP 层收敛直测(013 挂账⑮):rag_retrieve 对「200 但非 JSON」
网关形态按失败返回 None(回退实时内省),不裸抛 JSONDecodeError——
nl2sql-agent spec 立约「召回服务失败时回退实时内省兜底」,docstring
承诺「任何失败返回 None」,本用例锁该承诺兑现。
"""

import httpx
import pytest

from kongming_agent.apiclient import ApiClient
from kongming_agent.settings import Settings


async def test_rag_retrieve_non_json_200_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """200 但报文非 JSON(网关回 HTML 错误页等)→ None,不裸抛。"""
    client = ApiClient(Settings())

    async def fake_post(self: ApiClient, path: str, payload: dict[str, object]) -> httpx.Response:
        return httpx.Response(200, content=b"<html>Bad Gateway</html>")

    monkeypatch.setattr(ApiClient, "_post", fake_post)
    result = await client.rag_retrieve(tenant_key="t", datasource_key="default", question="q")
    assert result is None


async def test_rag_retrieve_valid_json_still_parsed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """对照组:正常 JSON 报文解析不受影响(防收敛误伤主路径)。"""
    client = ApiClient(Settings())

    async def fake_post(self: ApiClient, path: str, payload: dict[str, object]) -> httpx.Response:
        return httpx.Response(200, json={"schema_hint": "film 表:电影", "history_sql": "SELECT 1"})

    monkeypatch.setattr(ApiClient, "_post", fake_post)
    result = await client.rag_retrieve(tenant_key="t", datasource_key="default", question="q")
    assert result is not None
    assert result.schema_hint == "film 表:电影"
    assert result.history_sql == "SELECT 1"
