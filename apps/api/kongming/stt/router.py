"""stt 路由(114):POST /chat/stt——语音转文字进输入框。

权限复用 chat:execute(问数输入能力族,零新权限点);multipart 音频,
15MB 上限;即转即弃(不落盘不建附件行),操作审计仅记 action+size
(音频与转写正文不进日志)。引擎不可用 → 503 明示配方(design D2
可选启用形态)。
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.repository import SqlOperLogRepo
from kongming.auth.router import current_user, require_perm
from kongming.db import get_session
from kongming.stt.engine import FasterWhisperEngine
from kongming.stt.ports import EngineUnavailable
from kongming.stt.service import AudioTooLarge, SttService

router = APIRouter(prefix="/chat", tags=["chat"])

_service: SttService | None = None


def get_stt_service() -> SttService:
    """组合根:引擎单例(懒加载,进程内首次请求时才加载模型)。"""
    global _service
    if _service is None:
        from kongming.config import get_settings

        _service = SttService(FasterWhisperEngine(get_settings().stt_model))
    return _service


@router.post(
    "/stt",
    dependencies=[Depends(require_perm("chat:execute"))],
)
async def transcribe_audio(
    request: Request,
    file: Annotated[UploadFile, File(description="音频文件(webm/opus/mp3/wav/m4a)")],
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[SttService, Depends(get_stt_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """语音转文字:音频 → 文本(即转即弃;前端填入输入框确认后发送)。"""
    audio = await file.read()
    # 操作审计仅记 action+体积(音频与转写正文不进日志,114 design D4);
    # 审计与业务同会话=同事务(007 组合模式)
    await SqlOperLogRepo(session).add(
        str(user["sub"]),
        "chat.stt.transcribe",
        None,
        {"size": len(audio)},
    )
    try:
        text = await svc.transcribe(audio)
    except AudioTooLarge as e:
        raise HTTPException(status_code=413, detail=str(e)) from None
    except EngineUnavailable as e:
        raise HTTPException(status_code=503, detail=f"STT_UNAVAILABLE: {e}") from None
    await session.commit()
    return {"text": text, "filename": file.filename or "audio", "request_id": str(uuid.uuid4())}
