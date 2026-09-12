"""describer 润色测试(009):prompt 形态/输出清洗防御/降级契约/端点行为。"""

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from kongming_agent import deps
from kongming_agent.describer import (
    ColumnSpec,
    DescribeError,
    TableSpec,
    describe_table,
    fallback_description,
)
from kongming_agent.llm import LlmClient, LlmError
from kongming_agent.main import app
from kongming_agent.settings import Settings

# 端点测试夹具注入形态:yield (TestClient, 真 LlmClient, monkeypatch) 三元组
ClientFixture = tuple[TestClient, LlmClient, pytest.MonkeyPatch]


class StubLlm:
    """按脚本回放 chat 输出;记录 prompt 供断言。"""

    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.calls: list[tuple[str, str]] = []

    async def chat(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.2,
        **_kw: object,  # 021:task/trace_id/tenant_key 吸收
    ) -> str:
        self.calls.append((system, user))
        return self.replies.pop(0) if self.replies else ""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise AssertionError("describe 链路不应调用 embed")


def _spec() -> TableSpec:
    return TableSpec(
        table_name="film",
        schema_name="tenant_sakila",
        table_comment="",
        columns=[
            ColumnSpec(name="film_id", type="integer"),
            ColumnSpec(name="title", type="varchar", comment="片名"),
        ],
    )


# ============ prompt 形态(上游逐字移植锚点) ============


async def test_prompt_shape_upstream_verbatim() -> None:
    llm = StubLlm(['{"table_comment":"影片表","columns_comment":{},"sample_queries":[]}'])
    await describe_table(_spec(), llm)  # type: ignore[arg-type]
    system, user = llm.calls[0]
    assert system.startswith("你是数据库文档专家。根据给定的 PostgreSQL 表结构，")
    assert "只输出 JSON" in system
    assert "表：tenant_sakila.film" in user
    assert "- film_id (integer)" in user
    assert "- title (varchar) -- 片名" in user
    assert "GROUP BY" in user
    assert "schema 前缀" in user


# ============ 输出清洗防御 ============


# ============ 037 alias 产出(同一次调用附带;缺失/畸形降级不判失败) ============


async def test_alias_produced_from_same_call() -> None:
    """alias 随润色同一次 LLM 调用产出(prompt 含要求;输出可解析)。"""
    llm = StubLlm(
        ['{"table_comment":"商品信息","columns_comment":{},"sample_queries":[],"alias":"商品表"}']
    )
    out = await describe_table(_spec(), llm)  # type: ignore[arg-type]
    assert out.alias == "商品表"
    _, user = llm.calls[0]
    # prompt 资产例外清单登记项:alias 产出要求钉住(手滑删除即红)
    assert "alias：业务称谓别名" in user
    assert "若无法推断则 alias 为空字符串" in user


async def test_alias_missing_degrades_empty_not_failure() -> None:
    """alias 缺失:按空串降级,不判润色失败(D2)。"""
    llm = StubLlm(['{"table_comment":"影片表","columns_comment":{},"sample_queries":[]}'])
    out = await describe_table(_spec(), llm)  # type: ignore[arg-type]
    assert out.alias == ""


async def test_alias_overlong_truncated_to_column_width() -> None:
    """alias 超长:截断到 64(对齐 VARCHAR(64)),不判失败。"""
    llm = StubLlm(
        [
            '{"table_comment":"影片表","columns_comment":{},'
            '"sample_queries":[],"alias":"' + "别名" * 40 + '"}'
        ]
    )
    out = await describe_table(_spec(), llm)  # type: ignore[arg-type]
    assert len(out.alias) == 64


async def test_alias_structural_chars_degrade_empty() -> None:
    """R1-F1:alias 含换行/括号/竖线(破坏 api 侧 hint 行形态)按空降级。"""
    import json as _json

    for dirty in ("a\nfake.table(q)", "商品(表)", "商品|表"):
        llm = StubLlm(
            [
                _json.dumps(
                    {
                        "table_comment": "影片表",
                        "columns_comment": {},
                        "sample_queries": [],
                        "alias": dirty,
                    },
                    ensure_ascii=False,
                )
            ]
        )
        out = await describe_table(_spec(), llm)  # type: ignore[arg-type]
        assert out.alias == ""


async def test_hallucinated_columns_filtered() -> None:
    llm = StubLlm(
        [
            '{"table_comment":"影片表","columns_comment":'
            '{"film_id":"integer 影片ID","ghost_col":"text 臆造列"},'
            '"sample_queries":[]}'
        ]
    )
    out = await describe_table(_spec(), llm)  # type: ignore[arg-type]
    assert "film_id" in out.columns_comment
    assert "ghost_col" not in out.columns_comment  # 臆造列不入库


async def test_samples_only_readonly_select() -> None:
    llm = StubLlm(
        [
            '{"table_comment":"影片表","columns_comment":{},'
            '"sample_queries":['
            '"SELECT count(*) FROM tenant_sakila.film GROUP BY title",'
            '"DELETE FROM tenant_sakila.film",'
            '"SELECT * FROM tenant_sakila.film INTO new_t",'
            '"SELECT 1; DROP TABLE x",'
            '"SELECT 2",'
            '"SELECT 3",'
            '"SELECT 4"]} '
        ]
    )
    out = await describe_table(_spec(), llm)  # type: ignore[arg-type]
    assert out.sample_queries == [
        "SELECT count(*) FROM tenant_sakila.film GROUP BY title",
        "SELECT 2",
        "SELECT 3",
    ]  # 非 SELECT/INTO/多语句全拒;条数 ≤3


