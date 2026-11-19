"""检索质量评测(retrieval 层):语料幂等上传→逐题文档检索试跑→Recall@k/全跳覆盖。

gold=文档名+必含片段(运行时经 chunk_document 映射父块,不硬编码块 id);
指标纯函数见 evals/retrieval_metrics.py(默认门禁单测守护)。运行:
pytest evals/retrieval -m retrieval(评估环境显式触发;缺凭证由父 conftest 整层跳过)。
基线口径:RETRIEVAL_EVAL 行进 README 基线表,红线=macro Recall@5 劣化 ≥5pp。
"""
# pyright: reportPrivateUsage=false

from pathlib import Path
from typing import Any, cast

import httpx
import pytest
import yaml
from conftest import API_BASE, _login

from evals.retrieval_metrics import (
    full_hop_coverage,
    gold_parent_keys,
    macro,
    recall_at_k,
)
from kongming.rag.chunker import chunk_document

pytestmark = [pytest.mark.retrieval]

RETR_DIR = Path(__file__).parent
_REQUIRED_CATEGORIES = {"single_hop", "multi_hop_doc", "multi_hop_cross"}


def _load_dataset() -> dict[str, Any]:
    yml = RETR_DIR / "retrieval_eval.yaml"
    loaded = cast("object", yaml.safe_load(yml.read_text(encoding="utf-8")))
    if not isinstance(loaded, dict):
        raise pytest.UsageError(f"{yml.name}: 顶层必须是映射(dataset/questions)")
    return cast("dict[str, Any]", loaded)


def _check_dataset(cfg: dict[str, Any], questions: list[dict[str, Any]]) -> None:
    """评测集自检:题量/多跳下限 + 跳型覆盖(retrieval-eval spec)。"""
    categories = {str(q["category"]) for q in questions}
    if not _REQUIRED_CATEGORIES.issubset(categories):
        raise pytest.UsageError(f"评测集缺跳型: {_REQUIRED_CATEGORIES - categories}")
    multihop = [q for q in questions if str(q["category"]).startswith("multi_hop")]
    if len(questions) < int(cfg.get("min_questions", 12)):
        raise pytest.UsageError(f"题量低于下限({cfg.get('min_questions')})")
    if len(multihop) < int(cfg.get("min_multihop", 5)):
        raise pytest.UsageError(f"多跳题数低于下限({cfg.get('min_multihop')})")


def _map_gold(
    questions: list[dict[str, Any]], texts: dict[str, str]
) -> dict[str, set[tuple[str, int]]]:
    """gold 预映射:语料本地分块(与服务端同一纯函数),片段非法即拒绝启动。"""
    gold_by_id: dict[str, set[tuple[str, int]]] = {}
    for q in questions:
        gold: set[tuple[str, int]] = set()
        for g in cast("list[dict[str, Any]]", q["gold"]):
            doc_name = str(g["doc"])
            if doc_name not in texts:
                raise pytest.UsageError(f"{q['id']}: gold 文档不在语料: {doc_name}")
            snippets = [str(s) for s in cast("list[Any]", g["snippets"])]
            gold |= gold_parent_keys(chunk_document(doc_name, texts[doc_name]), doc_name, snippets)
        gold_by_id[str(q["id"])] = gold
    return gold_by_id


async def _upload_corpus(
    c: httpx.AsyncClient, texts: dict[str, str], headers: dict[str, str], dsk: str
) -> list[str]:
    """语料幂等上传(同内容 skipped;内容变更重切,块数随更新),返回 doc_id 列表。"""
    doc_ids: list[str] = []
    for name, content in texts.items():
        r = await c.post(
            "/system/document/upload",
            headers=headers,
            files={"file": (name, content.encode("utf-8"), "text/markdown")},
            data={"datasource_key": dsk},
        )
        assert r.status_code == 200, f"{name}: 语料上传失败 {r.status_code} {r.text[:200]}"
        doc_ids.append(str(r.json()["doc_id"]))
    return doc_ids


