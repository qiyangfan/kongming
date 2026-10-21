"""prompt 资产内容指纹单测:确定性、资产覆盖、无状态、内省端点守门。

ai-eval spec「Prompt 版本指纹」:同一资产任意进程一致;任一资产字变化即换
指纹;评估产物跨指纹比对须显式标注不可比。本文件为纯函数与端点守护,
不依赖评估环境(常规门禁可跑)。
"""

from __future__ import annotations

import importlib
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from kongming_agent.graph import doc_prompt, extract_prompt, prompts
from kongming_agent.graph.react import prompts as react_prompts
from kongming_agent.main import app
from kongming_agent.prompt_fingerprint import (
    format_cross_fingerprint_note,
    prompt_fingerprint,
)

TOKEN = {"X-Internal-Token": "test-internal-token"}  # noqa: S106  # 测试令牌,非凭证

# 四份资产文件(指纹覆盖全集,不漏文件)
_ASSET_MODULE_PATHS = (
    "kongming_agent.graph.prompts",
    "kongming_agent.graph.doc_prompt",
    "kongming_agent.graph.extract_prompt",
    "kongming_agent.graph.react.prompts",
)


def _asset_constants(module: object) -> list[tuple[str, str]]:
    """模块内模板常量(公开 UPPER 字符串)。"""
    found: list[tuple[str, str]] = []
    for name, value in sorted(vars(module).items()):  # type: ignore[attr-defined]
        if name.isupper() and isinstance(value, str):
            found.append((name, value))
    return found


# ============ 1.1 确定性 ============


def test_fingerprint_stable_in_process() -> None:
    """同一进程内两次计算一致。"""
    assert prompt_fingerprint() == prompt_fingerprint()


def test_fingerprint_stable_cross_process() -> None:
    """跨进程(子解释器)再算仍一致——无随机盐/无字典序漂移。"""
    code = "from kongming_agent.prompt_fingerprint import prompt_fingerprint as f;print(f())"
    out = subprocess.check_output(  # noqa: S603
        [sys.executable, "-c", code],
        text=True,
        stderr=subprocess.STDOUT,
    )
    assert out.strip() == prompt_fingerprint()


# ============ 1.2 资产变更即换指纹 ============


@pytest.mark.parametrize("module_path", _ASSET_MODULE_PATHS)
def test_asset_file_change_alters_fingerprint(
    module_path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """任一资产文件内容变更 → 指纹变化(对四个 prompt 文件逐一验证,不漏文件)。"""
    module = importlib.import_module(module_path)
    constants = _asset_constants(module)
    assert constants, f"{module_path} 无模板常量(指纹覆盖漏文件)"
    original = prompt_fingerprint()
    name, value = constants[0]
    monkeypatch.setattr(module, name, value + "\n# 修订")
    assert prompt_fingerprint() != original, f"{module_path}.{name} 变更未换指纹"


def test_all_asset_constants_covered() -> None:
    """全集口径:每个资产模块至少一个常量进入指纹(防后续新增文件被漏)。"""
    for module_path in _ASSET_MODULE_PATHS:
        module = importlib.import_module(module_path)
        assert _asset_constants(module), f"{module_path} 模板常量为空"


# ============ 1.3 哈希无状态 ============


def test_fingerprint_restores_when_assets_restored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """资产内容还原 → 指纹还原。"""
    original = prompt_fingerprint()
    value = prompts.SQL_GEN_SYSTEM
    monkeypatch.setattr(prompts, "SQL_GEN_SYSTEM", value + "\n改")
    assert prompt_fingerprint() != original
    monkeypatch.setattr(prompts, "SQL_GEN_SYSTEM", value)
    assert prompt_fingerprint() == original


# ============ 1.4 内省端点守门(ADR-002) ============


def test_fingerprint_endpoint_requires_internal_token() -> None:
    r = TestClient(app).get("/internal/prompt-fingerprint")
    assert r.status_code == 403


def test_fingerprint_endpoint_returns_fingerprint() -> None:
    r = TestClient(app).get("/internal/prompt-fingerprint", headers=TOKEN)
    assert r.status_code == 200
    assert r.json() == {"prompt_fingerprint": prompt_fingerprint()}


# ============ 3.3 跨指纹判定输出 ============


def test_cross_version_note_with_monkeypatched_assets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """构造两个不同指纹(monkeypatch 资产)→ 输出含不可比标注与两侧指纹。"""
    baseline = prompt_fingerprint()
    value = prompts.SQL_GEN_SYSTEM
    monkeypatch.setattr(prompts, "SQL_GEN_SYSTEM", value + "\n规则 11:…")
    current = prompt_fingerprint()
    assert current != baseline
    note = format_cross_fingerprint_note(current, baseline)
    assert note is not None
    assert "跨 prompt 版本,通过率不可直接比较" in note
    assert current in note and baseline in note


def test_cross_version_note_same_fingerprint_is_silent() -> None:
    """同指纹不标注(可比时不得误报不可比)。"""
    fp = prompt_fingerprint()
    assert format_cross_fingerprint_note(fp, fp) is None


# ============ 资产模块在位(防 import 漂移) ============


def test_asset_modules_importable() -> None:
    assert prompts.SQL_GEN_SYSTEM
    assert doc_prompt.DOC_ANSWER_SYSTEM
    assert extract_prompt.EXTRACT_MEMORY_SYSTEM
    assert react_prompts.REACT_DISCIPLINE
