"""支付通道(035,零框架):MOCK 通道实现。

与真实通道同构:签名/验签算法收在通道内,核销主链(BillingService.
reconcile_payment)对通道零感知——未来接微信支付/支付宝=新增同
Protocol 实现+注册,主链一行不动(design D1/D6)。
mock 通道无网络 I/O:签名本地生成,演练路径与真实回调 100% 同形。
"""

import hashlib
import hmac
import uuid


class MockChannel:
    """mock 支付通道:HMAC-SHA256 签名(密钥经配置注入)。"""

    key = "MOCK"

    def __init__(self, secret: str) -> None:
        self._secret = secret.encode("utf-8")

    def new_trade_no(self) -> str:
        """通道侧交易号(mock 前缀可辨识)。"""
        return f"mock-{uuid.uuid4().hex[:16]}"

    def sign_payload(
        self, *, payment_id: str, trade_no: str, result: str, amount: int
    ) -> str:
        """回调报文签名:{payment_id}.{trade_no}.{result}.{amount}。"""
        msg = f"{payment_id}.{trade_no}.{result}.{amount}".encode()
        return hmac.new(self._secret, msg, hashlib.sha256).hexdigest()

    def verify(
        self, *, payment_id: str, trade_no: str, result: str, amount: int, sign: str
    ) -> bool:
        """验签(compare_digest 防时序侧信道,031 同款)。"""
        expected = self.sign_payload(
            payment_id=payment_id, trade_no=trade_no, result=result, amount=amount
        )
        return hmac.compare_digest(expected, sign)
