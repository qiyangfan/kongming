"""将审核通过的候选题并入正式评估集并重建基线。
候选移动并入正式评估集 + 全量评估重跑 + 基线行登记(原因=自动构造扩题)。

人审是唯一并入路径(proposal Non-goal:不做无人审自动入集);本模块只提供
收口命令,不做任何「自动并入」。基线表=evals/README.md 表格(本仓基线单源)。
"""
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false

import re
import subprocess
import sys
from pathlib import Path
from typing import cast

import yaml

from evals.construct.generate import CANDIDATES_DIR, EVALS_DIR

README = EVALS_DIR / "README.md"

# 租户 → 正式评估集文件(与 README「评估集分层」表一致)
_TENANT_DATASET = {"sakila": "sakila.yaml", "olist": "olist.yaml", "default": "default.yaml"}


class MergeError(RuntimeError):
    """并入前置不满足(候选缺失/未过验证/目标文件缺失)。"""


def load_candidates(path: Path) -> list[dict[str, object]]:
    """读候选文件(畸形=显式失败,不静默缩并入分母)。"""
    try:
        loaded = cast("object", yaml.safe_load(path.read_text(encoding="utf-8")))
    except yaml.YAMLError as e:
        raise MergeError(f"候选文件解析失败: {e}") from e
    if not isinstance(loaded, list):
        raise MergeError("候选文件顶层必须是列表")
    return [cast("dict[str, object]", c) for c in loaded if isinstance(c, dict)]


def select_mergeable(
    candidates: list[dict[str, object]], ids: list[str]
) -> list[dict[str, object]]:
    """人审圈定 ids → 只取验证通过(ok=True)的候选;未过验证的 id 显式拒绝。"""
    by_id = {str(c.get("id")): c for c in candidates}
    missing = [i for i in ids if i not in by_id]
    if missing:
        raise MergeError(f"候选 id 不存在: {missing}")
    unmergeable = [
        i
        for i in ids
        if (cast("dict[str, object]", by_id[i].get("validation") or {})).get("ok") is not True
    ]
    if unmergeable:
        raise MergeError(f"候选未过自洽验证,SHALL NOT 并入: {unmergeable}")
    return [by_id[i] for i in ids]


def _table_list(candidate: dict[str, object]) -> list[str]:
    """expected_tables 字段收窄(缺失/异形=空,不再猜)。"""
    tables = candidate.get("expected_tables")
    if not isinstance(tables, list):
        return []
    return [str(cast("object", t)) for t in tables]


def to_eval_case(candidate: dict[str, object], tenant: str) -> dict[str, object]:
    """候选 → 正式评估用例(剥验证报告;字段对齐 eval 域评估集结构)。"""
    return {
        "id": str(candidate["id"]),
        # 显式租户章:候选 id 是内容哈希(无租户前缀),评估器按 id 前缀
        # 按候选 id 推断租户会得到无效租户并返回 403；并入时
        # 已知租户,必须显式携带
        "tenant": tenant,
        "question": str(candidate["question"]),
        "gold_sql": str(candidate["gold_sql"]),
        "expected_tables": _table_list(candidate),
        "category": str(candidate["category"]),
    }


