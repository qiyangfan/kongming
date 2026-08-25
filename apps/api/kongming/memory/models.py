"""memory 领域模型(022):记忆实体 + 显式意图提取 + 敏感检测(零框架)。

spec: agent-memory「记忆形成(显式+隐式)」——PII/凭证命中拒绝落库
(复用 020 护栏规则集,不另立规则)。
"""

import re
from dataclasses import dataclass
from datetime import datetime

from kongming.guardrail.rules import REGISTRY as GUARDRAIL_REGISTRY

_CONTENT_MAX = 500  # 记忆内容截断

# 显式意图词(句首锚定):「记住/请记住/帮我记住/记一下」+ 可选冒号
_EXPLICIT_PATTERN = re.compile(r"^(?:请记住|记住|帮我记住|记一下)[:：,，]?\s*(.+)$", re.S)

# 敏感检测=020 输入护栏全部规则(R1-H2:injection blocked 类一并纳入——
# 记忆会跨会话注入 prompt,形成期是全链路唯一闸;召回注入不再过护栏)+
# 记忆域特有的密码语境规则(「密码是 X」形态;不进护栏注册表——正常问答
# 的密码字段提及不误伤)
_SENSITIVE_PATTERNS = [
    r.pattern for r in GUARDRAIL_REGISTRY if r.guardrail_type == "input" and r.pattern
] + [
    r"(密码|口令|password|passwd)\s*(是|为|:|：)\s*\S+",
]


@dataclass(frozen=True)
class MemoryEntry:
    id: str
    user_id: str
    tenant_id: str
    content: str
    category: str
    confidence: float
    source: str  # EXPLICIT / IMPLICIT
    dormant: bool
    last_hit_at: datetime | None = None
    created_at: datetime | None = None


def extract_explicit_intent(question: str) -> str | None:
    """显式记忆意图提取(纯函数):句首意图词+内容;无意图返回 None。"""
    m = _EXPLICIT_PATTERN.match(question.strip())
    if not m:
        return None
    content = m.group(1).strip()
    return content[:_CONTENT_MAX] if content else None


def contains_sensitive(text: str) -> bool:
    """PII/凭证形态检测(复用 020 规则正则;IGNORECASE 与 020 R1-H1 同口径)。"""
    return any(re.search(p, text, re.IGNORECASE) for p in _SENSITIVE_PATTERNS)
