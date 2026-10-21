"""prompt 资产内容指纹。

对生效的 prompt 模板常量取 SHA-256 前 8 位:任一字符变化即换指纹,
内容不变则跨进程一致,不含人工维护的序号或随机分量。纯 stdlib、无 IO,
成本为几次字符串拼接和一次 SHA-256,无需缓存。QueryOut.prompt_version
直接采用本指纹(对齐 VARCHAR(8)),禁止另写一份算法。
"""

from __future__ import annotations

import hashlib
from types import ModuleType
from typing import Final

from kongming_agent.graph import doc_prompt, extract_prompt, prompts
from kongming_agent.graph.react import prompts as react_prompts

# 资产模块固定序(四文件全集;新增资产模块须同步本元组与单测)
_ASSET_MODULES: Final[tuple[ModuleType, ...]] = (
    prompts,
    doc_prompt,
    extract_prompt,
    react_prompts,
)

# 短指纹长度(prompt_version VARCHAR(8) 同宽)
_FINGERPRINT_LEN: Final = 8


def _module_asset_text(module: ModuleType) -> str:
    """模块内模板常量(公开 UPPER 字符串)按名称序拼接。"""
    names = sorted(
        name
        for name, value in vars(module).items()
        if name.isupper() and isinstance(value, str) and not name.startswith("_")
    )
    return "\x1e".join(f"{name}={getattr(module, name)}" for name in names)


def prompt_fingerprint() -> str:
    """四份 prompt 资产按固定序拼接 → SHA-256 前 8 位十六进制。"""
    blob = "\x1f".join(_module_asset_text(module) for module in _ASSET_MODULES)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:_FINGERPRINT_LEN]


def format_cross_fingerprint_note(current: str, baseline: str) -> str | None:
    """跨指纹比对标注(评估红线口径):不同 → 不可比说明(列两侧指纹);
    相同 → None(可比时不得误报)。SHALL NOT 静默套用劣化红线。"""
    if current == baseline:
        return None
    return f"跨 prompt 版本,通过率不可直接比较(本次指纹 {current} vs 基线指纹 {baseline})"