def _atomic_write_text(path: Path, text: str) -> None:
    """tmp+rename 原子写(进程中断不撕裂文件;rename 同目录原子)。
    并发 merge 的丢更新由「单操作者」假设覆盖(工具形态:人审是唯一并入
    路径,两人同时收口同一租户属流程违规,不做文件锁过度设计)。"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def append_cases(dataset_path: Path, cases: list[dict[str, object]]) -> int:
    """并入正式评估集(追加;id 冲突=显式拒绝防重复扩题)。返回并入数。
    注意:yaml.dump 全量重写会丢失文件内注释——编写规范/逐题决策注释在
    git 历史与 README(规范单源),并入后如有需要从历史恢复。"""
    try:
        existing = cast(
            "list[object]", yaml.safe_load(dataset_path.read_text(encoding="utf-8")) or []
        )
    except yaml.YAMLError as e:
        raise MergeError(f"正式评估集解析失败: {e}") from e
    typed_existing = [cast("dict[str, object]", c) for c in existing if isinstance(c, dict)]
    existing_ids = {str(c.get("id")) for c in typed_existing}
    conflicts = [str(c["id"]) for c in cases if str(c["id"]) in existing_ids]
    if conflicts:
        raise MergeError(f"id 与既有用例冲突: {conflicts}")
    existing.extend(cases)
    _atomic_write_text(
        dataset_path, yaml.dump(existing, allow_unicode=True, sort_keys=False, width=120)
    )
    return len(cases)


def build_baseline_row(stats_line: str, reason: str) -> str:
    """全量评估统计行 → README 基线表行(纯函数;断言过=)。"""
    from datetime import datetime

    date = datetime.now().strftime("%Y-%m-%d")
    m = re.search(r"宽松\s*(\d+/\d+)\s*\((\d+%)\)", stats_line)
    loose = f"{m.group(1)} ({m.group(2)})" if m else "—"
    m = re.search(r"严格\s*(\d+/\d+)\s*\((\d+%)\)", stats_line)
    strict = f"{m.group(1)} ({m.group(2)})" if m else "—"
    m = re.search(r"多轮\s*(\S+)", stats_line)
    multiturn = m.group(1) if m else "—"
    m = re.search(r"动态数据源\s*(\S+)", stats_line)
    ds = m.group(1) if m else "—"
    m = re.search(r"表命中\s*(\S+)\s*\(", stats_line)
    recall = m.group(1) if m else "—"
    m = re.search(r"prompt 指纹\s*([0-9a-f]{8}|指纹不可得)", stats_line)
    fp = m.group(1) if m else "—"
    return f"| {date} | {reason} | {strict} | {loose} | {multiturn} | {ds} | {recall} | {fp} |"


def append_baseline_row(row: str) -> None:
    """基线行追加到 README 表末(插到最后一条表行之后;原子写防撕裂)。"""
    text = README.read_text(encoding="utf-8")
    lines = text.splitlines()
    last_table_idx = max(i for i, ln in enumerate(lines) if ln.startswith("| 20"))
    lines.insert(last_table_idx + 1, row)
    _atomic_write_text(README, "\n".join(lines) + "\n")


def run_full_eval() -> str:
    """重跑全量评估(子进程,继承环境;统计行从输出提取)。"""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "evals", "-m", "full", "-q", "-s"],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(EVALS_DIR.parent),
    )
    output = proc.stdout + proc.stderr
    if proc.returncode != 0:
        print(output[-4000:], file=sys.stderr)
        raise MergeError(f"全量评估失败(exit={proc.returncode}),基线不登记")
    m = re.search(r"=== 评估统计:.*===", output)
    if not m:
        raise MergeError("全量评估输出缺统计行,基线不登记")
    return m.group(0)


def merge(
    tenant: str,
    ids: list[str],
    *,
    with_full_eval: bool = True,
    reason: str = "自动构造扩题(092 三段闸人审并入)",
) -> Path:
    """并入收口一条命令:候选校验 → 移动并入 → 全量重跑 → 基线行登记。"""
    dataset_name = _TENANT_DATASET.get(tenant)
    if dataset_name is None:
        raise MergeError(f"未登记租户评估集映射: {tenant}(缺省 sakila/olist/default)")
    candidates = load_candidates(CANDIDATES_DIR / tenant / "candidates.yaml")
    cases = [to_eval_case(c, tenant) for c in select_mergeable(candidates, ids)]
    dataset_path = EVALS_DIR / dataset_name
    merged = append_cases(dataset_path, cases)
    print(f"已并入 {merged} 条候选 → {dataset_path}")
    if with_full_eval and merged > 0:
        stats_line = run_full_eval()
        print(stats_line)
        append_baseline_row(build_baseline_row(stats_line, reason))
        print(f"基线行已登记({reason})")
    return dataset_path
