"""全文/向量门槛标定(只读):正例(题集)与负例(语料外问题)的原生分分布。

探针走文档通道 `/system/document/retrieval-probe`:向量路原生分是余弦距离,
这里换算为余弦相似度 `1-distance`,与 `kongming/rag/gate.py` 的向量路口径
一致;全文路原生分已是 ts_rank(越大越好)。判据对齐 gate.py:逐路命中按
分数降序,top1 与前 mean_window 位均值双条件。输出两群分布、可分性与候选
阈值下正例过闸率/负例拒绝率,不写任何配置。`KM_RAG_GATE_*` 作用于 schema
召回通道,本文档通道分布只作候选参考,写入前须在目标通道复测。

前提:评测环境已起(api/agent),语料已上传(先跑一次 retrieval-eval 即可)。
运行:`KM_EVAL_ACCESS_TOKEN=... KM_EVAL_API_BASE=... uv run python \
evals/retrieval/calibrate_fulltext_gate.py [数据集名,默认 retrieval_scale.yaml]`
"""

# pyright: reportPrivateUsage=false
import asyncio
import sys
from pathlib import Path
from typing import Any, cast

import httpx
import yaml

_EVALS = Path(__file__).parent.parent
if str(_EVALS) not in sys.path:
    sys.path.insert(0, str(_EVALS))

from conftest import API_BASE, _login  # noqa: E402  # 路径注入后导入评测公共件

RETR_DIR = Path(__file__).parent
MEAN_WINDOW = 3


def _load_yaml(path: Path) -> dict[str, Any]:
    loaded = cast("object", yaml.safe_load(path.read_text(encoding="utf-8")))
    if not isinstance(loaded, dict):
        raise SystemExit(f"{path.name}: 顶层必须是映射")
    return cast("dict[str, Any]", loaded)


async def _route_scores(
    c: httpx.AsyncClient, headers: dict[str, str], dsk: str, question: str
) -> dict[str, tuple[float, float]]:
    """单问逐路 (top1, 前 N 均值);无命中的路不出现在结果里。"""
    r = await c.get(
        "/system/document/retrieval-probe",
        headers=headers,
        params={"datasource_key": dsk, "question": question, "parent_top_k": 5},
    )
    r.raise_for_status()
    body = cast("dict[str, Any]", r.json())
    out: dict[str, tuple[float, float]] = {}
    for name, hits in cast("dict[str, list[dict[str, Any]]]", body.get("paths") or {}).items():
        scores = [float(h["score"]) for h in hits]
        if name == "vector":
            # 探针向量路原始分=余弦距离(越小越相似),换算成 gate.py 的相似度口径
            scores = [1.0 - score for score in scores]
        if not scores:
            continue
        window = scores[:MEAN_WINDOW]
        out[name] = (scores[0], sum(window) / len(window))
    return out


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, int(q / 100.0 * len(ordered)) - 1))
    return ordered[idx]


def _suggest(pos: list[float], neg: list[float]) -> str:
    """在负例最大值与正例 p10 之间取中点;不可分则如实报告。"""
    if not pos or not neg:
        return "样本不足"
    floor = max(neg)
    ceil = _pct(pos, 10)
    if floor >= ceil:
        return f"不可分(负例最大 {floor:.4f} ≥ 正例 p10 {ceil:.4f})"
    mid = round((floor + ceil) / 2.0, 4)
    pos_pass = sum(1 for v in pos if v >= mid) / len(pos)
    neg_reject = sum(1 for v in neg if v < mid) / len(neg)
    return f"候选阈值 {mid:.4f}(正例过闸 {pos_pass:.0%},负例拒绝 {neg_reject:.0%})"


async def main() -> None:
    dataset_name = sys.argv[1] if len(sys.argv) > 1 else "retrieval_scale.yaml"
    ds = _load_yaml(RETR_DIR / dataset_name)
    cfg = cast("dict[str, Any]", ds["dataset"])
    questions = cast("list[dict[str, Any]]", ds["questions"])
    negs = cast("list[dict[str, Any]]", _load_yaml(RETR_DIR / "negative_queries.yaml")["queries"])
    dsk = str(cfg["datasource_key"])
    tenant = str(cfg.get("tenant", "default"))
    token = await _login()
    headers = {"Authorization": f"Bearer {token}"}
    if tenant != "default":
        headers["X-Tenant-Id"] = tenant
    async with httpx.AsyncClient(base_url=API_BASE, timeout=120.0) as c:
        pos_rows = [await _route_scores(c, headers, dsk, str(q["question"])) for q in questions]
        neg_rows = [await _route_scores(c, headers, dsk, str(q["question"])) for q in negs]
    routes = sorted({name for row in [*pos_rows, *neg_rows] for name in row})
    for route in routes:
        pos_top1 = [row[route][0] for row in pos_rows if route in row]
        pos_mean = [row[route][1] for row in pos_rows if route in row]
        neg_top1 = [row[route][0] for row in neg_rows if route in row]
        neg_mean = [row[route][1] for row in neg_rows if route in row]
        print(
            f"[{route}] 正例 n={len(pos_top1)} top1 min/p10/中位={_fmt(pos_top1)};"
            f" 负例 n={len(neg_top1)} top1 中位/max={_fmt_max(neg_top1)}"
        )
        print(f"  top1 建议: {_suggest(pos_top1, neg_top1)}")
        print(f"  mean{MEAN_WINDOW} 建议: {_suggest(pos_mean, neg_mean)}")


def _fmt(values: list[float]) -> str:
    return f"{min(values, default=0):.4f}/{_pct(values, 10):.4f}/{_pct(values, 50):.4f}"


def _fmt_max(values: list[float]) -> str:
    return f"{_pct(values, 50):.4f}/{max(values, default=0):.4f}"


if __name__ == "__main__":
    asyncio.run(main())
