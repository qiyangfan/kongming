"""billing 领域模型(034,零框架):配额判定纯函数(全部网关判定
收于此单点)。

三维:TOKENS(token 数)/CALLS(调用次数)/COST(成本,厘)。
金额一律整数厘(禁浮点——浮点金额是累计误差源)。

035:支付单状态机(纯函数)——CREATED → PAID/CANCELLED/EXPIRED
三终态;转换合法性判定收于此,服务层只负责 CAS 落库。
"""

from dataclasses import dataclass
from typing import Literal

Dimension = Literal["TOKENS", "CALLS", "COST"]

PaymentStatus = Literal["CREATED", "PAID", "CANCELLED", "EXPIRED"]

_TERMINAL: frozenset[str] = frozenset({"PAID", "CANCELLED", "EXPIRED"})


def is_terminal_payment(status: str) -> bool:
    """终态判定:终态单据不再接受任何转换(CAS 前的纯函数预判)。"""
    return status in _TERMINAL


def can_pay(status: str) -> bool:
    """可核销(转 PAID)判定:仅在途 CREATED 单可支付。"""
    return status == "CREATED"


def can_create_payment(invoice_status: str) -> bool:
    """可对账单发起支付判定:仅 PENDING 账单(PAID/VOID 拒)。"""
    return invoice_status == "PENDING"


@dataclass(frozen=True)
class QuotaVerdict:
    """配额判定结果:allowed=False 即超限(429 语义);warn=True 达预警线。"""

    allowed: bool
    warn: bool
    dimension: str  # 超限维度(未超为 "")
    used: int
    limit: int


def check_quota(
    *, used: int, limit: int, warn_pct: int, dimension: Dimension
) -> QuotaVerdict:
    """单维判定(纯函数):limit<=0 视为不限(放行无预警)。"""
    if limit <= 0:
        return QuotaVerdict(allowed=True, warn=False, dimension="", used=used, limit=0)
    if used >= limit:
        return QuotaVerdict(
            allowed=False, warn=True, dimension=dimension, used=used, limit=limit
        )
    warn = used * 100 >= limit * warn_pct
    return QuotaVerdict(
        allowed=True, warn=warn, dimension=dimension if warn else "", used=used, limit=limit
    )


def estimate_cost(
    *, tokens: int, calls: int, price_per_mtok: int, price_per_call: int
) -> int:
    """成本估算(整数厘):token/1000×单价 + 次数×单价(向下取整)。"""
    return (tokens * price_per_mtok) // 1000 + calls * price_per_call