async def _enrich_corpus(c: httpx.AsyncClient, doc_ids: list[str], headers: dict[str, str]) -> bool:
    """实体形态自发现:服务端启用实体抽取时对语料补抽(覆盖式);
    400 DOC_ENTITY_DISABLED=实体关形态,静默跳过(开关态进输出行)。"""
    entity_mode = False
    for doc_id in doc_ids:
        er = await c.post(f"/system/document/documents/{doc_id}/enrich", headers=headers)
        if er.status_code == 200:
            entity_mode = True
        elif er.status_code != 400 or er.json().get("detail") != "DOC_ENTITY_DISABLED":
            raise AssertionError(f"enrich 非预期失败 {er.status_code} {er.text[:150]}")
    return entity_mode


async def _probe_question(
    c: httpx.AsyncClient,
    q: dict[str, Any],
    headers: dict[str, str],
    dsk: str,
    top_k: int,
    gold_by_id: dict[str, set[tuple[str, int]]],
) -> tuple[str, str, float, bool, list[str], str | None]:
    """单题检索试跑评分;环境异常/非 200 记入该行错误列,不抛出。"""
    qid = str(q["id"])
    try:
        r = await c.get(
            "/system/document/retrieval-probe",
            headers=headers,
            params={
                "datasource_key": dsk,
                "question": str(q["question"]),
                "parent_top_k": top_k,
            },
        )
        if r.status_code != 200:
            return (qid, str(q["category"]), 0.0, False, [], f"HTTP {r.status_code}")
        ranked = [(str(p["doc_name"]), int(p["seq"])) for p in r.json()["parents"]]
        gold = gold_by_id[qid]
        rec = recall_at_k(gold, ranked, top_k)
        fh = full_hop_coverage(gold, ranked, top_k)
        missing = sorted(f"{d}#{s}" for d, s in gold.difference(ranked[:top_k]))
        return (qid, str(q["category"]), rec, fh, missing, None)
    except Exception as e:  # noqa: BLE001  # 单题环境异常不中断整层
        return (qid, str(q["category"]), 0.0, False, [], repr(e)[:120])


async def test_retrieval_quality_report() -> None:
    ds = _load_dataset()
    cfg = cast("dict[str, Any]", ds["dataset"])
    questions = cast("list[dict[str, Any]]", ds["questions"])
    tenant = str(cfg["tenant"])
    dsk = str(cfg["datasource_key"])
    top_k = int(cfg.get("top_k", 5))

    _check_dataset(cfg, questions)

    # gold 预映射:语料本地分块(与服务端同一纯函数)
    corpus_dir = RETR_DIR / str(cfg.get("corpus_dir", "corpus"))
    texts = {p.name: p.read_text(encoding="utf-8") for p in sorted(corpus_dir.glob("*.md"))}
    gold_by_id = _map_gold(questions, texts)

    token = await _login()
    headers = {"Authorization": f"Bearer {token}"}
    if tenant != "default":
        headers["X-Tenant-Id"] = tenant
    async with httpx.AsyncClient(base_url=API_BASE, timeout=120.0) as c:
        doc_ids = await _upload_corpus(c, texts, headers, dsk)
        entity_mode = await _enrich_corpus(c, doc_ids, headers)

        # 逐题试跑评分(单题失败不中断:先记异常,末尾统一报告)
        rows: list[tuple[str, str, float, bool, list[str], str | None]] = [
            await _probe_question(c, q, headers, dsk, top_k, gold_by_id) for q in questions
        ]

    macro_rec = macro([r[2] for r in rows])
    hop_rate = (sum(1 for r in rows if r[3]) / len(rows)) if rows else 0.0
    for qid, cat, rec, fh, missing, err in rows:
        mark = err or f"missing={missing}" if (missing or err) else "hit-all"
        print(f"[retrieval] {qid} {cat} recall@{top_k}={rec:.2f} full_hop={int(fh)} {mark}")
    print(
        f"RETRIEVAL_EVAL macro_recall@{top_k}={macro_rec:.3f} "
        f"full_hop_rate={hop_rate:.3f} n={len(rows)} entity={'on' if entity_mode else 'off'}"
    )
    # 回归闸:macro Recall@k 下限(链路坏档如向量路死亡/语料失配必破;
    # 语料扩容后单题零命中属合法基线形态,闸门只拦系统性坏档)
    env_errs = [r[0] for r in rows if r[5] is not None]
    assert not env_errs, f"试跑环境异常题: {env_errs}"
    assert macro_rec >= 0.5, f"macro Recall@{top_k}={macro_rec:.3f} 低于坏档闸 0.5,检索链路异常"
