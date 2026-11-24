"""chat 域内部面:同类 api 实例间的在途轮取消转发(ADR-002 内部通道)。

取消请求落到非持有该轮的实例时,由那个实例经此端点把取消转给持有者。持有侧
直接跑用户面同一套取消编排(本地登记命中即编排,未命中走落库兜底),不写第二套。
归属用户由转发方随请求携带(内部通道互信),持有侧仍按「用户 + trace」复核。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from kongming.chat.dependencies import get_chat_service
from kongming.chat.domain_errors import TurnNotFound
from kongming.chat.schemas import TurnCancelOut
from kongming.chat.service import ChatService

router = APIRouter(prefix="/internal/chat", tags=["internal"])


class TurnCancelIn(BaseModel):
    """跨实例取消转发入参:轮次标识 + 归属用户(转发方从已鉴权请求带入)。"""

    trace_id: str = Field(min_length=1, max_length=64)
    user_id: str = Field(min_length=1, max_length=64)


@router.post("/turn-cancel", response_model=TurnCancelOut)
async def turn_cancel(
    req: TurnCancelIn, svc: Annotated[ChatService, Depends(get_chat_service)]
) -> TurnCancelOut:
    """取消一轮在途问答(持有侧入口):与用户面取消端点同一编排、同一归属规则。"""
    try:
        settled = await svc.cancel_turn(user_id=req.user_id, trace_id=req.trace_id)
    except TurnNotFound:
        # 该轮在持有侧也已不在途且无落库结果:与用户面同形 404,转发方据此走兜底
        raise HTTPException(status_code=404, detail="TURN_NOT_FOUND") from None
    return TurnCancelOut(outcome=settled.outcome, message_id=settled.message_id)
