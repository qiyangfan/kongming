"""评估劣化红线:解析评估输出里的通过率,与上次基线比较,掉超阈值即非零退出。

评测层不同(如 smoke → full)或 prompt 指纹不同时两侧不可比,只记录不判罚。
基线缺失(首次运行)同样只记录。

用法:
  python3 scripts/eval_redline.py summarize <输出文件> <层> <summary.json>
  python3 scripts/eval_redline.py compare <基线目录> <本次输出> <层>
"""

# ruff: noqa: T201  # CLI 门禁脚本以 stdout 汇报

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

DEFAULT_THRESHOLD = 0.05

_STAT_PAIRS = re.compile(r"(\d+)/(\d+)")
_JUDGE = re.compile(r"\[judge:([a-z0-9_-]+)\][^\n]*?通过率\s*(\d+)/(\d+)")
_RECALL = re.compile(r"macro_recall@(\d+)=([\d.]+)")
_HOP = re.compile(r"full_hop_rate=([\d.]+)")
_FINGERPRINT = re.compile(r"prompt 指纹\s+(\S+)")

# 评估统计行的 X/Y 顺序:宽松、严格、表命中、动态数据源
_STAT_NAMES = ("eval:pass", "eval:strict", "eval:recall", "eval:ds")


def extract_metrics(text: str) -> dict[str, float]:
    """从评估 stdout 提取可比指标(通过率/召回率,均为 0–1 比值)。"""
    metrics: dict[str, float] = {}
    for line in text.splitlines():
        if "评估统计" in line:
            pairs = _STAT_PAIRS.findall(line)
            for name, (num, den) in zip(_STAT_NAMES, pairs, strict=False):
                if int(den):
                    metrics[name] = int(num) / int(den)
    for kind, num, den in _JUDGE.findall(text):
        if int(den):
            metrics[f"judge:{kind}"] = int(num) / int(den)
    for top_k, value in _RECALL.findall(text):
        metrics[f"retrieval:macro_recall@{top_k}"] = float(value)
    hop = _HOP.findall(text)
    if hop:
        metrics["retrieval:full_hop"] = float(hop[-1])
    return metrics


def extract_fingerprint(text: str) -> str | None:
    """取 prompt 指纹;输出「指纹不可得」视为缺失,不参与可比性判断。"""
    match = _FINGERPRINT.search(text)
    if match is None or match.group(1) == "指纹不可得":
        return None
    return match.group(1)


def summarize(out_path: Path, layer: str, summary_path: Path) -> None:
    """把本次指标与层/指纹写进基线产物,供下次对比。"""
    text = out_path.read_text(encoding="utf-8")
    metrics = extract_metrics(text)
    payload = {
        "layer": layer,
        "fingerprint": extract_fingerprint(text),
        "metrics": metrics,
        "raw_tail": text[-2000:],
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"记录 {len(metrics)} 项指标 -> {summary_path}")


def compare(
    prev_dir: Path, cur_text: str, layer: str, threshold: float = DEFAULT_THRESHOLD
) -> tuple[str, list[str]]:
    """与基线比较,返回(结论说明, 劣化明细);明细非空即判罚。"""
    summary_path = prev_dir / "summary.json"
    if not summary_path.exists():
        return "无历史基线:只记录不判罚", []
    prev = json.loads(summary_path.read_text(encoding="utf-8"))
    if prev.get("layer") != layer:
        return f"基线层={prev.get('layer')} 与本次层={layer} 不同:跨层不可比,跳过", []
    prev_fp = prev.get("fingerprint")
    cur_fp = extract_fingerprint(cur_text)
    if prev_fp and cur_fp and prev_fp != cur_fp:
        return f"prompt 指纹不同({prev_fp} vs {cur_fp}):不可比,跳过", []
    prev_metrics: dict[str, float] = prev.get("metrics") or {}
    cur_metrics = extract_metrics(cur_text)
    common = sorted(set(prev_metrics).intersection(cur_metrics))
    if not common:
        return "两侧无共同指标:跳过", []
    violations = [
        f"{key}: {prev_metrics[key]:.1%} -> {cur_metrics[key]:.1%}"
        f"(掉 {prev_metrics[key] - cur_metrics[key]:.1%})"
        for key in common
        if prev_metrics[key] - cur_metrics[key] >= threshold - 1e-9
    ]
    return f"比较 {len(common)} 项指标,阈值 {threshold:.1%}", violations


def _summarize_cmd(argv: list[str]) -> int:
    if len(argv) != 5:
        print("用法: eval_redline.py summarize <输出文件> <层> <summary.json>")
        return 2
    summarize(Path(argv[2]), argv[3], Path(argv[4]))
    return 0


def _compare_cmd(argv: list[str]) -> int:
    if len(argv) != 5:
        print("用法: eval_redline.py compare <基线目录> <本次输出> <层>")
        return 2
    note, violations = compare(Path(argv[2]), Path(argv[3]).read_text(encoding="utf-8"), argv[4])
    print(note)
    for item in violations:
        print(f"::error::劣化超阈值 {item}")
    return 1 if violations else 0


def main(argv: list[str]) -> int:
    """分派 summarize/compare 子命令。"""
    if len(argv) < 2 or argv[1] not in {"summarize", "compare"}:
        print(__doc__)
        return 2
    return _summarize_cmd(argv) if argv[1] == "summarize" else _compare_cmd(argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
