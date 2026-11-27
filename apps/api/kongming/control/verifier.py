"""锦官快照验签：RSA-SHA256 PKCS#1 v1.5，签名覆盖原始响应体；公钥来自 PEM 或配置。"""

import base64
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from kongming.control.models import SnapshotRejected


def load_public_key(path: str) -> rsa.RSAPublicKey:
    """读取 PEM 公钥；非 RSA 或不可解析即拒绝启动。"""
    try:
        raw = Path(path).read_bytes()
    except OSError as e:
        raise SnapshotRejected(f"快照验签公钥不可读: {path}") from e
    return load_public_key_pem(raw)


def load_public_key_pem(raw: str | bytes) -> rsa.RSAPublicKey:
    """解析 PEM 公钥；非 RSA 或不可解析即拒绝启动。"""
    data = raw.encode() if isinstance(raw, str) else raw
    try:
        key = serialization.load_pem_public_key(data)
    except (ValueError, TypeError) as e:
        raise SnapshotRejected("快照验签公钥 PEM 非法") from e
    if not isinstance(key, rsa.RSAPublicKey):
        raise SnapshotRejected("快照验签公钥必须是 RSA")
    return key


def verify_snapshot(public_key: rsa.RSAPublicKey, body: bytes, signature: str) -> None:
    """校验裸签名；缺签名、base64 非法或验签失败一律 SnapshotRejected。"""
    if not signature.strip():
        raise SnapshotRejected("快照缺少签名")
    try:
        raw = base64.b64decode(signature, validate=True)
    except ValueError:
        try:
            raw = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
        except ValueError as e:
            raise SnapshotRejected("快照签名非 base64") from e
    try:
        public_key.verify(raw, body, padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature as e:
        raise SnapshotRejected("快照签名校验失败") from e
