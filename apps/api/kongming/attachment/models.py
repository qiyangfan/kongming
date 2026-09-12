"""attachment 领域模型(030,零框架):类型白名单与 magic bytes 复核。

Content-Type 可伪造,magic 不可——白名单判定以文件头为准。
"""

# (mime, magic 前缀);png/jpg/webp/gif 四族
_MAGIC: dict[str, bytes] = {
    "image/png": b"\x89PNG\r\n\x1a\n",
    "image/jpeg": b"\xff\xd8\xff",
    "image/webp": b"RIFF",  # +8 位移处须为 WEBP(见 sniff_mime)
    "image/gif": b"GIF8",
}

MAX_BYTES = 10 * 1024 * 1024  # 10MB(spec)
EXT_BY_MIME = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
}


def sniff_mime(head: bytes) -> str | None:
    """文件头判定(magic bytes);不识别 → None(拒绝)。"""
    for mime, magic in _MAGIC.items():
        if head.startswith(magic):
            if mime == "image/webp" and head[8:12] != b"WEBP":
                continue
            return mime
    return None
