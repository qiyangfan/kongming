"""BM25 对照评测运行器(隔离实例):同题配对试跑 → 覆盖率/耗时/结论。

两组共用同一资料快照、分词、向量模型与候选上限;基线=现有 API
(ts_rank),实验组=评测启动器拉起的 BM25 实例。运行:
`pytest evals/retrieval/test_bm25_eval.py -m retrieval`(需 api/agent 与
LLM 凭证;缺凭证由父 conftest 整层跳过)。正式对照须业务语料与独立题集,
仓库题库运行只作调试,结论按配置判为待验证。
"""

# pyright: reportPrivateUsage=false

import os
import time
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
import yaml
from conftest import API_BASE, _login

from evals.retrieval.bm25_launcher import launch_bm25_api
from evals.retrieval.bm25_metrics import (
    assess,
    build_groups,
    config_issues,
    coverage_issues,
    paired_ci,
    percentile,
)
from evals.retrieval.bm25_scope import doc_snapshot, snapshot_diff
from evals.retrieval.test_retrieval_eval import _check_dataset, _map_gold, _upload_corpus
from evals.retrieval_metrics import full_hop_coverage, recall_at_k

pytestmark = [pytest.mark.retrieval]

RETR_DIR = Path(__file__).parent


def _load_dataset_file(name: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """按运行条件选择题集文件;正式题集与调试题集分开存放。"""
    path = RETR_DIR / name
    loaded = cast("object", yaml.safe_load(path.read_text(encoding="utf-8")))
    if not isinstance(loaded, dict):
        raise pytest.UsageError(f"{path.name}: 顶层必须是映射")
    ds = cast("dict[str, Any]", loaded)
    return cast("dict[str, Any]", ds["dataset"]), cast("list[dict[str, Any]]", ds["questions"])


def _load_conditions() -> dict[str, Any]:
    """运行条件(bm25_eval.yaml);缺文件即拒绝启动。"""
    path = RETR_DIR / "bm25_eval.yaml"
    loaded = cast("object", yaml.safe_load(path.read_text(encoding="utf-8")))
    if not isinstance(loaded, dict):
        raise pytest.UsageError(f"{path.name}: 顶层必须是映射")
    return cast("dict[str, Any]", loaded)


async def _timed_probe(
    c: httpx.AsyncClient,
    headers: dict[str, str],
    dsk: str,
    question: str,
    top_k: int,
) -> tuple[dict[str, Any], float]:
    """单题试跑;非 200 显式失败(对照结论不接受残缺样本)。"""
    started = time.perf_counter()
    resp = await c.get(
        "/system/document/retrieval-probe",
        headers=headers,
        params={"datasource_key": dsk, "question": question, "parent_top_k": top_k},
    )
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    if resp.status_code != 200:
        raise AssertionError(f"试跑失败 {resp.status_code}: {resp.text[:200]}")
    return cast("dict[str, Any]", resp.json()), elapsed_ms


async def _docs_list(
    c: httpx.AsyncClient, headers: dict[str, str], dsk: str
) -> list[dict[str, Any]]:
    """资料列表快照(运行前后核对;只读)。"""
    resp = await c.get(
        "/system/document/documents", headers=headers, params={"datasource_key": dsk}
    )
    assert resp.status_code == 200, f"资料列表失败 {resp.status_code}"
    return cast("list[dict[str, Any]]", resp.json())


def _vector_order(body: dict[str, Any]) -> list[str]:
    """试跑向量路子块名次(两组需逐题一致)。"""
    return [str(hit["child_id"]) for hit in body["paths"]["vector"]]


def _ranked(body: dict[str, Any]) -> list[tuple[str, int]]:
    """试跑返回的父块名次键(判定单位)。"""
    return [(str(p["doc_name"]), int(p["seq"])) for p in body["parents"]]


async def _paired_row(
    base_client: httpx.AsyncClient,
    exp_client: httpx.AsyncClient,
    headers: dict[str, str],
    dsk: str,
    q: dict[str, Any],
    top_k: int,
    gold: set[tuple[str, int]],
) -> tuple[dict[str, Any] | None, float, float]:
    """单题配对;向量名次重跑一次仍不一致返回 (None, …) 记失败。"""

    async def probe(client: httpx.AsyncClient) -> tuple[dict[str, Any], float]:
        return await _timed_probe(client, headers, dsk, str(q["question"]), top_k)

    base_body, base_ms = await probe(base_client)
    exp_body, exp_ms = await probe(exp_client)
    if _vector_order(base_body) != _vector_order(exp_body):
        base_body, base_ms = await probe(base_client)
        exp_body, exp_ms = await probe(exp_client)
    if _vector_order(base_body) != _vector_order(exp_body):
        return None, base_ms, exp_ms
    cat = str(q["category"])
    fallback = "multihop" if cat.startswith("multi_hop") else "single_hop"
    declared = cast("list[str]", q.get("groups") or [fallback])
    groups = list(dict.fromkeys(["overall", *declared]))
    base_ranked, exp_ranked = _ranked(base_body), _ranked(exp_body)
    row = {
        "qid": str(q["id"]),
        "groups": groups,
        "base_recall": recall_at_k(gold, base_ranked, top_k),
        "exp_recall": recall_at_k(gold, exp_ranked, top_k),
        "base_full_hop": float(full_hop_coverage(gold, base_ranked, top_k)),
        "exp_full_hop": float(full_hop_coverage(gold, exp_ranked, top_k)),
    }
    return row, base_ms, exp_ms


async def test_bm25_paired_report() -> None:
    conditions = _load_conditions()
    if issues := config_issues(conditions):
        pytest.fail(f"运行条件缺项: {issues}")
    dataset_file = os.environ.get(
        "KM_EVAL_BM25_DATASET", str(conditions.get("dataset_file", "retrieval_eval.yaml"))
    )
    cfg, questions = _load_dataset_file(dataset_file)
    _check_dataset(cfg, questions)
    subtype_gaps = coverage_issues(
        questions, cast("dict[str, int]", conditions.get("min_subtype_counts", {}))
    )
    top_k = int(conditions["top_k"])
    tenant = str(cfg["tenant"])
    dsk = str(cfg["datasource_key"])
    corpus_dir = RETR_DIR / str(cfg.get("corpus_dir", "corpus"))
    texts = {p.name: p.read_text(encoding="utf-8") for p in sorted(corpus_dir.glob("*.md"))}
    gold_by_id = _map_gold(questions, texts)

    token = await _login()
    headers = {"Authorization": f"Bearer {token}"}
    if tenant != "default":
        headers["X-Tenant-Id"] = tenant

    rows: list[dict[str, Any]] = []
    failures: list[str] = []
    exp_latencies: list[float] = []
    async with httpx.AsyncClient(base_url=API_BASE, timeout=120.0) as base_client:
        await _upload_corpus(base_client, texts, headers, dsk)
        before = doc_snapshot(await _docs_list(base_client, headers, dsk))
        with launch_bm25_api() as bm25_base:
            async with httpx.AsyncClient(base_url=bm25_base, timeout=120.0) as exp_client:
                for q in questions:
                    row, _, exp_ms = await _paired_row(
                        base_client, exp_client, headers, dsk, q, top_k, gold_by_id[str(q["id"])]
                    )
                    if row is None:
                        failures.append(str(q["id"]))
                        continue
                    rows.append(row)
                    exp_latencies.append(exp_ms)
        after = doc_snapshot(await _docs_list(base_client, headers, dsk))

    scope_ok = snapshot_diff(before, after) == []
    groups_stat = build_groups(rows)
    precise_diffs = [
        float(r["exp_recall"]) - float(r["base_recall"])
        for r in rows
        if "precise" in cast("list[str]", r["groups"])
    ]
    ci = paired_ci(
        precise_diffs,
        samples=int(conditions["bootstrap_samples"]),
        seed=int(conditions["bootstrap_seed"]),
    )
    latency_p95 = percentile(exp_latencies, 95)
    verdict, reasons = assess(
        groups_stat,
        precise_ci=ci,
        latency_p95_ms=latency_p95,
        latency_cap_ms=float(conditions["latency_cap_ms_p95"]),
        config_issues_=[*subtype_gaps],
        scope_ok=scope_ok,
    )
    for row in rows:
        print(
            f"[bm25] {row['qid']} groups={row['groups']} "
            f"base={row['base_recall']:.2f} exp={row['exp_recall']:.2f} "
            f"full_hop={row['base_full_hop']:.0f}->{row['exp_full_hop']:.0f}"
        )
    print(
        f"BM25_EVAL verdict={verdict} scope_ok={scope_ok} "
        f"n={len(rows)} failures={failures} "
        f"p50={percentile(exp_latencies, 50):.0f}ms p95={latency_p95:.0f}ms "
        f"dataset={dataset_file} debug={conditions.get('debug')}"
    )
    if reasons:
        print(f"BM25_EVAL reasons={reasons}")
    assert not failures, f"两组向量名次无法对齐的题: {failures}"
    assert len(rows) == len(questions), "对照样本不完整,不能出结论"
    assert scope_ok, "对照期间资料发生变化,本次结果作废,需重新固定资料"
