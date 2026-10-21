"""检索精排测试:配置组合校验/供应商适配/检索精排级接入。

适配器使用 MockTransport 验证请求和错误归一,不访问外部服务;
检索管线使用真实数据库,精排器使用内存替身;父项得分取子项最高分。
"""
# pyright: reportPrivateUsage=false

import json
import logging
from collections.abc import Callable
from typing import Any, cast

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.seed import seed_all
from kongming.config import RERANK_PROVIDERS, Settings, get_settings
from kongming.rag.documents_repo import SqlDocumentRepo
from kongming.rag.documents_service import (
    derive_citation_key,
    rank_parents_by_score,
    retrieve_docs,
    upload_document,
)
from kongming.rag.ports import EmbeddingError
from kongming.rag.rerank import (
    REASON_RATELIMIT,
    REASON_TIMEOUT,
    REASON_UNAVAILABLE,
    DocReranker,
    RerankError,
    build_doc_reranker,
)

# ── 构造助手 ──


def _settings(**kw: str | int | float) -> Settings:
    """显式置空 rerank 配置(隔离本地 .env 的 KM_RERANK_*),按用例覆写。"""
    base: dict[str, Any] = {
        "environment": "dev",
        "rerank_provider": "",
        "rerank_api_key": "",
        "rerank_model": "",
    }
    base.update(kw)
    return Settings(**base)


_PROD_KW: dict[str, Any] = {
    "environment": "prod",
    "jwt_private_key_pem": "k",
    "jwt_public_key_pem": "k",
    "internal_token": "k",
    "datasource_encrypt_key": "k" * 32,
    "billing_mock_secret": "prod-real-secret",
    "websearch_provider": "",
    "websearch_api_key": "",
}


class _NoEmbed:
    """向量路恒不可用(检索降级纯全文路,命中确定性由 ASCII 正文保证)。"""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise EmbeddingError("测试:向量路不可用")


class _FakeReranker:
    """内存精排体:按文档文本映射分数;记录每次调用的池(观察断言)。"""

    model = "fake-rerank-model"

    def __init__(
        self,
        score_fn: Callable[[str], float] | None = None,
        *,
        pool_topk: int = 20,
        threshold: float = 0.0,
        error: RerankError | None = None,
    ) -> None:
        self._score_fn: Callable[[str], float] = score_fn or (lambda d: 0.5)
        self.pool_topk = pool_topk
        self.score_threshold = threshold
        self._error = error
        self.calls: list[list[str]] = []

    async def score(self, *, query: str, documents: list[str]) -> list[float]:
        self.calls.append(list(documents))
        if self._error is not None:
            raise self._error
        return [float(self._score_fn(d)) for d in documents]


_DOC = """# Policy Book

## Refund Terms

refund window alpha applies within 7 days of delivery for unused goods in original condition.

## Exchange Terms

exchange beta requires the receipt and original packaging within 15 days of delivery.
"""


async def _seed_doc(session: AsyncSession) -> str:
    """种 default 租户 + 单文档两父块(ASCII 正文,全文命中确定);返回租户 id。"""
    await seed_all(session, get_settings())
    tid = str(
        (
            await session.execute(
                text("SELECT id FROM public.sys_tenant WHERE tenant_key='default'")
            )
        ).scalar_one()
    )
    repo = SqlDocumentRepo(session, ts_config="simple")
    await upload_document(
        repo,
        _NoEmbed(),
        tenant_id=tid,
        datasource_key="default",
        doc_name="policy.md",
        text=_DOC,
    )
    return tid


# ── 1.1/1.2 配置组合校验 ──


def test_unconfigured_zero_footprint() -> None:
    """未配置:无残缺信号,组合根工厂返回 None(与精排级从未存在不可区分)。"""
    s = _settings()
    assert s.rerank_misconfiguration() is None
    assert build_doc_reranker(s) is None


def test_provider_without_key_misconfigured() -> None:
    s = _settings(rerank_provider="siliconflow")
    msg = s.rerank_misconfiguration()
    assert msg is not None and "KM_RERANK_API_KEY" in msg
    assert build_doc_reranker(s) is None


def test_key_without_provider_misconfigured() -> None:
    s = _settings(rerank_api_key="sk-x")
    msg = s.rerank_misconfiguration()
    assert msg is not None and "KM_RERANK_PROVIDER" in msg


def test_model_without_provider_misconfigured() -> None:
    """只配 model 缺供应商同属残缺族(覆盖三变量组合)。"""
    s = _settings(rerank_model="gte-rerank-v2")
    msg = s.rerank_misconfiguration()
    assert msg is not None and "KM_RERANK_PROVIDER" in msg


