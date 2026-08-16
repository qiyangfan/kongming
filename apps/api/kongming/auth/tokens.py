"""JWT 签发与验签(ADR-003:PyJWT + RS256)。

access 15min / refresh 7d;refresh 轮换由 auth 域的 Redis 家族指纹管理,
此处只管令牌本体。
"""

from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Literal

import jwt

from kongming.config import get_settings

KeyPair = tuple[str, str]  # (private_pem, public_pem)

TokenType = Literal["access", "refresh"]

_ALG = "RS256"


@lru_cache
def _keypair() -> KeyPair:
    """密钥对进程内缓存一次(dev 兜底密钥若每次重新生成,签发与验签将使用不同密钥)。"""
    s = get_settings()
    if s.jwt_private_key_pem and s.jwt_public_key_pem:
        return s.jwt_private_key_pem, s.jwt_public_key_pem
    # dev 兜底:临时生成,重启失效(签名密钥跨重启不稳定的代价在 dev 可接受)
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    pub = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    return priv, pub


def issue(user_id: str, token_type: TokenType, extra: dict[str, object] | None = None) -> str:
    s = get_settings()
    priv, _ = _keypair()
    if token_type == "access":  # noqa: S105 —— 字面量分支标记,非密钥
        ttl = s.access_token_ttl_sec
    else:
        ttl = s.refresh_token_ttl_sec
    now = datetime.now(UTC)
    claims = {
        "iss": s.jwt_issuer,
        "sub": user_id,
        "typ": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl)).timestamp()),
        **(extra or {}),
    }
    return jwt.encode(claims, priv, algorithm=_ALG)


def verify(
    token: str,
    expected_type: TokenType = "access",  # noqa: S105 类型标记非密钥
) -> dict[str, object]:
    s = get_settings()
    _, pub = _keypair()
    claims = jwt.decode(
        token,
        pub,
        algorithms=[_ALG],
        issuer=s.jwt_issuer,
        options={"require": ["exp", "sub", "typ"]},
    )
    if claims["typ"] != expected_type:
        raise jwt.InvalidTokenError(f"expected {expected_type}, got {claims['typ']}")
    return claims
