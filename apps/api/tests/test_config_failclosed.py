"""config prod fail-closed 回归:控制面组合残缺。

managed 缺 base URL/验签密钥/服务身份时拒启;本地 pre-shared 服务身份
只允许回环控制面地址。standalone 合法缺省。
"""

from typing import Any

import pytest

from kongming.config import Settings

_PROD_KW: dict[str, Any] = {
    "environment": "prod",
    "jwt_private_key_pem": "k",
    "jwt_public_key_pem": "k",
    "internal_token": "k",
    "datasource_encrypt_key": "k" * 32,
    # 清空外部供应商配置,避免本机 .env 改变生产环境校验结果。
    "websearch_provider": "",
    "websearch_api_key": "",
}


def _prod(**over: Any) -> Settings:
    kw: dict[str, Any] = {**_PROD_KW, **over}
    return Settings(**kw)


def test_standalone_is_valid_default() -> None:
    _prod().fail_closed_checks()  # 不抛:standalone 不连控制面


def test_managed_missing_base_url_rejected() -> None:
    s = _prod(control_mode="managed", control_signing_secret="s", control_service_token="t")
    with pytest.raises(RuntimeError, match="KM_CONTROL_BASE_URL"):
        s.fail_closed_checks()


def test_managed_missing_signing_secret_rejected() -> None:
    s = _prod(
        control_mode="managed",
        control_base_url="http://127.0.0.1:9000",
        control_service_token="t",
    )
    with pytest.raises(RuntimeError, match="KM_CONTROL_SIGNING_SECRET"):
        s.fail_closed_checks()


def test_managed_without_identity_rejected() -> None:
    s = _prod(
        control_mode="managed",
        control_base_url="http://127.0.0.1:9000",
        control_signing_secret="s",
    )
    with pytest.raises(RuntimeError, match="服务身份"):
        s.fail_closed_checks()


def test_prod_local_service_token_requires_loopback() -> None:
    s = _prod(
        control_mode="managed",
        control_base_url="https://jinguan.example",
        control_signing_secret="s",
        control_service_token="t",
    )
    with pytest.raises(RuntimeError, match="回环"):
        s.fail_closed_checks()


def test_prod_reader_token_rejected() -> None:
    s = _prod(control_reader_token="local-reader")
    with pytest.raises(RuntimeError, match="KM_CONTROL_READER_TOKEN"):
        s.fail_closed_checks()


def test_managed_keycloak_identity_valid() -> None:
    s = _prod(
        control_mode="managed",
        control_base_url="https://jinguan.example",
        control_signing_secret="s",
        control_keycloak_issuer="https://idp",
        control_keycloak_client_id="kongming-control",
        control_keycloak_client_secret="x",
    )
    s.fail_closed_checks()  # 不抛


def test_dev_default_still_allowed() -> None:
    Settings(environment="dev").fail_closed_checks()
