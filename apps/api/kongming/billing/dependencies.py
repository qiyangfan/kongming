"""billing 组合根:服务装配(端点内直调,非 Depends 键)与支付通道注册表。"""

from sqlalchemy.ext.asyncio import AsyncSession

from kongming.billing.adapters import BillingOper
from kongming.billing.channels import MockChannel
from kongming.billing.ports import PaymentChannel
from kongming.billing.repository import SqlBillingRepo
from kongming.billing.service import BillingService
from kongming.config import get_settings


def _channels() -> dict[str, PaymentChannel]:
    """通道注册表:mock 通道可经环境开关整体关闭。"""
    st = get_settings()
    if not st.billing_mock_enabled:
        return {}
    return {MockChannel.key: MockChannel(st.billing_mock_secret)}


def get_billing_service(session: AsyncSession, actor: str | None = None) -> BillingService:
    # 审计器恒创建:callback 等无登录态场景 actor=None 落库即匿名
    st = get_settings()
    return BillingService(
        SqlBillingRepo(session),
        BillingOper(session, actor),
        channels=_channels(),
        payment_ttl_minutes=st.billing_payment_ttl_minutes,
    )