def test_provider_key_without_model_misconfigured() -> None:
    s = _settings(rerank_provider="bailian", rerank_api_key="sk-x")
    msg = s.rerank_misconfiguration()
    assert msg is not None and "KM_RERANK_MODEL" in msg


def test_prod_rerank_misconfiguration_rejects_startup() -> None:
    kw = {**_PROD_KW, "rerank_provider": "siliconflow", "rerank_api_key": "sk-x"}
    s = Settings(**cast("dict[str, Any]", kw))
    with pytest.raises(RuntimeError, match="KM_RERANK_MODEL"):
        s.fail_closed_checks()


def test_illegal_provider_rejected_unconditional() -> None:
    """provider 非法值 dev/prod 同拒启(降级=换供应商,无安全降级形态)。"""
    s = _settings(rerank_provider="acme")
    with pytest.raises(RuntimeError, match="KM_RERANK_PROVIDER"):
        s.require_rerank_provider()


def test_build_reranker_whitelist_out_entry_dropped(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """出口主机白名单外条目:装载期剔除并告警,精排级不生效(fail-closed 不裸崩)。"""
    monkeypatch.setitem(RERANK_PROVIDERS, "acme", ("ftp://nope.example", "nope.example"))
    s = _settings(rerank_provider="acme", rerank_api_key="sk-x", rerank_model="m")
    s.require_rerank_provider()  # 不抛(注册表内有名;白名单判定在装载期结构校验)
    logging.getLogger(
        "kongming.rag.rerank"
    ).disabled = False  # alembic fileConfig 置 disabled 的防线
    with caplog.at_level(logging.WARNING, logger="kongming.rag.rerank"):
        assert build_doc_reranker(s) is None
    assert "剔除" in caplog.text


def test_build_reranker_wired_and_key_masked() -> None:
    r = build_doc_reranker(
        _settings(
            rerank_provider="bailian",
            rerank_api_key="sk-abc",
            rerank_model="gte-rerank-v2",
            rerank_pool_topk=7,
            rerank_score_threshold=0.5,
            rerank_timeout_ms=2500,
        )
    )
    assert r is not None
    assert r.model == "gte-rerank-v2"
    assert r.pool_topk == 7
    assert r.score_threshold == 0.5
    assert "sk-abc" not in repr(r)


# ── 1.3 供应商适配器(MockTransport,零真实出站) ──


def _siliconflow(handler: Any) -> DocReranker:
    return DocReranker(
        provider="siliconflow",
        model="bge-m3",
        api_key="sk-t",
        timeout_s=1.0,
        pool_topk=20,
        score_threshold=0.0,
        transport=httpx.MockTransport(handler),
    )


def _bailian(handler: Any) -> DocReranker:
    return DocReranker(
        provider="bailian",
        model="gte-rerank-v2",
        api_key="sk-t",
        timeout_s=1.0,
        pool_topk=20,
        score_threshold=0.0,
        transport=httpx.MockTransport(handler),
    )


async def test_siliconflow_dialect() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "results": [
                    {"index": 1, "relevance_score": 0.9},
                    {"index": 0, "relevance_score": 0.2},
                ]
            },
        )

    scores = await _siliconflow(handler).score(query="q", documents=["d0", "d1"])
    assert scores == [0.2, 0.9]  # 按 index 与输入顺序对齐
    assert seen["url"] == "https://api.siliconflow.cn/v1/rerank"
    assert seen["auth"] == "Bearer sk-t"
    assert seen["body"]["model"] == "bge-m3"
    assert seen["body"]["documents"] == ["d0", "d1"]


async def test_bailian_dialect_native_and_flat() -> None:
    """百炼原生形态 output.results;qwen3-rerank 起无包装顶层 results 亦兼容。"""
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"output": {"results": [{"index": 0, "relevance_score": 0.8}]}}
        )

    scores = await _bailian(handler).score(query="q", documents=["only"])
    assert scores == [0.8]
    assert seen["url"].endswith("/api/v1/services/rerank/text-rerank/text-rerank")
    assert seen["body"]["input"]["documents"] == ["only"]

    def flat_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [{"index": 0, "relevance_score": 0.4}]})

    assert await _bailian(flat_handler).score(query="q", documents=["only"]) == [0.4]


async def test_missing_index_scores_zero() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [{"index": 0, "relevance_score": 0.9}]})

    assert await _siliconflow(handler).score(query="q", documents=["d0", "d1"]) == [0.9, 0.0]


