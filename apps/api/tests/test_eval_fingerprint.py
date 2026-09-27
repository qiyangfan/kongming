"""评估器 prompt 指纹接线单测(073,默认门禁):输出口径与取数降级。

不依赖评估环境;指纹算法本身由 agent 侧 test_prompt_fingerprint 守护
(含 monkeypatch 资产构造两枚不同指纹),本文件只测评估输出与基线存取。
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

import evals.prompt_fingerprint as fpmod
from evals.prompt_fingerprint import (
    FINGERPRINT_UNAVAILABLE,
    fetch_prompt_fingerprint,
    format_cross_fingerprint_note,
    format_fingerprint_line,
    load_baseline_fingerprint,
    save_baseline_fingerprint,
)

# ============ 输出口径 ============


def test_fingerprint_line_shows_value() -> None:
    assert format_fingerprint_line("deadbeef") == "=== prompt 指纹: deadbeef ==="


def test_fingerprint_line_unavailable_is_explicit() -> None:
    line = format_fingerprint_line(None)
    assert FINGERPRINT_UNAVAILABLE in line
    assert "prompt 指纹" in line


def test_cross_note_two_different_fingerprints() -> None:
    """构造两个不同指纹 → 输出含不可比标注与两侧指纹(3.3)。"""
    note = format_cross_fingerprint_note("deadbeef", "cafebabe")
    assert note is not None
    assert "跨 prompt 版本,通过率不可直接比较" in note
    assert "deadbeef" in note and "cafebabe" in note


def test_cross_note_same_fingerprint_silent() -> None:
    assert format_cross_fingerprint_note("deadbeef", "deadbeef") is None


def test_cross_note_missing_side_silent() -> None:
    """任一侧不可得:不误报不可比(也不得静默判劣化)。"""
    assert format_cross_fingerprint_note(None, "deadbeef") is None
    assert format_cross_fingerprint_note("deadbeef", None) is None
    assert format_cross_fingerprint_note(None, None) is None


# ============ 取数降级 ============


def test_fetch_returns_none_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fpmod, "_INTERNAL_TOKEN", "")
    assert fetch_prompt_fingerprint() is None


def test_fetch_returns_none_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_a: object, **_kw: object) -> object:
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(fpmod, "_INTERNAL_TOKEN", "tok")
    monkeypatch.setattr(fpmod.httpx, "get", _boom)
    assert fetch_prompt_fingerprint() is None


def test_fetch_returns_none_on_bad_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Resp:
        status_code = 200

        def json(self) -> list[str]:
            return ["not-a-dict"]

    def _get(*_a: object, **_kw: object) -> _Resp:
        return _Resp()

    monkeypatch.setattr(fpmod, "_INTERNAL_TOKEN", "tok")
    monkeypatch.setattr(fpmod.httpx, "get", _get)
    assert fetch_prompt_fingerprint() is None


def test_fetch_returns_fingerprint_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Resp:
        status_code = 200

        def json(self) -> dict[str, str]:
            return {"prompt_fingerprint": "abcd1234"}

    def _get(*_a: object, **_kw: object) -> _Resp:
        return _Resp()

    monkeypatch.setattr(fpmod, "_INTERNAL_TOKEN", "tok")
    monkeypatch.setattr(fpmod.httpx, "get", _get)
    assert fetch_prompt_fingerprint() == "abcd1234"


# ============ 基线存取 ============


def test_baseline_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fpmod, "_BASELINE_PATH", tmp_path / "fp.txt")
    monkeypatch.delenv("KM_EVAL_BASELINE_FINGERPRINT", raising=False)
    assert load_baseline_fingerprint() is None
    save_baseline_fingerprint("aaaa1111")
    assert load_baseline_fingerprint() == "aaaa1111"


def test_baseline_env_overrides_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fpmod, "_BASELINE_PATH", tmp_path / "fp.txt")
    monkeypatch.delenv("KM_EVAL_BASELINE_FINGERPRINT", raising=False)
    save_baseline_fingerprint("aaaa1111")
    monkeypatch.setenv("KM_EVAL_BASELINE_FINGERPRINT", "bbbb2222")
    assert load_baseline_fingerprint() == "bbbb2222"


def test_baseline_skip_save_when_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fpmod, "_BASELINE_PATH", tmp_path / "fp.txt")
    monkeypatch.delenv("KM_EVAL_BASELINE_FINGERPRINT", raising=False)
    save_baseline_fingerprint("aaaa1111")
    save_baseline_fingerprint(None)  # 不可得时不清除既有基线
    assert load_baseline_fingerprint() == "aaaa1111"
