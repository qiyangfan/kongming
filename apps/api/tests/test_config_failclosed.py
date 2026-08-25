"""config prod fail-closed 回归(审查循环 S1):mock 支付密钥闸。

prod + mock 开启 + 密钥缺省/占位 → 拒启(否则默认公开密钥可伪造 PAID 回调)。
"""

import pytest

from kongming.config import Settings

_PROD_KW = {
    "environment": "prod",
    "jwt_private_key_pem": "k",
    "jwt_public_key_pem": "k",
    "internal_token": "k",
    "datasource_encrypt_key": "k" * 32,
}


def test_prod_mock_secret_dev_placeholder_rejected() -> None:
    s = Settings(**{**_PROD_KW, "billing_mock_secret": "dev-mock-payment-secret"})
    with pytest.raises(RuntimeError, match="KM_BILLING_MOCK_SECRET"):
        s.fail_closed_checks()


def test_prod_mock_secret_explicit_passes() -> None:
    s = Settings(**{**_PROD_KW, "billing_mock_secret": "prod-real-secret"})
    s.fail_closed_checks()  # 不抛


def test_dev_default_still_allowed() -> None:
    Settings(environment="dev").fail_closed_checks()