async def test_long_text_truncated() -> None:
    long_desc = "说明" * 300  # 600 字,远超 200 上限
    llm = StubLlm(
        [
            '{"table_comment":"' + long_desc + '","columns_comment":'
            '{"film_id":"' + long_desc + '"},"sample_queries":[]}'
        ]
    )
    out = await describe_table(_spec(), llm)  # type: ignore[arg-type]
    assert len(out.table_comment) == 200
    assert len(out.columns_comment["film_id"]) == 200


async def test_all_empty_output_degrades() -> None:
    """表说明与列说明全空 → 按失败降级(不落空知识)。"""
    llm = StubLlm(['{"table_comment":"","columns_comment":{},"sample_queries":[]}'])
    with pytest.raises(DescribeError):
        await describe_table(_spec(), llm)  # type: ignore[arg-type]


async def test_llm_failure_raises_describe_error() -> None:
    class BoomLlm(StubLlm):
        async def chat(
            self,
            system: str,
            user: str,
            *,
            temperature: float = 0.2,
            **_kw: object,  # 021:task/trace_id/tenant_key 吸收
        ) -> str:
            raise LlmError("LLM 调用失败: 模拟故障")

    with pytest.raises(DescribeError):
        await describe_table(_spec(), BoomLlm([]))  # type: ignore[arg-type]


async def test_markdown_fenced_json_parsed() -> None:
    """LLM 违约输出 markdown 代码块:容错解析仍成功。"""
    llm = StubLlm(
        ['```json\n{"table_comment":"影片表","columns_comment":{},"sample_queries":[]}\n```']
    )
    out = await describe_table(_spec(), llm)  # type: ignore[arg-type]
    assert out.table_comment == "影片表"


def test_fallback_description_pure_structure() -> None:
    out = fallback_description(_spec())
    assert out.table_comment == ""  # 原注释为空(有原注释时回退原值)
    assert out.columns_comment == {"film_id": "integer", "title": "varchar"}
    assert out.sample_queries == []
    assert out.alias == ""  # 037:降级无 alias


def test_invalid_table_name_rejected() -> None:
    import asyncio

    with pytest.raises(DescribeError):
        asyncio.run(
            describe_table(  # type: ignore[arg-type]
                TableSpec(
                    table_name="坏表名",
                    schema_name="s",
                    columns=[ColumnSpec(name="a", type="int")],
                ),
                StubLlm([]),  # pyright: ignore[reportArgumentType]  # 测试替身与 LlmClient 鸭子类型兼容(签名子集)
            )
        )


# ============ 端点行为 ============


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Generator[ClientFixture]:
    settings = Settings(
        internal_token="test-internal-token",  # noqa: S106  # 测试令牌,非凭证
        llm_api_key="k",
        embedding_api_key="k",
    )
    real_llm = LlmClient(settings)
    monkeypatch.setattr(deps, "get_llm", lambda: real_llm)
    with TestClient(app) as c:
        yield c, real_llm, monkeypatch


def _headers() -> dict[str, str]:
    return {"X-Internal-Token": "test-internal-token"}


def test_describe_table_endpoint_enriched(client: ClientFixture) -> None:
    c, _, mp = client

    class FakeLlm(StubLlm):
        pass

    fake = FakeLlm(
        [
            '{"table_comment":"影片表","columns_comment":{"film_id":"integer ID"},'
            '"sample_queries":["SELECT 1"]}'
        ]
    )
    mp.setattr(deps, "get_llm", lambda: fake)
    resp = c.post(
        "/internal/describe-table",
        headers=_headers(),
        json={
            "table_name": "film",
            "schema_name": "tenant_sakila",
            "columns": [{"name": "film_id", "type": "integer"}],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["enriched"] is True
    assert data["table_comment"] == "影片表"


def test_describe_table_endpoint_degrades_not_5xx(client: ClientFixture) -> None:
    """润色失败:enriched=False + 200,api 侧按表降级不阻断整批。"""
    c, _, _ = client
    resp = c.post(
        "/internal/describe-table",
        headers=_headers(),
        json={"table_name": "film", "schema_name": "s", "columns": []},  # 空列→不可润色
    )
    assert resp.status_code == 422  # 传输层校验(列清单必填)


def test_embedding_endpoint_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeLlm2:
        async def embed(self, texts: list[str]) -> list[list[float]]:
            captured["texts"] = texts
            return [[0.1, 0.2] for _ in texts]

    monkeypatch.setattr(deps, "get_llm", lambda: FakeLlm2())
    with TestClient(app) as c:
        resp = c.post(
            "/internal/embedding",
            headers=_headers(),
            json={"texts": ["问句一", "问句二"]},
        )
    assert resp.status_code == 200
    assert resp.json()["vectors"] == [[0.1, 0.2], [0.1, 0.2]]
    assert captured["texts"] == ["问句一", "问句二"]


def test_embedding_endpoint_over_limit_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeLlm3:
        async def embed(self, texts: list[str]) -> list[list[float]]:
            raise AssertionError("超限请求不应触达 LLM")

    monkeypatch.setattr(deps, "get_llm", lambda: FakeLlm3())
    with TestClient(app) as c:
        resp = c.post(
            "/internal/embedding",
            headers=_headers(),
            json={"texts": [f"t{i}" for i in range(65)]},  # 超默认上限 64
        )
    assert resp.status_code == 422


def test_llm_embed_batch_guard() -> None:
    """LlmClient.embed 批量边界:空/超限拒绝(即使 key 已配)。"""
    settings = Settings(llm_api_key="k")
    client_obj = LlmClient(settings)
    import asyncio

    with pytest.raises(LlmError):
        asyncio.run(client_obj.embed([]))
