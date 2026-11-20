"""门禁脚本自身的单测:许可白名单拆分与镜像摘要校验的边界行为。"""

import importlib.util
import json
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"


def _load(name: str) -> ModuleType:
    """按文件路径加载 scripts/ 下的门禁脚本(它们不是可导入包)。"""
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_run(responses: Iterator[str]) -> Callable[[list[str], Path], str]:
    """按顺序吐出预置输出;任何多余的子进程调用都是测试用例写错了。"""

    def run(_argv: list[str], _cwd: Path) -> str:
        return next(responses)

    return run


licenses = _load("license_check")
pins = _load("pin_check")
links = _load("link_check")
redline = _load("eval_redline")

_DIGEST = "0" * 64


def test_atoms_split_composed_license() -> None:
    assert licenses._atoms("MIT AND Apache-2.0") == ["MIT", "APACHE-2.0"]
    assert set(licenses._atoms("ISC License (ISCL)")) == {"ISC LICENSE", "ISCL"}


def test_atoms_empty_license_has_no_atom() -> None:
    assert licenses._atoms("") == []


def test_disallowed_formats_rejected_atoms() -> None:
    assert licenses._disallowed("MIT AND GPL-3.0", licenses.PY_ALLOWED) == ["GPL-3.0"]


def test_python_gate_rejects_missing_license(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = iter(
        [
            "libx==1.0\n",
            "Name,Version,License\nlibx,1.0,\n",
        ]
    )
    monkeypatch.setattr(licenses, "_run", _fake_run(responses))
    problems = licenses.check_python(Path())
    assert problems == ["libx 1.0: 未声明许可证"]


def test_python_gate_skips_own_packages_but_rejects_gpl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter(
        [
            "kongming-api==0.1.0\nlibx==1.0\n",
            "Name,Version,License\nkongming-api,0.1.0,UNKNOWN\nlibx,1.0,GPL-3.0\n",
        ]
    )
    monkeypatch.setattr(licenses, "_run", _fake_run(responses))
    problems = licenses.check_python(Path())
    assert len(problems) == 1
    assert problems[0].startswith("libx 1.0")


def test_node_gate_matches_license_key_case_insensitively(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {"MIT": [{"name": "a"}], "GPL-3.0": [{"name": "libx"}]}
    monkeypatch.setattr(licenses, "_run", _fake_run(iter([json.dumps(payload)])))
    problems = licenses.check_node(Path())
    assert problems == ["GPL-3.0: libx"]


def test_pin_check_accepts_digests_and_stage_aliases(tmp_path: Path) -> None:
    dockerfile = tmp_path / "ok.Dockerfile"
    dockerfile.write_text(
        f"FROM python:3.13-slim@sha256:{_DIGEST} AS build\n"
        f"FROM build\n"
        f"FROM --platform=linux/amd64 ghcr.io/a/b:1@sha256:{_DIGEST}\n"
    )
    assert pins.check(tmp_path) == []


def test_pin_check_rejects_floating_tag(tmp_path: Path) -> None:
    dockerfile = tmp_path / "bad.Dockerfile"
    dockerfile.write_text("FROM python:3.13-slim\n")
    problems = pins.check(tmp_path)
    assert len(problems) == 1
    assert "未钉 sha256 摘要" in problems[0]


def test_link_gate_reports_missing_relative_target(tmp_path: Path) -> None:
    doc = tmp_path / "a.md"
    doc.write_text("见 [设计](../b.md) 与 [外链](https://x.test/a.md)\n", encoding="utf-8")
    broken = links.broken_links(tmp_path)
    assert [(p.name, target) for p, target in broken] == [("a.md", "../b.md")]


def test_link_gate_ignores_prose_parentheses(tmp_path: Path) -> None:
    doc = tmp_path / "a.md"
    doc.write_text("gold 列 [id, total],生成列 [id, sum](别名差异,数值全对)\n", encoding="utf-8")
    assert links.broken_links(tmp_path) == []


def test_link_gate_skips_fenced_code(tmp_path: Path) -> None:
    doc = tmp_path / "a.md"
    doc.write_text("```\n[示例](../gone.md)\n```\n", encoding="utf-8")
    assert links.broken_links(tmp_path) == []


def test_link_gate_resolves_root_relative_against_scan_root(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "x.md").write_text("# x\n", encoding="utf-8")
    doc = tmp_path / "a.md"
    doc.write_text("[ADR](/docs/x.md)\n", encoding="utf-8")
    assert links.broken_links(tmp_path) == []
    doc.write_text("[ADR](/docs/missing.md)\n", encoding="utf-8")
    assert [(p.name, target) for p, target in links.broken_links(tmp_path)] == [
        ("a.md", "/docs/missing.md")
    ]


_EVAL_SAMPLE = (
    "=== 评估统计: 宽松 8/10 (80%) | 严格 6/10 (60%) | 表命中 9/10 (90%)"
    " | 动态数据源 1/2 | prompt 指纹 p123 ===\n"
    "[judge:answer] run=r1 通过率 7/10 (70%) borderline=1 error=0\n"
    "RETRIEVAL_EVAL macro_recall@3=0.500 full_hop_rate=0.800 n=4 entity=on\n"
)


def test_redline_extracts_rates_and_fingerprint() -> None:
    metrics = redline.extract_metrics(_EVAL_SAMPLE)
    assert metrics["eval:pass"] == 0.8
    assert metrics["eval:strict"] == 0.6
    assert metrics["eval:recall"] == 0.9
    assert metrics["eval:ds"] == 0.5
    assert metrics["judge:answer"] == 0.7
    assert metrics["retrieval:macro_recall@3"] == 0.5
    assert metrics["retrieval:full_hop"] == 0.8
    assert redline.extract_fingerprint(_EVAL_SAMPLE) == "p123"


def test_redline_flags_drop_at_threshold(tmp_path: Path) -> None:
    prev = {"layer": "smoke", "fingerprint": "p1", "metrics": {"eval:pass": 0.8}}
    (tmp_path / "summary.json").write_text(json.dumps(prev), encoding="utf-8")
    cur = "=== 评估统计: 宽松 7/10 (70%) | prompt 指纹 p1 ===\n"
    note, violations = redline.compare(tmp_path, cur, "smoke")
    assert "比较" in note
    assert violations and violations[0].startswith("eval:pass")


def test_redline_skips_cross_layer_and_fingerprint(tmp_path: Path) -> None:
    prev = {"layer": "full", "fingerprint": "p1", "metrics": {"eval:pass": 0.8}}
    (tmp_path / "summary.json").write_text(json.dumps(prev), encoding="utf-8")
    cur = "=== 评估统计: 宽松 5/10 (50%) | prompt 指纹 p1 ===\n"
    note, violations = redline.compare(tmp_path, cur, "smoke")
    assert "跨层不可比" in note and violations == []
    prev["layer"] = "smoke"
    (tmp_path / "summary.json").write_text(json.dumps(prev), encoding="utf-8")
    cur_other_fp = "=== 评估统计: 宽松 5/10 (50%) | prompt 指纹 p2 ===\n"
    note, violations = redline.compare(tmp_path, cur_other_fp, "smoke")
    assert "指纹不同" in note and violations == []
