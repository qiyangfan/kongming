"""来源运行前核对与三种取材方式(评测用,全部经注入端口,零 DB 访问)。

页面可用性以服务端分块为准:按文档名找资料、按片段定位唯一父块、
核对父块摘要;任一事实无法确认则整页排除。三种方式共用同一问题、
同一证据上限与同一总时限。
"""

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from evals.wiki.pages import Page, sha256_text


class DocsPort(Protocol):
    """资料与分块读取端口(生产=现有文档管理接口)。"""

    async def list_docs(self) -> list[dict[str, Any]]: ...

    async def list_chunks(self, doc_id: str) -> list[dict[str, Any]]: ...


class ProbePort(Protocol):
    """原文检索端口(生产=/system/document/retrieval-probe 的父块名次)。"""

    async def probe(self, question: str) -> list[tuple[str, int]]: ...


@dataclass(frozen=True)
class VerifiedFact:
    """已核对事实:文档名 + 服务端父块序号。"""

    doc: str
    seq: int


@dataclass(frozen=True)
class PageCheck:
    """单页核对结果;usable=False 时整页不进入取材。"""

    page_id: str
    usable: bool
    facts: tuple[VerifiedFact, ...]
    reasons: tuple[str, ...]


def check_page(
    page: Page, docs: list[dict[str, Any]], chunks: Mapping[str, list[dict[str, Any]]]
) -> PageCheck:
    """按服务端当前分块核对页面全部事实(片段唯一 + 父块摘要一致)。"""
    docs_by_name = {str(d.get("doc_name")): d for d in docs}
    reasons = [f"来源缺失: {doc}" for doc in page.source_sha if doc not in docs_by_name]
    if reasons:
        return PageCheck(page.page_id, False, (), tuple(reasons))
    facts: list[VerifiedFact] = []
    for fact in page.facts:
        parents = [
            c
            for c in chunks.get(fact.doc, [])
            if str(c.get("chunk_level") or c.get("level")) == "PARENT"
        ]
        hits = [c for c in parents if fact.snippet in str(c.get("content", ""))]
        if len(hits) != 1:
            reasons.append(f"{fact.id}: 片段命中父块数={len(hits)}")
            continue
        if sha256_text(str(hits[0]["content"])) != fact.parent_sha256:
            reasons.append(f"{fact.id}: 父块摘要不符")
            continue
        facts.append(VerifiedFact(doc=fact.doc, seq=int(hits[0]["seq"])))
    if reasons:
        return PageCheck(page.page_id, False, (), tuple(reasons))
    return PageCheck(page.page_id, True, tuple(facts), ())


class WikiIndexPort(Protocol):
    """主题页查找(向量命中 + 运行前核对);只返回已核对页面。"""

    async def lookup(self, question: str, limit: int) -> list[PageCheck]: ...


@dataclass(frozen=True)
class Evidence:
    """一种方式的取材结果;switch_reason 记录转用/单路失败原因。"""

    strategy: str
    items: tuple[tuple[str, int], ...]
    switch_reason: str | None


def _dedupe(items: list[tuple[str, int]]) -> tuple[tuple[str, int], ...]:
    return tuple(dict.fromkeys(items))


async def _raw(probe: ProbePort, question: str) -> list[tuple[str, int]]:
    return list(await probe.probe(question))


def _wiki_items(pages: list[PageCheck]) -> list[tuple[str, int]]:
    return [(f.doc, f.seq) for page in pages if page.usable for f in page.facts]


async def gather_evidence(
    strategy: str,
    question: str,
    *,
    probe: ProbePort,
    wiki: WikiIndexPort,
    limit: int = 5,
    timeout_s: float | None = None,
) -> Evidence:
    """按方式组装证据;并行方式单路失败时另一路有效即继续作答。"""
    if strategy == "raw":
        return Evidence("raw", _dedupe(await _raw(probe, question)), None)
    if strategy == "wiki_first":
        try:
            pages = await asyncio.wait_for(wiki.lookup(question, limit), timeout_s)
        except TimeoutError:
            return Evidence("wiki_first", _dedupe(await _raw(probe, question)), "主题页查找超时")
        except Exception as e:  # noqa: BLE001  # 单路失败按转用处理,原因入报告
            return Evidence(
                "wiki_first", _dedupe(await _raw(probe, question)), f"主题页查找失败: {e!r}"
            )
        items = _wiki_items(pages)
        if items:
            return Evidence("wiki_first", _dedupe(items), None)
        return Evidence("wiki_first", _dedupe(await _raw(probe, question)), "无可用主题页或出处")
    if strategy != "parallel":
        raise ValueError(f"未知取材方式: {strategy}")
    results = await asyncio.gather(
        asyncio.wait_for(wiki.lookup(question, limit), timeout_s),
        asyncio.wait_for(_raw(probe, question), timeout_s),
        return_exceptions=True,
    )
    wiki_result, raw_result = results
    wiki_items = _wiki_items(wiki_result) if isinstance(wiki_result, list) else []
    raw_items = raw_result if isinstance(raw_result, list) else []
    if not wiki_items and not raw_items:
        raise RuntimeError("并行取材两路均不可用")
    if not wiki_items:
        reason: str | None = "主题页路不可用,仅原文路"
    elif not raw_items:
        reason = "原文路不可用,仅主题页路"
    else:
        reason = None
    return Evidence("parallel", _dedupe(wiki_items + raw_items), reason)


async def verify_pages(pages: list[Page], docs_port: DocsPort) -> list[PageCheck]:
    """运行前逐页核对(只读);页面引用的分块按文档批量读取。"""
    docs = await docs_port.list_docs()
    docs_by_name = {str(d.get("doc_name")): d for d in docs}
    chunks: dict[str, list[dict[str, Any]]] = {}
    for page in pages:
        for doc_name in page.source_sha:
            row = docs_by_name.get(doc_name)
            if row is not None and doc_name not in chunks:
                chunks[doc_name] = await docs_port.list_chunks(str(row["id"]))
    return [check_page(page, docs, chunks) for page in pages]
