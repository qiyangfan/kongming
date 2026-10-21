"""评估器 prompt 指纹接线:取指纹、打印、跨指纹可比性判定。

指纹算法单源在 agent `kongming_agent.prompt_fingerprint`(本模块不重写);
这里只做取数(HTTP 内省端点)、上次基线存取与输出口径。指纹不可得时
显式标注,SHALL NOT 静默跳过;跨指纹比对标注不可比,SHALL NOT 静默
套用劣化红线(ai-eval spec「Prompt 版本指纹」)。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import cast

import httpx

EVAL_DIR = Path(__file__).parent

# agent 内省端点(ADR-002:与 /internal/* 同款 X-Internal-Token 守门)
AGENT_BASE = os.getenv("KM_EVAL_AGENT_BASE", "http://127.0.0.1:8001")
_INTERNAL_TOKEN = os.getenv("KM_EVAL_INTERNAL_TOKEN") or os.getenv("KM_INTERNAL_TOKEN", "")

# 指纹不可得的显式标注(SHALL NOT 静默跳过)
FINGERPRINT_UNAVAILABLE = "指纹不可得"

# 上次基线指纹(本地运行记录;KM_EVAL_BASELINE_FINGERPRINT 显式覆盖,供 CI 注入)
_BASELINE_PATH = EVAL_DIR / ".baseline_fingerprint"


def fetch_prompt_fingerprint(timeout: float = 5.0) -> str | None:
    """取 agent prompt 资产指纹;不可达/无令牌/报文畸形 → None。

    指纹身份来自正在被评估的 agent 进程(HTTP 内省),而非本地重算——
    本地源与运行部署不一致时仍以运行侧为准。
    """
    if not _INTERNAL_TOKEN:
        return None
    try:
        resp = httpx.get(
            f"{AGENT_BASE.rstrip('/')}/internal/prompt-fingerprint",
            headers={"X-Internal-Token": _INTERNAL_TOKEN},
            timeout=timeout,
        )
        if resp.status_code != 200:
            return None
        data: object = resp.json()
    except (httpx.HTTPError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    payload = cast("dict[str, object]", data)
    fp = payload.get("prompt_fingerprint")
    return fp if isinstance(fp, str) and fp else None


def format_fingerprint_line(fp: str | None) -> str:
    """评估运行首行指纹展示(与四组分数同屏);不可得时显式标注。"""
    return f"=== prompt 指纹: {fp if fp else FINGERPRINT_UNAVAILABLE} ==="


def format_cross_fingerprint_note(current: str | None, baseline: str | None) -> str | None:
    """跨指纹判定输出:两侧皆有且不同 → 不可比标注(列两侧指纹);其余 None。"""
    if not current or not baseline or current == baseline:
        return None
    return f"跨 prompt 版本,通过率不可直接比较(本次指纹 {current} vs 基线指纹 {baseline})"


def load_baseline_fingerprint() -> str | None:
    """上次基线指纹:KM_EVAL_BASELINE_FINGERPRINT 显式优先,否则读本地记录。"""
    env_fp = os.getenv("KM_EVAL_BASELINE_FINGERPRINT", "").strip()
    if env_fp:
        return env_fp
    try:
        raw = _BASELINE_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return raw or None


def save_baseline_fingerprint(fp: str | None) -> None:
    """保存当前提示词指纹供下次基线比较；无法读取指纹时保留已有基线。"""
    if not fp:
        return
    _BASELINE_PATH.write_text(fp + "\n", encoding="utf-8")
