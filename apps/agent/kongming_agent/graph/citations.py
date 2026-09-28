"""068-doc-answer-citations:引用键净化(确定性、零模型调用、零凭证)。

职责(收口点与 057 numeric.audit_summarize_output 同一位置——doc_answer 尾部):
- 键形态判定(可读前缀 + `-` + 定长大写十六进制后缀);
- 两级匹配:全等于合法键保留;末段哈希后缀全等于某合法键后缀时**归一
  为该合法键**(哈希后缀是权威身份,人读前缀改写容忍——但正文与 sources
  列表必须同键,否则"正文有标注而列表无条目"的幽灵不一致);皆不命中降级
  为纯文本(剥括号、保留括号内文字,不改写句子其余部分);
- 合法键集合取自截断前的完整命中集(调用方传入;空集=整体跳过净化);
- 被剥离计数经返回值透出(调用方计入 trace)。

纯函数模块(零框架、零 IO、零凭证;import-linter 契约覆盖)。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# 引用标记:【…】全角方括号(与 DOC_ANSWER_SYSTEM 纪律段标注形态一致)
_MARK_RE = re.compile("【([^【】]*)】")
# 键后缀:4 位大写十六进制(derive_citation_key 定长哈希同宽)
_SUFFIX_RE = re.compile("^[0-9A-F]{4}$")


@dataclass(frozen=True)
class SanitizeResult:
    """净化产物:正文(降级后)+ 被剥离标记数 + 存活键集合(按出现序去重)。"""

    text: str
    stripped: int = 0
    surviving_keys: tuple[str, ...] = field(default_factory=tuple[str, ...])


def is_citation_key_shape(token: str) -> bool:
    """键形态判定(纯函数):`前缀-后缀` 且末段为 4 位大写十六进制。

    普通中括号文本(无 `-`+哈希末段)不属键形态——净化不得误伤非标记内容。
    """
    if "-" not in token:
        return False
    head, _, tail = token.rpartition("-")
    if not head or not _SUFFIX_RE.match(tail):
        return False
    return True


def _suffix_of(key: str) -> str:
    return key.rpartition("-")[2]


def sanitize_citations(text: str, legal_keys: list[str] | tuple[str, ...]) -> SanitizeResult:
    """两级匹配净化(纯函数)。

    - 全等于合法键 → 原样保留;
    - 末段哈希后缀全等于某合法键后缀(模型改写人读前缀)→ **归一为该
      合法键**(权威形态;chat spec:正文与 sources 双向无幽灵);
    - 皆不命中 → 降级为纯文本(剥掉【】、保留括号内文字);
    - legal_keys 为空(闲聊/未覆盖/降级路径)→ 整体原样返回(零标记误伤)。
    """
    if not legal_keys or not text:
        return SanitizeResult(text=text, stripped=0, surviving_keys=())
    legal = set(legal_keys)
    by_suffix = {_suffix_of(k): k for k in legal_keys}
    stripped = 0
    surviving: list[str] = []

    def _replace(m: re.Match[str]) -> str:
        nonlocal stripped
        token = m.group(1).strip()
        if token in legal:
            if token not in surviving:
                surviving.append(token)
            return m.group(0)  # 全等:原样保留
        if is_citation_key_shape(token):
            canonical = by_suffix.get(_suffix_of(token))
            if canonical is not None:
                # 后缀命中:正文归一为权威键(surviving 同键——sources 投影
                # 由 by_key 精取,杜绝"正文有标注、列表无条目")
                if canonical not in surviving:
                    surviving.append(canonical)
                return f"【{canonical}】"
            # 键形态但未命中(编造键):降级为纯文本
            stripped += 1
            return token
        # 非键形态的中括号文本(普通内容):原样保留,不误伤(任务 1.4)
        return m.group(0)

    return SanitizeResult(
        text=_MARK_RE.sub(_replace, text),
        stripped=stripped,
        surviving_keys=tuple(surviving),
    )


def surviving_markers(text: str) -> list[str]:
    """正文中的存活标记(按出现序去重)——sources 投影的输入(纯函数)。"""
    out: list[str] = []
    for m in _MARK_RE.finditer(text):
        token = m.group(1).strip()
        if token not in out:
            out.append(token)
    return out
