"""RFC 9562 UUID v7 生成器(纯函数模块,零框架依赖)。

原住 db.py;起域 service 层需要生成主键又受 import-linter
「service 不 import sqlalchemy(含间接)」约束,拆为独立纯模块,
db.py re-export 维持既有 import 路径不变。
"""

import os
import time
import uuid


def uuid7() -> uuid.UUID:
    """毫秒时间戳前缀,单调友好(索引局部性优于 v4)。"""
    ts_ms = time.time_ns() // 1_000_000
    rand_a = int.from_bytes(os.urandom(2), "big") & 0x0FFF
    rand_b = int.from_bytes(os.urandom(8), "big") & 0x3FFFFFFFFFFFFFFF
    value = ((ts_ms & 0xFFFFFFFFFFFF) << 80) | (0x7 << 76) | (rand_a << 64) | (0x2 << 62) | rand_b
    return uuid.UUID(int=value)
