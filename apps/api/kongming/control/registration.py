"""服务注册/心跳:异步注册 + 指数退避 + 恢复补报。

注册失败不得阻塞启动或核心流程;心跳只报告真实健康探测结果,不伪造 ready。
"""

import asyncio
import logging

from kongming.control.capabilities import (
    ADMIN_CAPABILITIES,
    CAPABILITIES,
    ENTITLEMENT_KEYS,
)
from kongming.control.models import HealthStatus
from kongming.control.ports import AuditPort, HealthProbe, RegistrationSink, RegistrationStore

log = logging.getLogger(__name__)


class RegistrationService:
    def __init__(
        self,
        *,
        sink: RegistrationSink,
        store: RegistrationStore,
        health: HealthProbe,
        service_id: str,
        instance_id: str,
        version: str,
        capabilities: tuple[str, ...] = CAPABILITIES,
        audit: AuditPort | None = None,
    ) -> None:
        self._sink = sink
        self._store = store
        self._health = health
        self._audit = audit
        self._service_id = service_id
        self._instance_id = instance_id
        self._version = version
        self._capabilities = capabilities

    async def register_once(self) -> bool:
        try:
            health = await self._health.check()
            await self._sink.register(
                service_id=self._service_id,
                instance_id=self._instance_id,
                version=self._version,
                capabilities=self._capabilities,
                admin_capabilities=ADMIN_CAPABILITIES,
                entitlement_keys=ENTITLEMENT_KEYS,
                health=health,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("服务注册失败(后台退避重试): %s", e)
            await self._store.record_attempt(
                service_id=self._service_id,
                instance_id=self._instance_id,
                version=self._version,
                capabilities=self._capabilities,
                error=str(e)[:300],
                registered=False,
            )
            return False
        await self._store.record_attempt(
            service_id=self._service_id,
            instance_id=self._instance_id,
            version=self._version,
            capabilities=self._capabilities,
            error=None,
            registered=True,
        )
        await self._audit_log("control.registration", registered=True, error="")
        return True

    async def heartbeat_once(self) -> bool:
        try:
            health = await self._health.check()
            await self._sink.heartbeat(
                service_id=self._service_id,
                instance_id=self._instance_id,
                version=self._version,
                health=health,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("服务心跳失败(后台退避重试): %s", e)
            await self._store.record_heartbeat(
                service_id=self._service_id, instance_id=self._instance_id, error=str(e)[:300]
            )
            return False
        await self._store.record_heartbeat(
            service_id=self._service_id, instance_id=self._instance_id, error=None
        )
        await self._audit_log("control.heartbeat", registered=True, error="")
        return True

    async def _audit_log(self, action: str, *, registered: bool, error: str) -> None:
        if self._audit is None:
            return
        try:
            await self._audit.log(
                actor=None,
                action=action,
                target=self._service_id,
                detail={
                    "instance_id": self._instance_id,
                    "version": self._version,
                    "ok": registered,
                    "error": error,
                },
            )
        except Exception:  # 审计旁路不得影响注册
            log.warning("注册审计写入失败", exc_info=True)

    async def run_forever(self, *, heartbeat_sec: int, backoff_max_sec: int) -> None:
        """先注册后心跳;任一步失败回到注册,退避上限封顶。"""
        backoff = min(5, backoff_max_sec)
        registered = False
        while True:
            try:
                ok = await self.heartbeat_once() if registered else await self.register_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("注册循环异常")
                ok = False
            if ok:
                backoff = min(5, backoff_max_sec)
                registered = True
                await asyncio.sleep(heartbeat_sec)
            else:
                registered = False
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, backoff_max_sec)


def empty_health() -> HealthStatus:
    return HealthStatus(ready=False, database=False, redis=False, detail="未探测")
