"""attachment 路由(030,薄适配层):
- POST /chat/attachments(用户面,chat:execute):流式读+大小闸+magic
  复核+落盘+元数据+审计;
- GET /internal/attachment/{id}/content(内部面,中间件令牌已守):
  绑定复校(user_id+tenant_key 传参比对)+一次性消费(置 CONSUMED
  +删文件),重复/越权 404 同形。
"""

import contextlib
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.attachment.models import (
    MAX_BYTES,
    sniff_mime,
)
from kongming.attachment.repository import SqlAttachmentRepo
from kongming.auth.repository import SqlOperLogRepo
from kongming.auth.router import current_user, require_perm
from kongming.chat.repository import ChatRepository
from kongming.db import get_session

log = logging.getLogger(__name__)

chat_router = APIRouter(prefix="/chat", tags=["attachment"])
internal_router = APIRouter(prefix="/internal/attachment", tags=["internal"])

_CHUNK = 256 * 1024


class AttachmentOut(BaseModel):
    attachment_id: str
    filename: str
    mime: str
    size_bytes: int


@chat_router.post(
    "/attachments",
    response_model=AttachmentOut,
    status_code=201,
    dependencies=[Depends(require_perm("chat:execute"))],
)
async def upload_attachment(
    file: UploadFile,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> AttachmentOut:
    """流式读+超限即弃;magic 复核(Content-Type 可伪造);审计落库。"""
    buf = bytearray()
    while True:
        chunk = await file.read(_CHUNK)
        if not chunk:
            break
        buf.extend(chunk)
        if len(buf) > MAX_BYTES:
            raise HTTPException(status_code=400, detail="ATTACHMENT_TOO_LARGE")
    if not buf:
        raise HTTPException(status_code=400, detail="ATTACHMENT_EMPTY")
    mime = sniff_mime(bytes(buf[:16]))
    if mime is None:
        raise HTTPException(status_code=400, detail="ATTACHMENT_TYPE_INVALID")
    repo = SqlAttachmentRepo(session)
    # R1-S1:租户解析与 chat 同构(读 X-Tenant-Id 头)——曾硬解析默认租户,
    # 非默认租户下「上传→引用」必 404(归属校验按当前租户,附件却绑默认)
    chat_repo = ChatRepository(session)
    if x_tenant_id is not None:
        bound = await chat_repo.find_bound_tenant(user_id=str(user["sub"]), tenant_key=x_tenant_id)
        tenant = (bound, x_tenant_id) if bound is not None else None
    else:
        tenant = await chat_repo.find_default_tenant(user_id=str(user["sub"]))
    if tenant is None:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN")
    filename = (file.filename or "image")[:256]
    att_id = await repo.insert(
        tenant_id=str(tenant[0]),
        user_id=str(user["sub"]),
        filename=filename,
        mime=mime,
        size_bytes=len(buf),
    )
    await repo.write_file(att_id, mime, bytes(buf))
    await SqlOperLogRepo(session).add(
        str(user["sub"]),
        "attachment.upload",
        att_id,
        {"filename": filename, "mime": mime, "size": len(buf)},
    )
    try:
        await session.commit()
    except Exception:
        # 审查循环 B10:行回滚但文件已落盘=行驱动孤儿清理永不覆盖的
        # file-only 残余——补偿删文件后再抛
        with contextlib.suppress(Exception):
            await repo.delete_file(att_id, mime)
        raise
    return AttachmentOut(attachment_id=att_id, filename=filename, mime=mime, size_bytes=len(buf))


@internal_router.get("/{att_id}/content")
async def read_attachment_content(
    att_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_key: Annotated[str, Query(min_length=1, max_length=64)],
    user_id: Annotated[str, Query(min_length=1, max_length=64)],
) -> Response:
    """一次性消费(绑定复校:tenant_key 解析 + user_id 比对);
    命中即置 CONSUMED+删文件;任何未命中 404 同形(防探测)。"""
    import uuid as _uuid

    try:
        _uuid.UUID(att_id)
        _uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    repo = SqlAttachmentRepo(session)
    tid = await ChatRepository(session).find_bound_tenant(user_id=user_id, tenant_key=tenant_key)
    if tid is None:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    row = await repo.find_owned(att_id=att_id, tenant_id=str(tid), user_id=user_id)
    if row is None:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    got = await repo.consume_and_read(att_id=att_id, tenant_id=str(tid))
    if got is None:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    meta, data = got
    await session.commit()
    return Response(content=data, media_type=str(meta["mime"]))
