"""guardrail 领域模型(020):护栏判定与脱敏纯函数(零框架,import-linter 守护)。

spec: prompt-guardrails「输入护栏」「输出护栏」「工具调用前意图审核」
「护栏 fail-closed」。判定全确定性(正则/关键词/特征片段),无 LLM 调用。
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

GuardrailType = Literal["input", "output", "tool_intent"]
GuardrailAction = Literal["blocked", "masked"]

_CONTENT_DIGEST_MAX = 500  # 审计摘要截断上限


@dataclass(frozen=True)
class GuardrailRule:
    """护栏规则定义(代码注册表项);配置表只覆盖 enabled/action/threshold。"""

    name: str
    guardrail_type: GuardrailType
    action: GuardrailAction  # 缺省动作
    description: str
    pattern: str = ""  # 正则(input/output 文本类)
    substrings: tuple[str, ...] = ()  # 特征片段集(任一命中即算;输出泄漏检测用)
    enabled: bool = True
    threshold: int | None = None  # tool_intent 类(trace 内写工具计数阈值)


@dataclass(frozen=True)
class GuardrailHit:
    """单次规则命中。masked 时 masked_text 为处理后文本。"""

    rule_name: str
    action: GuardrailAction
    digest: str  # 审计摘要(脱敏后,截断)
    masked_text: str = ""


# ── 脱敏纯函数(值域掩码;原文不进 LLM、不落审计) ──


def mask_phone(text: str) -> str:
    return re.sub(r"(?<!\d)(1[3-9]\d)(\d{4})(\d{4})(?!\d)", r"\1****\3", text)


def mask_idcard(text: str) -> str:
    return re.sub(r"(?<!\d)(\d{6})\d{8,9}([\dXx]{4})(?!\d)", r"\1********\2", text)


def mask_bankcard(text: str) -> str:
    # 16-19 位连续数字,前后需非数字边界;排除 19/20 前缀(时间戳形态)
    return re.sub(r"(?<!\d)(?!19\d|20\d)\d{16,19}(?!\d)", "****", text)


def mask_email(text: str) -> str:
    return re.sub(r"[\w.+-]+(@[\w-]+\.[\w.]+)", r"***\1", text)


def mask_apikey(text: str) -> str:
    return re.sub(r"sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}", "sk-****", text)


# 规则名 → 脱敏函数(注册表 pattern 命中后的处理)
MASKERS: dict[str, Callable[[str], str]] = {
    "pii_phone": mask_phone,
    "pii_idcard": mask_idcard,
    "pii_bankcard": mask_bankcard,
    "pii_email": mask_email,
    "cred_apikey": mask_apikey,
    "out_cred_apikey": mask_apikey,  # 输出凭证泄漏同款脱敏
}


@dataclass(frozen=True)
class RuleConfig:
    """配置合并视图(注册表缺省 + 库覆盖)的单规则生效形态。"""

    rule: GuardrailRule
    enabled: bool
    action: GuardrailAction
    threshold: int | None


def evaluate_text(
    text: str, configs: list[RuleConfig], gtype: GuardrailType
) -> tuple[str, list[GuardrailHit]]:
    """文本护栏判定(纯函数):masked 规则改文本,blocked 规则收集命中。

    返回 (最终文本, 命中集);调用方按命中集落审计,有 blocked 即拦截。
    规则按注册表顺序应用(masked 先改后判后续规则,次序即注册表次序)。
    R1-H1:统一 re.IGNORECASE(英文 injection 首字母大写为常态;中文规则无
    大小写概念,不受影响)。
    R1-M2:审计 digest 生成前对文本过全量 MASKERS——审计永不落 PII 原文
    (含 blocked 命中时、多 PII 混合时前序 digest 带后序未处理值两形态)。
    """
    hits: list[GuardrailHit] = []
    current = text
    for cfg in configs:
        if not cfg.enabled or cfg.rule.guardrail_type != gtype:
            continue
        matched = bool(
            cfg.rule.pattern and re.search(cfg.rule.pattern, current, re.IGNORECASE)
        ) or any(s in current for s in cfg.rule.substrings)
        if not matched:
            continue
        if cfg.action == "masked" and cfg.rule.name in MASKERS:
            masked = MASKERS[cfg.rule.name](current)
            hits.append(
                GuardrailHit(
                    rule_name=cfg.rule.name,
                    action="masked",
                    digest=f"{gtype} 脱敏: {_mask_all(masked)[:_CONTENT_DIGEST_MAX]}",
                    masked_text=masked,
                )
            )
            current = masked
        else:
            hits.append(
                GuardrailHit(
                    rule_name=cfg.rule.name,
                    action="blocked",
                    digest=f"{gtype} 命中拦截: {_mask_all(current)[:_CONTENT_DIGEST_MAX]}",
                )
            )
    return current, hits


def _mask_all(text: str) -> str:
    """审计摘要兜底脱敏:全部 masker 过一遍(固定顺序,idcard 先于 bankcard——
    18 位带 X 必身份证;不依赖规则启用态,审计永不落 PII 原文)。"""
    for fn in (mask_idcard, mask_phone, mask_bankcard, mask_email, mask_apikey):
        text = fn(text)
    return text
