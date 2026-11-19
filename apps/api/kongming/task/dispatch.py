"""task 后台派发:代际自增 + 编排循环 + 白名单终态收口。

派发链(bump_and_schedule→_schedule_run)按模块全局名查找,整簇同模块
保持测试 monkeypatch 靶点生效;_GEN_TASKS 持后台任务强引用防 GC。
"""

import asyncio
import logging
from typing import Any, cast

from kongming.chat.agent_client import AgentClient
from kongming.config import get_settings
from kongming.db import get_sessionmaker
from kongming.task import models as m
from kongming.task.collab_repo import SqlCollabLedgerRepo
from kongming.task.service import TaskConflict, TaskInvalidTransition, TaskService
from kongming.task.system_repo import SqlTaskSystemRepo

log = logging.getLogger(__name__)

_GEN_TASKS: set[asyncio.Task[None]] = set()

_TASK_ROW_SQL = (
    "SELECT t.id, t.tenant_id, tn.tenant_key, t.user_id, t.goal, t.budget_config,"
    "       t.datasource_key, t.trace_root, t.dispatch_epoch"
    " FROM public.sys_agent_task t"
    " JOIN public.sys_tenant tn ON tn.id = t.tenant_id"
    " WHERE t.id = CAST(:i AS uuid)"
)


async def bump_and_schedule(task_id: str) -> int | None:
    """真实派发前置:派发代际原子自增,携新代数派发(防旧代数双执行)。
    代数失配会拦下旧循环的全部后续回调(begin abort/回写 stale/终态 409)。"""

    async with get_sessionmaker()() as session:
        epoch = await SqlTaskSystemRepo(session).bump_dispatch_epoch(task_id=task_id)
        await session.commit()
    if epoch is None:
        return None
    _schedule_run(task_id=task_id, epoch=epoch)
    return epoch


async def schedule_task_run(task_id: str) -> int | None:
    """公共派发入口(跨域组合根消费:触发器 task 目标自动派发):
    派发代际自增 + 后台编排循环,与创建/恢复同链。"""
    return await bump_and_schedule(task_id)


def _schedule_run(*, task_id: str, epoch: int | None = None) -> None:
    """将 Agent 任务放入后台循环并保留强引用;每个回调使用短数据库
    会话,终态经 service.finish 收口。"""

    async def _run() -> None:
        agent = AgentClient(get_settings())
        collector = _Collector(agent=agent, task_id=task_id, epoch=epoch)
        try:
            final = await collector.dispatch()
            if final == "STALE":
                log.info("任务编排代际失配退出(新派发接管): task_id=%s", task_id)
                return
            if final in (m.T_DONE, m.T_FAILED):
                await collector.finish_whitelisted(final)
            elif final not in (
                m.T_PAUSED,
                m.T_AWAITING_APPROVAL,
                m.T_CANCELLED,
                m.T_INTERRUPTED,
            ):
                # Agent 不可用/报文无效/终态未知 → 可恢复 INTERRUPTED;
                # 审批等白名单状态保持不变。
                await collector.finish_whitelisted(
                    m.T_INTERRUPTED, reason="任务编排链路异常(agent 未终态化),可显式恢复"
                )
        except Exception as e:
            log.exception("任务编排后台任务失败: task_id=%s", task_id)
            try:
                await collector.finish_whitelisted(
                    m.T_INTERRUPTED, reason=f"任务编排后台任务异常: {str(e)[:180]}"
                )
            except Exception:
                log.exception("任务中断终态落库失败: task_id=%s", task_id)

    task = asyncio.get_running_loop().create_task(_run())
    _GEN_TASKS.add(task)
    task.add_done_callback(_GEN_TASKS.discard)


class _Collector:
    """一次后台派发的编排驱动与终态收口(段一会话读行,段二 agent,段三白名单收口)。"""

    def __init__(self, *, agent: AgentClient, task_id: str, epoch: int | None = None) -> None:
        self._agent = agent
        self._task_id = task_id
        self._epoch = epoch

    async def _read_row(self) -> dict[str, Any] | None:
        from sqlalchemy import text

        async with get_sessionmaker()() as session:
            row = (
                (await session.execute(text(_TASK_ROW_SQL), {"i": self._task_id}))
                .mappings()
                .one_or_none()
            )
            await session.commit()
        return dict(row) if row is not None else None

    async def dispatch(self) -> str:
        """段一读任务行 + 授权工具注入集(短会话)→ 段二 agent 编排。
        返回 agent 报告的终态口径(任务不存在返回空串)。"""
        from kongming.auth.repository import SqlPermRepo
        from kongming.toolcall import registry as tool_registry

        row = await self._read_row()
        if row is None:
            return ""
        tools: list[dict[str, object]] = []
        async with get_sessionmaker()() as session:
            codes = await SqlPermRepo(session).list_codes(str(row["user_id"]))
            tools = [dict(s) for s in tool_registry.filter_specs(set(codes))]
            await session.commit()
        payload: dict[str, object] = {
            "task_id": self._task_id,
            "epoch": self._epoch or 0,
            "trace_root": str(row["trace_root"] or ""),
            # 租户键以任务行归属为准(header 缺省/他人租户视图不污染回调解析)
            "tenant_key": str(row["tenant_key"] or "default"),
            "datasource_key": str(row["datasource_key"] or "default"),
            "user_id": str(row["user_id"]),
            "goal": str(row["goal"] or ""),
            "budget": dict(cast("dict[str, object]", row["budget_config"] or {})),
            "tools": tools,
        }
        result = await self._agent.run_task(payload)
        return str((result or {}).get("final_status") or "")

    async def finish_whitelisted(self, status: str, reason: str | None = None) -> None:
        """段三终态收口:重读当前行后经 service.finish 白名单(白名单+CAS+
        DONE 步骤校验);任务已被用户/控制面推进到不可达终态时拒绝并保持
        现态(终态不复活),不覆盖用户决策。"""
        fresh = await self._read_row()
        if fresh is None:
            return
        if self._epoch is not None and int(fresh.get("dispatch_epoch") or 0) != self._epoch:
            # 代数已推进(新派发接管):本循环不得收口任务终态
            log.info(
                "任务终态收口放弃(派发代际已推进): task_id=%s epoch=%s",
                self._task_id,
                self._epoch,
            )
            return
        async with get_sessionmaker()() as session:
            try:
                await TaskService(SqlCollabLedgerRepo(session)).finish(
                    task_id=self._task_id,
                    tenant_id=str(fresh["tenant_id"]),
                    status=status,
                    fail_reason=reason,
                    # 与内部 finish 端点同口径:终态收口必须持派发代际,
                    # service 内闸 + 写入谓词双闸(复核与收口间仍有并发窗口)
                    epoch=self._epoch,
                )
                await session.commit()
            except TaskInvalidTransition as e:
                # 非法迁移的审计事件已入事务:提交留痕(与公开端点同口径)
                await session.commit()
                log.info(
                    "任务终态收口被白名单拒绝(审计已留痕): task_id=%s detail=%s",
                    self._task_id,
                    e,
                )
            except TaskConflict as e:
                await session.rollback()
                log.info(
                    "任务终态收口被拒(状态已推进,保持现态): task_id=%s detail=%s",
                    self._task_id,
                    e,
                )
