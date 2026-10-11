"""config prod fail-closed 回归(审查循环 S1):mock 支付密钥闸。

prod + mock 开启 + 密钥缺省/占位 → 拒启(否则默认公开密钥可伪造 PAID 回调)。
"""

from typing import Any, cast

import pytest

from kongming.config import Settings

# 值全为 str,但 Settings(pydantic)会按字段类型做 env 风格强转:
# 合并后的 kwargs cast 回全 Any 值域,免得 ** 展开被逐参数按 str 校验误报
_PROD_KW: dict[str, Any] = {
    "environment": "prod",
    "jwt_private_key_pem": "k",
    "jwt_public_key_pem": "k",
    "internal_token": "k",
    "datasource_encrypt_key": "k" * 32,
    # 094:websearch 组合残缺(供应商缺 key 等)在 prod 拒启——钉空隔离本地
    # .env 的 KM_WEBSEARCH_*,防开发者本机配置把既有用例打红(评审循环登记)
    "websearch_provider": "",
    "websearch_api_key": "",
    # 100:ticket 组合残缺(端点缺凭证等)同口径 prod 拒启——同款钉空隔离
    "ticket_base_url": "",
    "ticket_api_key": "",
}


def test_prod_mock_secret_dev_placeholder_rejected() -> None:
    s = Settings(
        **cast("dict[str, Any]", {**_PROD_KW, "billing_mock_secret": "dev-mock-payment-secret"})
    )
    with pytest.raises(RuntimeError, match="KM_BILLING_MOCK_SECRET"):
        s.fail_closed_checks()


def test_prod_mock_secret_explicit_passes() -> None:
    s = Settings(**cast("dict[str, Any]", {**_PROD_KW, "billing_mock_secret": "prod-real-secret"}))
    s.fail_closed_checks()  # 不抛


def test_dev_default_still_allowed() -> None:
    Settings(environment="dev").fail_closed_checks()
