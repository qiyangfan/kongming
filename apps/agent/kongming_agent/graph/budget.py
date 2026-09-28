"""生成提示词装配预算(080-prompt-assembly-budget):字符分档估算 + 分段优先级收敛。

设计纪律(proposal What Changes 2/3/4):
- **不可切段**:系统提示纪律段与本轮问句 SHALL NOT 被截(正确性与安全性不用
  成本换);selfheal 喂回段与哨兵引导同理保留完整(自愈语义属正确性);
- **证据段**(表结构/术语/示例/few-shot/语义/记忆)按优先级从尾部丢弃,且
  **首位豁免**——schema/history 首位=最高相关命中,砍它等于自废召回;
- **估算保守、零第三方分词器**(tech-stack 锁栈;精确计数对此用途是过度工程);
- 收敛后仍超预算 → PromptBudgetExceeded(如实失败分支,可归因),
  SHALL NOT 静默删不可切段。

零框架、零 IO 纯函数模块(import-linter 契约覆盖);调用方=nodes.generate。
"""

import math
from dataclasses import dataclass, field

# 估算标定(保守方向):CJK 按 1 字符 ≈ 1 token;其余按 3.5 字符 ≈ 1 token
# (英文/代码 tokenizer 实约 4,取 3.5 偏保守——宁可多裁不可漏裁)。
_NON_CJK_CHARS_PER_TOKEN = 3.5

# 常量:收敛循环的行级安全阀(防病态输入下无限循环)
_MAX_DROP_ROUNDS = 512


class PromptBudgetExceeded(Exception):
    """不可切段本身已超预算:装配无法收敛,走可归因失败分支(不静默降质)。"""


@dataclass(frozen=True)
class Segment:
    """提示词分段。priority=0 表示不可切(纪律段/问句/selfheal 喂回);
    否则为裁剪优先级(小者先裁)。lines=True 按行尾裁(keep_head 行豁免),
    lines=False 整段丢弃。"""

    key: str
    text: str
    priority: int = 0
    lines: bool = False
    keep_head: int = 0


@dataclass(frozen=True)
class BudgetResult:
    """收敛结果:cut[key]=被裁行数(整段丢弃记 1);estimated/budget 为 token 估算。"""

    segments: list[Segment]
    cut: dict[str, int] = field(default_factory=dict[str, int])
    estimated_tokens: int = 0
    budget_tokens: int = 0


def estimate_tokens(text: str) -> int:
    """保守字符分档估算(纯函数):CJK 1 tok/char,其余 1 tok/3.5 char。"""
    if not text:
        return 0
    cjk = sum(
        1
        for ch in text
        if "\u4e00" <= ch <= "\u9fff"
        or "\u3000" <= ch <= "\u303f"  # CJK 标点(，。：；【】等)
        or "\uff00" <= ch <= "\uffef"  # 全角 ASCII 变体
    )
    other = len(text) - cjk
    return cjk + math.ceil(other / _NON_CJK_CHARS_PER_TOKEN)


def _drop_tail(text: str, keep_head: int) -> tuple[str, int]:
    """从尾部丢一行(保留头部 keep_head 行);返回 (新文本, 丢弃行数)。"""
    lines = text.split("\n")
    if len(lines) <= keep_head:
        return text, 0
    return "\n".join(lines[:-1]), 1


def converge(segments: list[Segment], budget_tokens: int) -> BudgetResult:
    """按优先级收敛分段至预算内(纯函数)。

    - 总估算 ≤ 预算:原样返回(调用方装配结果逐字不变——零回归锚);
    - 超限:按 priority 升序裁剪(行级段从尾部逐行丢、头部豁免;整段段直接丢),
      每轮重估,低于预算即停;
    - 全部可切段裁完仍超限:PromptBudgetExceeded(不可切段超预算)。
    """
    working = list(segments)
    cut: dict[str, int] = {}

    def total() -> int:
        return sum(estimate_tokens(s.text) for s in working)

    if total() <= budget_tokens:
        return BudgetResult(
            segments=working, cut=cut, estimated_tokens=total(), budget_tokens=budget_tokens
        )

    cuttable = sorted((s for s in working if s.priority > 0 and s.text), key=lambda s: s.priority)
    for seg in cuttable:
        for _ in range(_MAX_DROP_ROUNDS):
            if total() <= budget_tokens:
                break
            idx = next(i for i, s in enumerate(working) if s.key == seg.key)
            if working[idx].lines:
                new_text, dropped = _drop_tail(working[idx].text, working[idx].keep_head)
                if dropped == 0:
                    break
                working[idx] = Segment(
                    key=seg.key,
                    text=new_text,
                    priority=seg.priority,
                    lines=True,
                    keep_head=seg.keep_head,
                )
                cut[seg.key] = cut.get(seg.key, 0) + dropped
            else:
                working[idx] = Segment(
                    key=seg.key,
                    text="",
                    priority=seg.priority,
                    lines=False,
                    keep_head=0,
                )
                cut[seg.key] = cut.get(seg.key, 0) + 1
                break  # 整段丢弃后转下一优先级段
        if total() <= budget_tokens:
            break

    if total() > budget_tokens:
        # 两类封顶:不可切段(纪律/问句/自愈喂回)本身超预算,或行级段的首位
        # 豁免行(最高相关命中)单行即超预算——都属如实失败,不静默删段
        blockers = [
            s.key
            for s in working
            if (s.priority == 0 and s.text)
            or (s.lines and s.text and s.text.count("\n") + 1 <= s.keep_head)
        ]
        raise PromptBudgetExceeded(
            f"提示词装配收敛后仍超预算(估算 {total()} > {budget_tokens});"
            f"阻塞段: {blockers or ['证据段首位']}"
        )
    return BudgetResult(
        segments=working, cut=cut, estimated_tokens=total(), budget_tokens=budget_tokens
    )
