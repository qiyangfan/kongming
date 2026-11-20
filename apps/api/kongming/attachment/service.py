"""附件用例:上传落库+落盘、内部一次性读取。路由只做传输适配与错误映射。"""

import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from kongming.attachment.models import MAX_BYTES, sniff_mime
from kongming.attachment.repository import SqlAttachmentRepo
from kongming.auth.repository import SqlOperLogRepo
from kongming.chat.repository import ChatRepository

_CHUNK = 256 * 1024


class AttachmentTooLarge(Exception):
    """上传体超过单文件上限,调用方映射 400。"""


class AttachmentEmpty(Exception):
    """上传体为空,调用方映射 400。"""


class AttachmentTypeInvalid(Exception):
    """前 16 字节嗅探不出允许的图片类型,调用方映射 400。"""


class TenantForbidden(Exception):
    """当前用户没有可用租户(含 X-Tenant-Id 未绑定),调用方映射 403。"""


class AttachmentNotFound(Exception):
    """附件不存在/非本人/非当前租户/已消费,调用方一律映射 404。"""


@dataclass(frozen=True)
class RegisteredAttachment:
    attachment_id: str
    filename: str
    mime: str
    size_bytes: int


async def _read_limited(read: Callable[[int], Awaitable[bytes]]) -> bytes:
    """按块读满上限即弃;超限抛 AttachmentTooLarge。"""
    buf = bytearray()
    while True:
        chunk = await read(_CHUNK)
        if not chunk:
            break
        buf.extend(chunk)
        if len(buf) > MAX_BYTES:
            raise AttachmentTooLarge
    return bytes(buf)


async def _resolve_tenant(session: AsyncSession, *, user_id: str, tenant_key: str | None) -> str:
    """租户解析与 chat 同构(读 X-Tenant-Id):回退默认租户会让非默认租户下的
    上传绑定错租户,「上传→引用」随后 404。"""
    repo = ChatRepository(session)
    if tenant_key is not None:
        bound = await repo.find_bound_tenant(user_id=user_id, tenant_key=tenant_key)
        tenant = (bound, tenant_key) if bound is not None else None
    else:
        tenant = await repo.find_default_tenant(user_id=user_id)
    if tenant is None:
        raise TenantForbidden
    return str(tenant[0])


async def upload_attachment(
    session: AsyncSession,
    *,
    read: Callable[[int], Awaitable[bytes]],
    filename: str | None,
    user_id: str,
    tenant_key: str | None,
) -> RegisteredAttachment:
    """读入并校验上传体(magic 复核,Content-Type 可伪造),落库+落盘并写审计。"""
    data = await _read_limited(read)
    if not data:
        raise AttachmentEmpty
    mime = sniff_mime(data[:16])
    if mime is None:
        raise AttachmentTypeInvalid
    tenant_id = await _resolve_tenant(session, user_id=user_id, tenant_key=tenant_key)
    name = (filename or "image")[:256]
    repo = SqlAttachmentRepo(session)
    att_id = await repo.insert(
        tenant_id=tenant_id,
        user_id=user_id,
        filename=name,
        mime=mime,
        size_bytes=len(data),
    )
    await repo.write_file(att_id, mime, data)
    await SqlOperLogRepo(session).add(
        user_id,
        "attachment.upload",
        att_id,
        {"filename": name, "mime": mime, "size": len(data)},
    )
    try:
        await session.commit()
    except Exception:
        # 行回滚但文件已落盘:孤儿清理按行驱动扫不到这种 file-only 残余
        with contextlib.suppress(Exception):
            await repo.delete_file(att_id, mime)
        raise
    return RegisteredAttachment(
        attachment_id=att_id, filename=name, mime=mime, size_bytes=len(data)
    )


async def read_attachment_content(
    session: AsyncSession, *, att_id: str, user_id: str, tenant_key: str
) -> tuple[str, bytes]:
    """一次性消费:绑定复校后置 CONSUMED 并删文件,返回 (mime, 内容)。"""
    repo = SqlAttachmentRepo(session)
    tid = await ChatRepository(session).find_bound_tenant(user_id=user_id, tenant_key=tenant_key)
    if tid is None:
        raise AttachmentNotFound
    row = await repo.find_owned(att_id=att_id, tenant_id=str(tid), user_id=user_id)
    if row is None:
        raise AttachmentNotFound
    got = await repo.consume_and_read(att_id=att_id, tenant_id=str(tid))
    if got is None:
        raise AttachmentNotFound
    meta, data = got
    await session.commit()
    return str(meta["mime"]), data
