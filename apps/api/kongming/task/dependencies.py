"""task 组合根:服务装配(端点内直调,非 Depends 键)。"""

from sqlalchemy.ext.asyncio import AsyncSession

from kongming.task.adapters import CollabBotResolver
from kongming.task.collab import CollabLedgerService
from kongming.task.collab_repo import SqlCollabLedgerRepo
from kongming.task.service import TaskService


def get_task_service(session: AsyncSession) -> TaskService:
    from kongming.task.graph_repo import SqlGraphRepo

    # 任务主链与协作账本共用同一会话/事务;组合仓储兼两端口
    return TaskService(
        SqlCollabLedgerRepo(session),
        bot_resolver=CollabBotResolver(session),
        graph_repo=SqlGraphRepo(session),
    )


def get_collab_ledger_service(session: AsyncSession) -> CollabLedgerService:
    return CollabLedgerService(SqlCollabLedgerRepo(session))
