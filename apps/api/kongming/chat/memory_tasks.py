"""隐式记忆后台任务(022):首个 SUCCESS 轮触发(与标题改写同触发点),
agent /internal/extract-memory 提取候选 → memory service 落库(IMPLICIT)。

与 010 title_tasks 同模式:独立会话(请求作用域会话随响应关闭);
任何失败仅日志(后台任务 best-effort 语义,不阻断主链路)。
"""

import logging

from kongming.chat.agent_client import AgentClient
from kongming.db import get_sessionmaker

log = logging.getLogger(__name__)


async def extract_memory_task(
    *,
    user_id: str,
    tenant_id: str,
    question: str,
    answer: str,
    agent: AgentClient,
    pref_revision: int | None = None,  # 087:问答准备期人工版本快照
) -> None:
    """隐式记忆提取与落库;全部异常收敛(后台任务语义)。

    087:pref_revision=None(准备期版本读取失败)→ 整轮跳过(不当作 0,
    防未知版本覆盖人工修改)。"""
    from kongming.auth.repository import SqlOperLogRepo
    from kongming.memory.repository import SqlMemoryRepo
    from kongming.memory.service import MemoryService
    from kongming.rag.agent_callback import get_agent_rag_client
    from kongming.redis_client import get_redis

    if pref_revision is None:
        return
    try:
        candidates = await agent.extract_memory(question=question, answer=answer)
        if not candidates:
            return
        async with get_sessionmaker()() as s:
            svc = MemoryService(
                SqlMemoryRepo(s),
                get_agent_rag_client(get_redis()),
                SqlOperLogRepo(s),
            )
            await svc.form_implicit(
                user_id=user_id,
                tenant_id=tenant_id,
                candidates=candidates,
                base_revision=pref_revision,
            )
    except Exception:
        log.warning("隐式记忆任务异常(不阻断)", exc_info=True)
