"""trigger 组合根:服务装配 + 调度器单例与 lifespan 启停。"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.repository import SqlOperLogRepo
from kongming.config import get_settings
from kongming.datasource.cipher import get_cipher
from kongming.db import get_sessionmaker
from kongming.trigger.ports import EventGateVerdict
from kongming.trigger.repository import SqlTriggerRepo
from kongming.trigger.runners import (
    HttpxNotifier,
    ImaplibMailClient,
    TargetRunnerImpl,
    TriggerInputGuardrail,
)
from kongming.trigger.scheduler import TriggerScheduler
from kongming.trigger.service import TriggerService


def make_trigger_service(session: AsyncSession, actor_id: str | None = None) -> TriggerService:
    settings = get_settings()

    class _Oper:
        async def log(self, *, action: str, target: str, detail: dict[str, object]) -> None:
            await SqlOperLogRepo(session).add(actor_id, action, target, detail)

    class _TargetCheckerImpl:
        """目标存在性(与前端候选同源,与派发期同口径):
        workflow=创建者视角可见集合(归属过滤:个人创建 + 所在团队,
        与 workflow 列表端点同源同参);bot=租户级存在;
        task=模板数据源存在(数据源为租户级资源,归属人可用口径)。"""

        async def target_exists(
            self, *, target_type: str, target_key: str, tenant_id: str, creator_id: str
        ) -> bool:
            if target_type == "task":
                n = (
                    await session.execute(
                        text(
                            "SELECT count(*) FROM public.sys_datasource"
                            " WHERE tenant_id=CAST(:t AS uuid) AND datasource_key=:k"
                        ),
                        {"t": tenant_id, "k": target_key},
                    )
                ).scalar_one()
                return int(n) > 0
            if target_type == "workflow":
                from kongming.team.repository import SqlTeamRepo
                from kongming.workflow.repository import SqlWorkflowRepo

                # 团队候选范围与 list_workflows 保持一致(team_ids=角色映射
                # 键集):缺 team_ids 则他人创建的团队 workflow 在候选可选、
                # 提交必拒——checker 与候选必须同源
                roles = await SqlTeamRepo(session).role_map(user_id=creator_id)
                rows = await SqlWorkflowRepo(session).list_latest(
                    tenant_id=tenant_id,
                    user_id=creator_id,
                    team_ids=list(roles.keys()),
                )
                return any(r["wf_key"] == target_key for r in rows)
            from kongming.bot.repository import SqlBotRepo

            return (
                await SqlBotRepo(session).get_by_key(tenant_id=tenant_id, bot_key=target_key)
                is not None
            )

    class _TaskGatewayImpl:
        """task 目标侧查/写(线程去重判定与事件归属;同会话直调任务仓储)。"""

        async def is_open(self, *, task_id: str) -> bool:
            status = (
                await session.execute(
                    text("SELECT status FROM public.sys_agent_task WHERE id=CAST(:i AS uuid)"),
                    {"i": task_id},
                )
            ).scalar()
            return status in ("PLANNING", "RUNNING", "AWAITING_APPROVAL", "PAUSED")

        async def append_thread_event(self, *, task_id: str, event: dict[str, Any]) -> bool:
            from kongming.task.collab_repo import SqlCollabLedgerRepo as _Repo

            await _Repo(session).append_task_event(task_id=task_id, event=event)
            return True

    class _EventJudgeImpl:
        """事件立项判读端口绑定(AgentClient 降级契约保证 None=判读不可用)。"""

        async def judge(
            self, *, source: str, summary: str, goal_preview: str
        ) -> EventGateVerdict | None:
            from kongming.chat.dependencies import get_agent_client as _gac

            out = await _gac().judge_event(
                source=source, summary=summary, goal_preview=goal_preview
            )
            if out is None:
                return None
            return EventGateVerdict(verdict=str(out["verdict"]), reason=str(out["reason"]))

    return TriggerService(
        repo=SqlTriggerRepo(session),
        cipher=get_cipher(settings.datasource_encrypt_key),
        runner=TargetRunnerImpl(),
        notifier=HttpxNotifier(),
        oper=_Oper(),
        target_checker=_TargetCheckerImpl(),
        # 部署级固定退避(重试次数上限在触发器行,间隔属部署)
        retry_backoff_sec=settings.trigger_retry_backoff_sec,
        # 邮件入站客户端与输入护栏(组合根绑定)
        mail=ImaplibMailClient(),
        input_guard=TriggerInputGuardrail(),
        event_judge=_EventJudgeImpl(),
        task_gateway=_TaskGatewayImpl(),
    )


# 调度器单例(lifespan start/stop 调用)
scheduler = TriggerScheduler(service_factory=None)  # 工厂在 lifespan 注入


async def _scheduler_factory() -> tuple[TriggerService, Any]:
    session = get_sessionmaker()()
    svc = make_trigger_service(session)

    async def _close() -> None:
        await session.close()

    return svc, _close


def start_scheduler() -> None:
    scheduler.attach_factory(_scheduler_factory)  # lifespan 注入点
    scheduler.start()


async def stop_scheduler() -> None:
    await scheduler.stop()
