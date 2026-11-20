"""成功问答后的标题改写任务。

只改写仍为首问截断值的标题，并用条件更新避免覆盖用户修改。
数据库会话不跨越 LLM 调用；异常只记录日志，原截断标题仍可用。
"""

import logging

from kongming.chat.agent_client import AgentClient
from kongming.chat.repository import ChatRepository
from kongming.db import get_sessionmaker

log = logging.getLogger(__name__)

TITLE_TRUNCATED_LEN = 30  # 首问截断长度(与 chat.service SESSION_TITLE_LEN 一致)
TITLE_MAX_LEN = 50  # LLM 标题入库上限(agent 侧已净化,此为双闸)


async def rewrite_session_title(session_id: str, agent: AgentClient) -> None:
    """用会话首条 user 问句生成标题，并仅在标题仍为截断值时更新。"""
    try:
        # 段一:守卫读(短会话,块退出即释放连接回池)
        async with get_sessionmaker()() as s:
            repo = ChatRepository(s)
            first_q = await repo.first_user_question(session_id)
            if first_q is None:
                return  # 会话无消息(并发删除/persist 边界)
            expected = first_q[:TITLE_TRUNCATED_LEN]
            current = await repo.get_session_title(session_id)
            if current is None or current != expected:
                return  # 已重命名/已改写:LLM 调用前短路(省 token)

        # 段二:LLM 调用(池外等待;降级/不可达 → None,保持截断标题)
        title = await agent.rewrite_title(question=first_q)
        if not title:
            return

        # 段三:条件写(另开短会话;竞态落败仅日志)
        async with get_sessionmaker()() as s:
            repo = ChatRepository(s)
            updated = await repo.update_title_if_current(
                session_id=session_id, expected=expected, new_title=title[:TITLE_MAX_LEN]
            )
            await s.commit()
            if updated == 0:
                log.info("标题改写竞态落败(用户已重命名) session=%s", session_id)
    except Exception:  # 后台任务永不外抛(响应已发出)
        log.warning("标题改写后台任务异常 session=%s", session_id, exc_info=True)
