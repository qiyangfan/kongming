"""config prod fail-closed 回归:mock 支付密钥闸。

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
    # 清空 websearch 配置,避免本机 .env 改变生产环境校验结果。
    "websearch_provider": "",
    "websearch_api_key": "",
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
