"""Wiki 页原型校验(研究用,非评测实现)。

核验:事实片段唯一落入 corpus 父块、parent_seq 一致、父块全文 SHA-256
一致、sources 整文档 SHA-256 一致、正文标记与事实清单互相覆盖。
--fill 用实际计算值回填 PENDING 并重写 front-matter。

运行:cd apps/api && uv run python ../../openspec/changes/evaluate-wiki-document-qa/prototype/validate_pages.py [--fill]
"""

import hashlib
import re
import sys
from pathlib import Path
from typing import Any, cast

import yaml

from kongming.rag.chunker import chunk_document

ROOT = Path(__file__).resolve().parents[4]
CORPUS = ROOT / "apps/api/evals/retrieval/corpus"
PAGES = Path(__file__).resolve().parent / "pages"


def sha256(text: str) -> str:
    """返回 UTF-8 文本的 SHA-256 十六进制摘要。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_page(path: Path) -> tuple[dict[str, Any], str]:
    """拆出 front-matter(YAML)与正文;格式不符直接报错。"""
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    if m is None:
        raise SystemExit(f"{path.name}: 缺少 front-matter")
    return cast("dict[str, Any]", yaml.safe_load(m.group(1))), m.group(2)


def main() -> None:
    """逐页核验并汇总错误;--fill 时回填实际摘要。"""
    fill = "--fill" in sys.argv
    errors: list[str] = []
    for page in sorted(PAGES.glob("*.md")):
        meta, body = parse_page(page)
        docs = {
            str(s["doc"]): (CORPUS / str(s["doc"])).read_text(encoding="utf-8")
            for s in meta["sources"]
        }
        parents = {
            name: [c for c in chunk_document(name, text) if c["level"] == "PARENT"]
            for name, text in docs.items()
        }
        for src in meta["sources"]:
            want = sha256(docs[str(src["doc"])])
            if src["doc_sha256"] == "PENDING":
                src["doc_sha256"] = want
            elif src["doc_sha256"] != want:
                errors.append(f"{page.name} source {src['doc']}: 整文档摘要不符")
        for fact in meta["facts"]:
            hits = [
                p
                for p in parents.get(str(fact["doc"]), [])
                if str(fact["snippet"]) in str(p["content"])
            ]
            if len(hits) != 1:
                errors.append(f"{page.name} {fact['id']}: 片段命中父块数={len(hits)}")
                continue
            parent = hits[0]
            if int(cast("int", parent["seq"])) != int(fact["parent_seq"]):
                errors.append(
                    f"{page.name} {fact['id']}: seq={parent['seq']} 与登记 {fact['parent_seq']} 不符"
                )
            want = sha256(str(parent["content"]))
            if fact["parent_sha256"] == "PENDING":
                fact["parent_sha256"] = want
            elif fact["parent_sha256"] != want:
                errors.append(f"{page.name} {fact['id']}: 父块摘要不符")
        ids = {str(f["id"]) for f in meta["facts"]}
        marks = set(re.findall(r"\[\[(\w+)\]\]", body))
        if marks - ids:
            errors.append(f"{page.name}: 正文引用未知事实 {sorted(marks - ids)}")
        if ids - marks:
            errors.append(f"{page.name}: 事实未被正文引用 {sorted(ids - marks)}")
        print(
            f"{page.name}: sources={len(meta['sources'])} facts={len(meta['facts'])} "
            f"body_chars={len(body.strip())}"
        )
        if fill:
            dumped = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False)
            page.write_text(f"---\n{dumped}---\n{body}", encoding="utf-8")
    if errors:
        print("\n".join(f"FAIL {e}" for e in errors))
        raise SystemExit(1)
    print("PASS 全部页面校验通过")


if __name__ == "__main__":
    main()
