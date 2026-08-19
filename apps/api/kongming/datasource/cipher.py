"""数据源凭证 AES-GCM 加解密(infrastructure 层;008 design D2)。

密文格式 base64(iv[12B] ‖ ciphertext+tag[16B]),与上游 CredentialEncryptor
逐字节同构(逻辑参考,非代码平移);GCM 自带完整性校验,篡改即解密失败。
密钥:KM_DATASOURCE_ENCRYPT_KEY(base64 16/32 字节,AES-128/256-GCM);
prod 缺配启动 fail-closed(config 已在)。dev 缺省降级为代码内固定 dev-only
密钥(构造告警一次),仍走真 AES-GCM round-trip——优于上游 dev 明文直通;
dev 密文可被本地重放属既定取舍(prod 强制真密钥,无安全风险面)。
"""

import base64
import binascii
import logging
import os
from functools import lru_cache

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from kongming.datasource.models import CredentialUndecryptable

log = logging.getLogger(__name__)

_IV_LEN = 12  # GCM 推荐 12 字节(与上游一致)

# dev-only 固定密钥(16 字节 AES-128):密钥缺省时的降级,密文可跨 dev 环境重放;
# prod 由 config fail-closed 强制真密钥,本常量永不出现在 prod 路径
_DEV_ONLY_KEY = b"kongming-dev-ds!"


class AesGcmCipher:
    """ports.CredentialCipher 的 AES-GCM 实现;密钥非法即构造失败(fail-closed)。"""

    def __init__(self, key_b64: str | None) -> None:
        if not key_b64:
            log.warning(
                "KM_DATASOURCE_ENCRYPT_KEY 未配置,数据源凭证加密降级为 dev-only 固定密钥"
                "(仅 dev 可接受,prod 启动已 fail-closed)"
            )
            key_bytes = _DEV_ONLY_KEY
        else:
            try:
                key_bytes = base64.b64decode(key_b64, validate=True)
            except binascii.Error as e:
                raise ValueError("KM_DATASOURCE_ENCRYPT_KEY 须为 base64") from e
            if len(key_bytes) not in (16, 32):
                raise ValueError(
                    "KM_DATASOURCE_ENCRYPT_KEY 解码后须为 16/32 字节(AES-128/256)"
                )
        self._aes = AESGCM(key_bytes)

    def encrypt(self, plain: str) -> str:
        iv = os.urandom(_IV_LEN)  # 每次随机:同明文不同密文,防重放/等值比对
        return base64.b64encode(iv + self._aes.encrypt(iv, plain.encode(), None)).decode()

    def decrypt(self, encoded: str) -> str:
        try:
            raw = base64.b64decode(encoded, validate=True)
            return self._aes.decrypt(raw[:_IV_LEN], raw[_IV_LEN:], None).decode()
        except (ValueError, InvalidTag) as e:
            # base64 非法/密文截断(ValueError)、GCM tag 校验失败(InvalidTag)、
            # UTF-8 解码失败(UnicodeDecodeError,ValueError 子类)——统一归「不可解」
            raise CredentialUndecryptable("凭证密文不可解(密钥轮换或密文损坏)") from e


@lru_cache(maxsize=4)
def get_cipher(key_b64: str | None) -> AesGcmCipher:
    """进程级缓存(密钥值即键):构造告警每进程一次,不随请求刷屏。"""
    return AesGcmCipher(key_b64)
