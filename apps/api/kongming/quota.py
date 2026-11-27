"""配额判定纯函数(零框架):chat 成本闸与网关配额共用。

billing 控制面移除后,配额判定仍需要一套确定性口径;本模块只保留
TOKENS/CALLS/COST 三维的纯函数,不持有任何计划/账单/支付状态。
"""

from dataclasses import dataclass
from typing import Literal

Dimension = Literal["TOKENS", "CALLS", "COST"]


@dataclass(frozen=True)
class QuotaVerdict:
    """配额判定结果:allowed=False 即超限(429 语义);warn=True 达预警线。"""

    allowed: bool
    warn: bool
    dimension: str  # 超限维度(未超为 "")
    used: int
    limit: int


def check_quota(*, used: int, limit: int, warn_pct: int, dimension: Dimension) -> QuotaVerdict:
    """单维判定(纯函数):limit<=0 视为不限(放行无预警)。"""
    if limit <= 0:
        return QuotaVerdict(allowed=True, warn=False, dimension="", used=used, limit=0)
    if used >= limit:
        return QuotaVerdict(allowed=False, warn=True, dimension=dimension, used=used, limit=limit)
    warn = used * 100 >= limit * warn_pct
    return QuotaVerdict(
        allowed=True, warn=warn, dimension=dimension if warn else "", used=used, limit=limit
    )


def estimate_cost(*, tokens: int, calls: int, price_per_mtok: int, price_per_call: int) -> int:
    """成本估算(整数厘):token/1000×单价 + 次数×单价(向下取整)。"""
    return (tokens * price_per_mtok) // 1000 + calls * price_per_call
