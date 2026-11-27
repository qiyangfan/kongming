"""有限运行时动作的本地实现(reindex/清缓存/取消任务)。

只调用既有域服务的公开能力,不绕过权限与不变量;控制面命令的 actor 不以
伪造用户身份落库(user_id 记空,actor 进审计 detail)。
"""

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kongming.config import get_settings
from kongming.control.commands import ActionHandler
from kongming.rag.cache import ContextCache
from kongming.redis_client import get_redis

log = logging.getLogger(__name__)


class LocalCommandActions:
    def __init__(self, maker: async_sessionmaker[AsyncSession]) -> None:
        self._maker = maker
        self.handlers: dict[str, ActionHandler] = {
            "rag.reindex": self.reindex,
            "cache.clear": self.clear_cache,
            "task.cancel": self.cancel_task,
        }

    async def _tenant(self, s: AsyncSession, tenant_key: str) -> str:
        tid = (
            await s.execute(
                text("SELECT id FROM public.sys_tenant WHERE tenant_key = :k"),
                {"k": tenant_key},
            )
        ).scalar_one_or_none()
        if tid is None:
            raise ValueError(f"租户不存在: {tenant_key}")
        return str(tid)

    async def reindex(self, params: dict[str, object], *, actor: str) -> dict[str, object]:
        from kongming.rag.dependencies import get_rag_admin_service

        tenant_key = str(params["tenant_key"])
        datasource_key = str(params["datasource_key"])
        async with self._maker() as s:
            tenant_id = await self._tenant(s, tenant_key)
            svc = get_rag_admin_service(s)
            result = await svc.reindex(
                actor_id="",  # 控制面命令不伪装成本地用户(空串由审计层落 NULL)
                tenant_id=tenant_id,
                tenant_key=tenant_key,
                datasource_key=datasource_key,
            )
        return {
            "deleted": result.deleted,
            "rebuilt": result.rebuilt,
            "enriched": result.enriched,
            "actor": actor,
        }

    async def clear_cache(self, params: dict[str, object], *, actor: str) -> dict[str, object]:
        tenant_key = str(params["tenant_key"])
        datasource_key = str(params["datasource_key"])
        async with self._maker() as s:
            tenant_id = await self._tenant(s, tenant_key)
        cache = ContextCache(get_redis(), ts_config=get_settings().rag_fulltext_ts_config)
        await cache.evict(tenant_id, datasource_key)
        return {"tenant_key": tenant_key, "datasource_key": datasource_key, "actor": actor}

    async def cancel_task(self, params: dict[str, object], *, actor: str) -> dict[str, object]:
        from kongming.task.dependencies import get_task_service

        tenant_key = str(params["tenant_key"])
        task_id = str(params["task_id"])
        reason = str(params.get("reason") or "控制面取消")
        async with self._maker() as s:
            tenant_id = await self._tenant(s, tenant_key)
            row: Any = (
                await s.execute(
                    text(
                        "SELECT user_id FROM public.sys_agent_task"
                        " WHERE id = CAST(:i AS uuid) AND tenant_id = CAST(:t AS uuid)"
                    ),
                    {"i": task_id, "t": tenant_id},
                )
            ).scalar_one_or_none()
            if row is None:
                raise ValueError("任务不存在")
            svc = get_task_service(s)
            status = await svc.cancel(
                task_id=task_id,
                tenant_id=tenant_id,
                user_id=str(row),
                reason=f"{reason}({actor})",
            )
            await s.commit()
        return {"task_id": task_id, "status": status, "actor": actor}
