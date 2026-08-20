"""会话标题 LLM 改写后台任务(010,chat spec「会话持久化」两阶段生成)。

由 /chat/query 每个 SUCCESS 轮后经 FastAPI BackgroundTasks 触发(响应返回后执行,
主链路零延迟);「首个成功轮」语义由守卫读实现——已改写/已重命名的会话在 LLM
调用前短路(省 token),故实际改写只发生在标题仍为截断值的首个成功轮。

双闸防覆盖(chat spec「手动重命名后不被自动改写覆盖」):
1. 守卫读:当前标题 != 首问截断值(已重命名/已改写/会话被删)→ LLM 调用前短路;
2. 条件写:update_title_if_current(WHERE title=expected)——守卫与回调竞态的兜底,
   用户恰在 LLM 回调期间改标题时改写静默落败。
连接卫生(R2 评审实证):守卫读在首个短会话内完成并随块退出释放连接,LLM 调用
(最长 30s)期间零池占用;回写另开短会话——并发成功轮不会把控制面池耗尽。
任何异常仅日志:标题是增强体验,截断标题永远可用。
"""

import logging

from kongming.chat.agent_client import AgentClient
from kongming.chat.repository import ChatRepository
from kongming.db import get_sessionmaker

log = logging.getLogger(__name__)

TITLE_TRUNCATED_LEN = 30  # 首问截断长度(与 chat.service SESSION_TITLE_LEN 一致)
TITLE_MAX_LEN = 50  # LLM 标题入库上限(agent 侧已净化,此为双闸)


async def rewrite_session_title(session_id: str, agent: AgentClient) -> None:
    """标题改写后台任务;改写素材与守卫基准恒为会话首条 user 问句
    (次轮触发时语义仍在首问,R1 评审:原 question 形参从未使用,已删)。"""
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
    except Exception:  # noqa: BLE001  # 后台任务永不外抛(响应已发出)
        log.warning("标题改写后台任务异常 session=%s", session_id, exc_info=True)
