"""JWT 签发/验签回路测试(ADR-003)。"""

from datetime import timedelta

import jwt
import pytest

from kongming.auth import tokens


def test_issue_and_verify_roundtrip() -> None:
    token = tokens.issue("user-1", "access")
    claims = tokens.verify(token, expected_type="access")
    assert claims["sub"] == "user-1"
    assert claims["typ"] == "access"


def test_token_type_confusion_rejected() -> None:
    refresh = tokens.issue("user-1", "refresh")
    with pytest.raises(jwt.InvalidTokenError):
        tokens.verify(refresh, expected_type="access")


def test_tampered_token_rejected() -> None:
    token = tokens.issue("user-1", "access")
    with pytest.raises(jwt.PyJWTError):
        tokens.verify(token[:-4] + "aaaa", expected_type="access")


def test_access_ttl_is_short() -> None:
    from kongming.config import get_settings

    s = get_settings()
    assert s.access_token_ttl_sec <= timedelta(minutes=15).total_seconds()
