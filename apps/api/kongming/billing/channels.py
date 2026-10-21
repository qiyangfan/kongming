"""本地模拟支付通道。签名和验签在通道内完成，不发起网络请求。"""

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

    def sign_payload(self, *, payment_id: str, trade_no: str, result: str, amount: int) -> str:
        """回调报文签名:{payment_id}.{trade_no}.{result}.{amount}。"""
        msg = f"{payment_id}.{trade_no}.{result}.{amount}".encode()
        return hmac.new(self._secret, msg, hashlib.sha256).hexdigest()

    def verify(
        self, *, payment_id: str, trade_no: str, result: str, amount: int, sign: str
    ) -> bool:
        """验签(compare_digest 防时序侧信道,同款)。"""
        expected = self.sign_payload(
            payment_id=payment_id, trade_no=trade_no, result=result, amount=amount
        )
        return hmac.compare_digest(expected, sign)
