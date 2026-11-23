"""主题页格式与离线校验:页面事实 → corpus 父块(片段唯一命中 + 摘要核对)。

页面为评测目录下的 Markdown 文件:front-matter 记录页面编号、来源快照
与逐条事实出处,正文带 [[fN]] 标记。资料编号与分块编号不入页(同名覆盖
更新会重生成、父子序号随分块变化);运行时以文档名、片段与父块摘要核对。
"""

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

import yaml

from kongming.rag.chunker import chunk_document

_FRONT_MATTER = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.S)
_MARKER = re.compile(r"\[\[(\w+)\]\]")


class PageError(ValueError):
    """页面格式或出处非法。"""


class ChatPort(Protocol):
    """页面生成/评审使用的对话端口(生产=JudgeLlm)。"""

    async def chat(self, system: str, user: str, *, max_tokens: int = 1200) -> str: ...


@dataclass(frozen=True)
class Fact:
    """单条事实:文档名 + 原文片段 + 所在父块全文摘要。"""

    id: str
    doc: str
    snippet: str
    parent_sha256: str


@dataclass(frozen=True)
class Page:
    """一张评测主题页(出处清单 + 正文)。"""

    page_id: str
    title: str
    version: int
    body: str
    facts: tuple[Fact, ...]
    source_sha: Mapping[str, str]
    generated_by: str = ""
    prompt_version: str = ""
    review_status: str = ""
    reviewer: str = ""
    reviewed_at: str = ""
    review_minutes: int | None = None


