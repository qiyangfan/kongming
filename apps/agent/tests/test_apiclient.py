"""检查 rag_retrieve 收到非 JSON 响应时返回 None 并回退实时内省。"""

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


async def test_rag_retrieve_doc_hints_citation_key_passthrough(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """将 API 返回的 citation_key 透传到 RagContext。"""
    client = ApiClient(Settings())

    async def fake_post(self: ApiClient, path: str, payload: dict[str, object]) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "schema_hint": "",
                "history_sql": "",
                "doc_hints": [
                    {
                        "source_ref": "退货政策.md §2",
                        "content": "7 天内可退",
                        "citation_key": "policy-A1B2",
                    },
                    {"source_ref": "旧版 api.md §1", "content": "无键命中"},
                ],
            },
        )

    monkeypatch.setattr(ApiClient, "_post", fake_post)
    result = await client.rag_retrieve(tenant_key="t", datasource_key="default", question="q")
    assert result is not None
    assert result.doc_hints[0]["citation_key"] == "policy-A1B2"
    # 键缺失(旧版 api)=空串:注入段表头与合法键集按既有空值语义回退 source_ref
    assert result.doc_hints[1]["citation_key"] == ""


async def test_rag_retrieve_user_id_payload_wiring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """接线:user_id 携带时进回调负载;缺省(含 None/空串)不携带该键
    ——负载与既有形态逐字节同形(可见集合缺省=纯公共,api 侧)。"""
    client = ApiClient(Settings())
    captured: list[dict[str, object]] = []

    async def fake_post(self: ApiClient, path: str, payload: dict[str, object]) -> httpx.Response:
        captured.append(dict(payload))
        return httpx.Response(200, json={"schema_hint": "", "history_sql": ""})

    monkeypatch.setattr(ApiClient, "_post", fake_post)
    await client.rag_retrieve(
        tenant_key="t",
        datasource_key="default",
        question="q",
        user_id="01913b2e-7f2a-7cc2-9a4e-1c50f3e0c9d1",
    )
    assert captured[0]["user_id"] == "01913b2e-7f2a-7cc2-9a4e-1c50f3e0c9d1"
    await client.rag_retrieve(tenant_key="t", datasource_key="default", question="q")
    assert "user_id" not in captured[1]
    await client.rag_retrieve(tenant_key="t", datasource_key="default", question="q", user_id=None)
    assert "user_id" not in captured[2]
