"""数据源凭证使用 AES-GCM 加解密。密文为 Base64 编码的 12 字节 nonce 与密文、16 字节认证标签。

生产环境必须配置 `KM_DATASOURCE_ENCRYPT_KEY`（Base64 编码的 16 或 32 字节密钥）；
开发环境缺省使用固定的开发密钥并记录告警。"""

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
                raise ValueError("KM_DATASOURCE_ENCRYPT_KEY 解码后须为 16/32 字节(AES-128/256)")
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