def sha256_text(text: str) -> str:
    """UTF-8 文本 SHA-256 十六进制摘要。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_page_text(text: str, *, origin: str = "<text>") -> Page:
    """解析页面 front-matter 与正文;结构缺失即 PageError。"""
    matched = _FRONT_MATTER.match(text)
    if matched is None:
        raise PageError(f"{origin}: 缺少 front-matter")
    meta = cast("dict[str, Any]", yaml.safe_load(matched.group(1)))
    facts_raw = cast("list[dict[str, Any]]", meta.get("facts") or [])
    review = cast("dict[str, Any]", meta.get("review") or {})
    minutes_raw = review.get("minutes")
    review_minutes = (
        int(minutes_raw)
        if isinstance(minutes_raw, int) and not isinstance(minutes_raw, bool) and minutes_raw > 0
        else None
    )
    return Page(
        page_id=str(meta["page_id"]),
        title=str(meta["title"]),
        version=int(meta.get("version", 1)),
        body=matched.group(2),
        facts=tuple(
            Fact(
                id=str(f["id"]),
                doc=str(f["doc"]),
                snippet=str(f["snippet"]),
                parent_sha256=str(f["parent_sha256"]),
            )
            for f in facts_raw
        ),
        source_sha={
            str(s["doc"]): str(s["doc_sha256"])
            for s in cast("list[dict[str, Any]]", meta.get("sources") or [])
        },
        generated_by=str(meta.get("generated_by") or ""),
        prompt_version=str(meta.get("prompt_version") or ""),
        review_status=str(review.get("status") or ""),
        reviewer=str(review.get("reviewer") or ""),
        reviewed_at=str(review.get("reviewed_at") or ""),
        review_minutes=review_minutes,
    )


def load_page(path: Path) -> Page:
    """读取单个页面文件。"""
    return parse_page_text(path.read_text(encoding="utf-8"), origin=path.name)


def load_pages(directory: Path) -> list[Page]:
    """读取目录下全部页面(按文件名排序)。"""
    return [load_page(path) for path in sorted(directory.glob("*.md"))]


def _review_problems(page: Page) -> list[str]:
    """正式目录的审核门禁:approved 状态、评审者/日期与人工分钟缺一即拒绝。"""
    ok = page.review_status == "approved" and bool(page.reviewer) and bool(page.reviewed_at)
    problems: list[str] = [] if ok else ["未审核: 缺少 approved 状态或评审者/日期"]
    if page.review_minutes is None:
        problems.append("未登记主题页维护时间(review.minutes)")
    return problems


def review_problems(pages: list[Page]) -> tuple[str, ...]:
    """整页集合的正式对照门禁问题(未审核/缺人工分钟);空=通过。"""
    problems: list[str] = []
    for page in pages:
        problems.extend(f"{page.page_id}: {item}" for item in _review_problems(page))
    return tuple(problems)


def maintenance_minutes(pages: list[Page]) -> float | None:
    """主题页人工核对分钟合计;任一页未登记返回 None(未登记口径,不估算)。"""
    values = [page.review_minutes for page in pages]
    if not values or any(value is None for value in values):
        return None
    return float(sum(cast("int", value) for value in values))


def validate_page(
    page: Page, corpus: Mapping[str, str], *, require_review: bool = False
) -> list[str]:
    """离线校验:来源摘要、片段唯一落入父块、父块摘要、正文标记覆盖。

    require_review=True 时还要求 review.status=approved 且记录评审者与日期
    (正式评测目录的落盘门禁)。"""
    problems: list[str] = []
    if require_review:
        problems.extend(_review_problems(page))
    for doc, want in page.source_sha.items():
        text = corpus.get(doc)
        if text is None:
            problems.append(f"来源不在语料: {doc}")
        elif sha256_text(text) != want:
            problems.append(f"整文档摘要不符: {doc}")
    parents: dict[str, list[dict[str, object]]] = {
        doc: [c for c in chunk_document(doc, text) if c["level"] == "PARENT"]
        for doc, text in corpus.items()
    }
    problems.extend(_fact_problems(page, parents))
    return problems


def _fact_problems(page: Page, parents: Mapping[str, list[dict[str, object]]]) -> list[str]:
    """事实出处与正文标记校验;来源缺失时由调用方先行记录。"""
    problems: list[str] = []
    for fact in page.facts:
        if fact.doc not in page.source_sha:
            problems.append(f"{fact.id}: 文档未列入来源: {fact.doc}")
            continue
        hits = [p for p in parents.get(fact.doc, []) if fact.snippet in str(p["content"])]
        if len(hits) != 1:
            problems.append(f"{fact.id}: 片段命中父块数={len(hits)}")
            continue
        if sha256_text(str(hits[0]["content"])) != fact.parent_sha256:
            problems.append(f"{fact.id}: 父块摘要不符")
    ids = {fact.id for fact in page.facts}
    marked = set(_MARKER.findall(page.body))
    if marked - ids:
        problems.append(f"正文引用未知事实 {sorted(marked - ids)}")
    if ids - marked:
        problems.append(f"事实未被正文引用 {sorted(ids - marked)}")
    return problems


def validate_pages(
    pages: list[Page], corpus: Mapping[str, str], *, require_review: bool = False
) -> dict[str, list[str]]:
    """批量校验;只返回有问题的页面。"""
    return {
        page.page_id: problems
        for page in pages
        if (problems := validate_page(page, corpus, require_review=require_review))
    }


_GENERATE_SYSTEM = (
    "你是评测资料整理助手。只依据给定原文写主题页事实,不得补充原文之外的信息。"
    '输出严格 JSON:{"facts":[{"doc":"文档名","snippet":"原文逐字片段"}],'
    '"body":"带 [[f1]]、[[f2]] 标记的正文"}。snippet 必须逐字出现在对应文档中。'
)


async def generate_page(
    chat: ChatPort,
    *,
    page_id: str,
    title: str,
    docs: Mapping[str, str],
    generated_by: str,
    prompt_version: str,
) -> Page:
    """由固定版本原文生成页面草案;片段必须逐字可核验,否则拒绝生成。

    生成结果仅作草案:人工核对前不带 review 标记,正式评测目录另行落盘。"""
    source_block = "\n\n".join(f"## {name}\n{text}" for name, text in docs.items())
    raw = await chat.chat(
        _GENERATE_SYSTEM,
        f"页面编号:{page_id}\n页面标题:{title}\n原文如下:\n{source_block}",
        max_tokens=1600,
    )
    try:
        loaded = cast("object", yaml.safe_load(raw.strip()))
    except yaml.YAMLError as e:
        raise PageError(f"{page_id}: 生成结果非 JSON/YAML") from e
    if not isinstance(loaded, dict):
        raise PageError(f"{page_id}: 生成结果不是映射")
    payload = cast("dict[str, Any]", loaded)
    facts_raw = cast("list[dict[str, Any]]", payload.get("facts") or [])
    if not facts_raw:
        raise PageError(f"{page_id}: 生成结果缺少事实")
    parents = {
        name: [c for c in chunk_document(name, text) if c["level"] == "PARENT"]
        for name, text in docs.items()
    }
    facts: list[Fact] = []
    for idx, item in enumerate(facts_raw, start=1):
        doc = str(item.get("doc") or "")
        snippet = str(item.get("snippet") or "")
        hits = [p for p in parents.get(doc, []) if snippet and snippet in str(p["content"])]
        if len(hits) != 1:
            raise PageError(f"{page_id}: f{idx} 片段无法唯一核对(doc={doc})")
        facts.append(
            Fact(
                id=f"f{idx}",
                doc=doc,
                snippet=snippet,
                parent_sha256=sha256_text(str(hits[0]["content"])),
            )
        )
    return Page(
        page_id=page_id,
        title=title,
        version=1,
        body=str(payload.get("body") or ""),
        facts=tuple(facts),
        source_sha={name: sha256_text(text) for name, text in docs.items()},
        generated_by=generated_by,
        prompt_version=prompt_version,
    )