async def test_error_reason_classification() -> None:
    def status(code: int) -> Callable[[httpx.Request], httpx.Response]:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(code, text="err")

        return handler

    with pytest.raises(RerankError) as e429:
        await _siliconflow(status(429)).score(query="q", documents=["d"])
    assert e429.value.reason == REASON_RATELIMIT

    with pytest.raises(RerankError) as e500:
        await _siliconflow(status(500)).score(query="q", documents=["d"])
    assert e500.value.reason == REASON_UNAVAILABLE

    def read_timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    with pytest.raises(RerankError) as et:
        await _siliconflow(read_timeout).score(query="q", documents=["d"])
    assert et.value.reason == REASON_TIMEOUT

    def malformed(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not-json")

    with pytest.raises(RerankError) as em:
        await _siliconflow(malformed).score(query="q", documents=["d"])
    assert em.value.reason == REASON_UNAVAILABLE


async def test_key_never_leaks_in_error_or_repr() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    r = _siliconflow(handler)
    with pytest.raises(RerankError) as ei:
        await r.score(query="q", documents=["d"])
    assert "sk-t" not in str(ei.value)
    assert "sk-t" not in repr(r)


# ── 2.1 纯函数:同父多子取最高分/阈值/截断/平序 ──


def test_rank_parents_takes_child_max() -> None:
    """父项得分取子项最高分: P1 为 0.9,排在 P2 的 0.5 之前。"""
    pool = ["c1", "c2", "c3"]
    scores = [0.3, 0.9, 0.5]
    id_to_parent = {"c1": "P1", "c2": "P1", "c3": "P2"}
    out = rank_parents_by_score(pool, scores, id_to_parent, threshold=0.0, top_k=5)
    assert out == ["P1", "P2"]


def test_rank_parents_threshold_and_topk() -> None:
    pool = ["c1", "c2", "c3"]
    scores = [0.3, 0.9, 0.5]
    id_to_parent = {"c1": "P1", "c2": "P1", "c3": "P2"}
    out = rank_parents_by_score(pool, scores, id_to_parent, threshold=0.6, top_k=5)
    assert out == ["P1"]  # 低于阈值候选被过滤,P2(0.5)随之消失
    out2 = rank_parents_by_score(pool, scores, id_to_parent, threshold=0.0, top_k=1)
    assert out2 == ["P1"]


def test_rank_parents_tie_keeps_rrf_first_seen() -> None:
    pool = ["c1", "c2", "c3"]
    scores = [0.5, 0.5, 0.5]
    id_to_parent = {"c1": "P2", "c2": "P1", "c3": "P1"}
    out = rank_parents_by_score(pool, scores, id_to_parent, threshold=0.0, top_k=5)
    assert out == ["P2", "P1"]  # 平分保持 RRF 首现序


def test_rank_parents_all_filtered_empty() -> None:
    out = rank_parents_by_score(["c1"], [0.1], {"c1": "P1"}, threshold=0.6, top_k=5)
    assert out == []  # 阈值后空命中=「文档未命中」收口语义


# ── 2.1/2.2 检索管线(真库) ──


async def _fulltext_parent_refs(repo: SqlDocumentRepo, tid: str, question: str) -> list[str]:
    """未启用精排的期望序:全文命中→父块首现去重(与既有 RRF-only 路径同构)。"""
    hits = await repo.recall_children_fulltext(
        tenant_id=tid, datasource_key="default", question=question, top_k=8
    )
    refs: list[str] = []
    seen: set[str] = set()
    for h in hits:
        pid = str(h["parent_id"])
        if pid in seen:
            continue
        seen.add(pid)
        row = await repo.fetch_parents(tenant_id=tid, parent_ids=[pid])
        refs.append(str(row[0]["source_ref"]) if row else "?")
    return refs[:5]


async def test_disabled_rerank_rrf_order_unchanged(session: AsyncSession) -> None:
    tid = await _seed_doc(session)
    repo = SqlDocumentRepo(session, ts_config="simple")
    out = await retrieve_docs(
        repo, _NoEmbed(), tenant_id=tid, datasource_key="default", question="delivery"
    )
    assert [h["source_ref"] for h in out] == await _fulltext_parent_refs(repo, tid, "delivery")


async def test_disabled_rerank_records_no_usage(session: AsyncSession) -> None:
    tid = await _seed_doc(session)
    repo = SqlDocumentRepo(session, ts_config="simple")
    await retrieve_docs(
        repo, _NoEmbed(), tenant_id=tid, datasource_key="default", question="delivery"
    )
    rows = (
        await session.execute(
            text(
                "SELECT count(*) FROM public.sys_llm_usage"
                " WHERE task='doc_rerank' AND created_at >= now() - interval '5 seconds'"
            )
        )
    ).scalar_one()
    assert int(rows) == 0


async def test_enabled_rerank_reorders_and_keys_stable(session: AsyncSession) -> None:
    tid = await _seed_doc(session)
    repo = SqlDocumentRepo(session, ts_config="simple")
    disabled = await retrieve_docs(
        repo, _NoEmbed(), tenant_id=tid, datasource_key="default", question="delivery"
    )
    # 分数按文本标记给分:exchange 父块子块 0.9、refund 父块子块 0.1 → 精排后换位
    fake = _FakeReranker(lambda d: 0.9 if "beta" in d else 0.1)
    enabled = await retrieve_docs(
        repo,
        _NoEmbed(),
        tenant_id=tid,
        datasource_key="default",
        question="delivery",
        reranker=fake,
    )
    assert [h["source_ref"] for h in enabled][0] != [h["source_ref"] for h in disabled][0]
    assert "exchange beta" in enabled[0]["content"]
    # 引用键与名次无关:两轮键集合与键→内容映射一致
    disabled_map = {h["citation_key"]: h["content"] for h in disabled}
    enabled_map = {h["citation_key"]: h["content"] for h in enabled}
    assert disabled_map == enabled_map
    for h in enabled:
        pid = await _parent_id_by_ref(session, tid, h["source_ref"])
        assert h["citation_key"] == derive_citation_key(parent_id=pid, doc_name="policy.md")


async def _parent_id_by_ref(session: AsyncSession, tid: str, ref: str) -> str:
    row = (
        await session.execute(
            text(
                "SELECT id FROM rag.document_chunks"
                " WHERE tenant_id=CAST(:t AS uuid) AND source_ref=:r AND chunk_level='PARENT'"
                " LIMIT 1"
            ),
            {"t": tid, "r": ref},
        )
    ).scalar_one()
    return str(row)


async def test_pool_topk_truncates_documents_passed(session: AsyncSession) -> None:
    tid = await _seed_doc(session)
    repo = SqlDocumentRepo(session, ts_config="simple")
    fake = _FakeReranker(lambda d: 0.5, pool_topk=1)
    await retrieve_docs(
        repo,
        _NoEmbed(),
        tenant_id=tid,
        datasource_key="default",
        question="delivery",
        reranker=fake,
    )
    assert len(fake.calls) == 1
    assert len(fake.calls[0]) == 1  # 融合池 2 子块被扩量上限截到 1


async def test_rerank_failure_degrades_rrf_only_and_records_error(
    session: AsyncSession,
) -> None:
    tid = await _seed_doc(session)
    repo = SqlDocumentRepo(session, ts_config="simple")
    disabled = await retrieve_docs(
        repo, _NoEmbed(), tenant_id=tid, datasource_key="default", question="delivery"
    )
    fake = _FakeReranker(error=RerankError(REASON_TIMEOUT, "持续超时"))
    out = await retrieve_docs(
        repo,
        _NoEmbed(),
        tenant_id=tid,
        datasource_key="default",
        question="delivery",
        reranker=fake,
    )
    assert [h["source_ref"] for h in out] == [h["source_ref"] for h in disabled]
    status = (
        await session.execute(
            text(
                "SELECT status FROM public.sys_llm_usage"
                " WHERE task='doc_rerank' AND model='fake-rerank-model'"
            )
        )
    ).scalar_one()
    assert status == "ERROR"


async def test_rerank_success_records_usage(session: AsyncSession) -> None:
    tid = await _seed_doc(session)
    repo = SqlDocumentRepo(session, ts_config="simple")
    await retrieve_docs(
        repo,
        _NoEmbed(),
        tenant_id=tid,
        datasource_key="default",
        question="delivery",
        reranker=_FakeReranker(lambda d: 0.8),
    )
    row = (
        await session.execute(
            text(
                "SELECT status, prompt_tokens, completion_tokens FROM public.sys_llm_usage"
                " WHERE task='doc_rerank' AND model='fake-rerank-model'"
            )
        )
    ).one()
    assert row.status == "SUCCESS"
    assert row.prompt_tokens == 0 and row.completion_tokens == 0  # rerank 无 token 概念


async def test_threshold_filters_to_document_miss(session: AsyncSession) -> None:
    tid = await _seed_doc(session)
    repo = SqlDocumentRepo(session, ts_config="simple")
    out = await retrieve_docs(
        repo,
        _NoEmbed(),
        tenant_id=tid,
        datasource_key="default",
        question="delivery",
        reranker=_FakeReranker(lambda d: 0.3, threshold=0.6),
    )
    assert out == []  # 全部候选低于阈值 → 「文档未命中」收口,不报错
