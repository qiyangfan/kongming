"""voice 域基础设施:审计出口(复用既有操作审计存储,独立会话)。

通话审计不能绑请求会话(通话生命周期跨请求,在 WebSocket 适配层内结束),
用独立会话写入,与流式失败落库同一范式。
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.repository import SqlOperLogRepo
from kongming.db import get_sessionmaker
from kongming.voice.ports import OperLogSink

log = logging.getLogger(__name__)


class SessionOperLogSink(OperLogSink):
    """每次写入自取独立会话并提交(通话审计尽力而为,失败由调用方记录)。"""

    def __init__(self, session: AsyncSession | None = None) -> None:
        # session 参数仅为测试注入便利;生产路径用 sessionmaker 自取
        self._session = session

    async def add(
        self, user_id: str | None, action: str, target: str | None, detail: dict[str, object]
    ) -> None:
        if self._session is not None:
            await SqlOperLogRepo(self._session).add(user_id, action, target, detail)
            return
        async with get_sessionmaker()() as s:
            await SqlOperLogRepo(s).add(user_id, action, target, detail)
            await s.commit()
