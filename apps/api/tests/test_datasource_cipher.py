"""数据源凭证 AES-GCM 加解密(008 design D2,安全规则类测试先行)。

格式 base64(iv[12B] ‖ ciphertext+tag) 与上游 CredentialEncryptor 逐字节同构;
dev 缺省降级=固定 dev-only 密钥走真 round-trip(优于上游明文直通)。
"""

import base64
import os

import pytest

from kongming.datasource.cipher import AesGcmCipher, get_cipher
from kongming.datasource.models import CredentialUndecryptable

_KEY_16 = base64.b64encode(os.urandom(16)).decode()
_KEY_32 = base64.b64encode(os.urandom(32)).decode()


def test_roundtrip_16_and_32_byte_keys() -> None:
    for key in (_KEY_16, _KEY_32):
        cipher = AesGcmCipher(key)
        enc = cipher.encrypt("s3cret-密码")
        assert enc != "s3cret-密码"
        assert cipher.decrypt(enc) == "s3cret-密码"


def test_same_plaintext_different_ciphertext() -> None:
    """IV 随机:同明文两次加密密文不同(防重放/防等值比对)。"""
    cipher = AesGcmCipher(_KEY_32)
    assert cipher.encrypt("pw") != cipher.encrypt("pw")


def test_ciphertext_layout_iv_prefix() -> None:
    """密文布局:base64 解码后前 12 字节为 IV,总长 = 12 + len(plain) + 16(tag)。"""
    cipher = AesGcmCipher(_KEY_32)
    raw = base64.b64decode(cipher.encrypt("abcd"))
    assert len(raw) == 12 + 4 + 16


def test_dev_fallback_when_key_missing() -> None:
    """密钥缺省 → 固定 dev-only 密钥降级,仍走真 AES-GCM round-trip(构造告警)。"""
    cipher = AesGcmCipher(None)
    enc = cipher.encrypt("pw")
    assert enc != "pw"  # 非明文直通(上游曾明文降级,本变更收紧)
    assert cipher.decrypt(enc) == "pw"
    # 同进程同缺省密钥可互解(dev 环境间可重放,既定取舍)
    assert AesGcmCipher("").decrypt(enc) == "pw"


def test_invalid_key_length_rejected() -> None:
    with pytest.raises(ValueError, match="16/32"):
        AesGcmCipher(base64.b64encode(b"too-short-8").decode())


def test_invalid_key_base64_rejected() -> None:
    with pytest.raises(ValueError):
        AesGcmCipher("!!!not-base64!!!")


def test_bad_ciphertext_rejected() -> None:
    cipher = AesGcmCipher(_KEY_32)
    with pytest.raises(CredentialUndecryptable):
        cipher.decrypt("!!!not-base64!!!")
    # 篡改密文(GCM tag 校验失败)
    raw = bytearray(base64.b64decode(cipher.encrypt("pw")))
    raw[-1] ^= 0xFF
    with pytest.raises(CredentialUndecryptable):
        cipher.decrypt(base64.b64encode(bytes(raw)).decode())
    # 异密钥不可解(密钥轮换场景)
    with pytest.raises(CredentialUndecryptable):
        AesGcmCipher(_KEY_16).decrypt(cipher.encrypt("pw"))


def test_get_cipher_cached_by_key() -> None:
    assert get_cipher(_KEY_32) is get_cipher(_KEY_32)
    assert get_cipher(None) is get_cipher(None)
    # None 与 "" 均为 dev 降级(不同缓存键各自实例化,语义等价)
    assert get_cipher("").decrypt(get_cipher(None).encrypt("pw")) == "pw"


def test_lifespan_rejects_invalid_encrypt_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """非法 KM_DATASOURCE_ENCRYPT_KEY 启动期即拒(R1-S3/R2-N3:lifespan 试构造接线)。

    cipher 试构造在 lifespan 的 yield 之前,异常即拒启(不触碰 DB/不跑 seed,
    teardown 的池清理也不会执行,无跨 loop 锁绑定面)。
    """
    from fastapi.testclient import TestClient

    from kongming.config import get_settings
    from kongming.main import app

    monkeypatch.setattr(get_settings(), "datasource_encrypt_key", "!!!not-base64!!!")
    with pytest.raises(ValueError, match="base64"), TestClient(app):
        pass
