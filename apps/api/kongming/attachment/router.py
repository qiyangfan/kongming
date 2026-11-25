"""attachment 路由(薄适配层):用户附件上传与内部一次性读取。"""

import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.attachment import service
from kongming.auth.deps import CurrentUser, require_perm
from kongming.db import get_session

log = logging.getLogger(__name__)

chat_router = APIRouter(prefix="/chat", tags=["attachment"])
internal_router = APIRouter(prefix="/internal/attachment", tags=["internal"])


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
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> AttachmentOut:
    """上传附件:类型/大小非法 400,无可用租户 403。"""
    read: Callable[[int], Awaitable[bytes]] = file.read
    try:
        saved = await service.upload_attachment(
            session,
            read=read,
            filename=file.filename,
            user_id=user.user_id,
            tenant_key=x_tenant_id,
        )
    except service.AttachmentTooLarge:
        raise HTTPException(status_code=400, detail="ATTACHMENT_TOO_LARGE") from None
    except service.AttachmentEmpty:
        raise HTTPException(status_code=400, detail="ATTACHMENT_EMPTY") from None
    except service.AttachmentTypeInvalid:
        raise HTTPException(status_code=400, detail="ATTACHMENT_TYPE_INVALID") from None
    except service.TenantForbidden:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None
    return AttachmentOut(
        attachment_id=saved.attachment_id,
        filename=saved.filename,
        mime=saved.mime,
        size_bytes=saved.size_bytes,
    )


@internal_router.get("/{att_id}/content")
async def read_attachment_content(
    att_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_key: Annotated[str, Query(min_length=1, max_length=64)],
    user_id: Annotated[str, Query(min_length=1, max_length=64)],
) -> Response:
    """内部一次性读取:任何未命中(含格式非法)一律 404 同形,防探测。"""
    try:
        uuid.UUID(att_id)
        uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    try:
        mime, data = await service.read_attachment_content(
            session, att_id=att_id, user_id=user_id, tenant_key=tenant_key
        )
    except service.AttachmentNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    return Response(content=data, media_type=mime)
