"""控制面运行时:组合根 + 后台循环 + 安全上下文闸。

managed 才启动快照/注册/命令/用量循环;standalone 全部退化成本地模式。
启动不等待控制面:首个快照未到之前,受保护请求由安全闸拒绝(失败关闭)。
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kongming.config import Settings
from kongming.control.capabilities import SERVICE_ID
from kongming.control.catalog import CatalogService
from kongming.control.client import ControlPlaneClient
from kongming.control.commands import CommandExecutor
from kongming.control.entitlement import EntitlementService, QuotaLimits
from kongming.control.events import install_event_sink
from kongming.control.identity import build_token_provider
from kongming.control.identity_cache import SqlIdentityApplier
from kongming.control.local_actions import LocalCommandActions
from kongming.control.models import ControlUnavailable, FetchResult, HealthStatus
from kongming.control.policy import PolicyService
from kongming.control.ports import SnapshotSource
from kongming.control.registration import RegistrationService
from kongming.control.service_auth import ServiceTokenVerifier
from kongming.control.snapshot import SnapshotService, SystemClock
from kongming.control.store import (
    SqlCommandReceiptStore,
    SqlRegistrationStore,
    SqlSnapshotStore,
    SqlUsageOutbox,
)
from kongming.control.usage import UsageReporter

log = logging.getLogger(__name__)


class _DisabledSource:
    async def fetch(self, *, service_id: str, etag: str) -> FetchResult:
        raise ControlUnavailable("控制面未启用")


class _SqlAuditPort:
    def __init__(self, maker: async_sessionmaker[AsyncSession]) -> None:
        self._maker = maker

    async def log(
        self, *, actor: str | None, action: str, target: str, detail: dict[str, object]
    ) -> None:
        from kongming.auth.repository import SqlOperLogRepo

        payload = dict(detail)
        payload.setdefault("actor", actor or "")
        async with self._maker() as s:
            await SqlOperLogRepo(s).add(None, action, target, payload)
            await s.commit()


class SqlHealthProbe:
    def __init__(self, maker: async_sessionmaker[AsyncSession], redis: Redis) -> None:
        self._maker = maker
        self._redis = redis

    async def check(self) -> HealthStatus:
        database = True
        redis_ok = True
        try:
            async with self._maker() as s:
                await s.execute(text("SELECT 1"))
        except Exception:  # noqa: BLE001  # 探测失败即不健康,不外抛
            database = False
        try:
            await cast("Any", self._redis).ping()  # redis-py 未注解(strict 判部分未知)
        except Exception:  # noqa: BLE001
            redis_ok = False
        return HealthStatus(
            ready=database and redis_ok,
            database=database,
            redis=redis_ok,
            detail="" if database and redis_ok else "依赖探测失败",
        )


@dataclass(frozen=True)
class ControlStatus:
    mode: str
    enabled: bool
    service_id: str
    base_url: str
    snapshot_present: bool
    snapshot_version: int
    observed_at: datetime | None
    stale: bool
    security_ok: bool
    pending_usage: int
    dropped_usage: int
    readonly_enabled: bool
    last_error: str


class ControlRuntime:
    def __init__(
        self,
        *,
        settings: Settings,
        maker: async_sessionmaker[AsyncSession],
        redis: Redis,
        client: ControlPlaneClient | None,
        snapshot: SnapshotService,
        entitlements: EntitlementService,
        policy: PolicyService,
        catalog: CatalogService,
        registration: RegistrationService | None,
        usage: UsageReporter | None,
        executor: CommandExecutor,
        reader_verifier: ServiceTokenVerifier,
    ) -> None:
        self._s = settings
        self._maker = maker
        self._redis = redis
        self._client = client
        self._snapshot = snapshot
        self._entitlements = entitlements
        self._policy = policy
        self._catalog = catalog
        self._registration = registration
        self._usage = usage
        self._executor = executor
        self._reader_verifier = reader_verifier
        self._tasks: list[asyncio.Task[None]] = []
        self._last_error: str = ""

    @property
    def settings(self) -> Settings:
        return self._s

    @property
    def snapshot(self) -> SnapshotService:
        return self._snapshot

    @property
    def entitlements(self) -> EntitlementService:
        return self._entitlements

    @property
    def policy(self) -> PolicyService:
        return self._policy

    @property
    def catalog(self) -> CatalogService:
        return self._catalog

    @property
    def executor(self) -> CommandExecutor:
        return self._executor

    @property
    def reader_verifier(self) -> ServiceTokenVerifier:
        return self._reader_verifier

    @property
    def managed(self) -> bool:
        return self._s.control_enabled()

    async def security_available(self) -> bool:
        if not self.managed:
            return True
        view = await self._snapshot.view()
        return view.present

    async def status(self) -> ControlStatus:
        view = await self._snapshot.view()
        pending = await SqlUsageOutbox(self._maker).count_pending() if self.managed else 0
        return ControlStatus(
            mode=self._s.control_mode,
            enabled=self.managed,
            service_id=SERVICE_ID,
            base_url=self._s.control_base_url,
            snapshot_present=view.present,
            snapshot_version=view.version,
            observed_at=view.observed_at,
            stale=view.stale,
            security_ok=(not self.managed) or view.present,
            pending_usage=pending,
            dropped_usage=self._usage.dropped if self._usage is not None else 0,
            readonly_enabled=self._s.control_readonly_enabled,
            last_error=self._last_error,
        )

    def start(self) -> None:
        if not self.managed or self._tasks:
            return
        if self._client is None or self._registration is None or self._usage is None:
            return
        self._tasks = [
            asyncio.create_task(self._snapshot_loop(), name="control-snapshot"),
            asyncio.create_task(
                self._registration.run_forever(
                    heartbeat_sec=self._s.control_heartbeat_sec,
                    backoff_max_sec=self._s.control_backoff_max_sec,
                ),
                name="control-registration",
            ),
            asyncio.create_task(
                self._usage.run_forever(interval_sec=self._s.control_usage_flush_sec),
                name="control-usage",
            ),
            asyncio.create_task(self._command_loop(), name="control-commands"),
        ]
        install_event_sink(self._usage)

    async def stop(self) -> None:
        install_event_sink(None)
        from kongming.guardrail.policy_scope import install_policy_scope

        install_policy_scope(None)
        if not self._tasks:
            return
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

    async def _snapshot_loop(self) -> None:
        while True:
            try:
                result = await self._snapshot.refresh()
                if result in ("updated", "not_modified"):
                    self._last_error = ""
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001  # 循环永不退出;last-known 继续服务
                self._last_error = str(e)[:300]
                log.warning("控制快照循环异常(下轮重试): %s", e)
            await asyncio.sleep(self._s.control_snapshot_poll_sec)

    async def _command_loop(self) -> None:
        client = self._client
        if client is None:
            return
        instance_id = self._s.control_instance_id or SERVICE_ID
        while True:
            try:
                commands = await client.claim(service_id=SERVICE_ID, instance_id=instance_id)
                for envelope in commands:
                    outcome = await self._executor.process(envelope)
                    await client.ack(
                        service_id=SERVICE_ID,
                        command_id=envelope.command_id,
                        idempotency_key=envelope.idempotency_key,
                        outcome=outcome,
                    )
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("命令循环异常(下轮重试): %s", e)
            await asyncio.sleep(self._s.control_command_poll_sec)


def build_runtime(
    *,
    settings: Settings,
    maker: async_sessionmaker[AsyncSession],
    redis: Redis,
    transport: object | None = None,
) -> ControlRuntime:
    """按配置装配运行时。transport 仅供测试注入 httpx MockTransport。"""
    import httpx

    http_transport = transport if isinstance(transport, httpx.AsyncBaseTransport) else None
    managed = settings.control_enabled()
    mode = (settings.control_mode or "").strip().lower()
    client = _build_client(settings, http_transport) if managed else None
    snapshot = _build_snapshot(settings, maker, client, managed)
    entitlements, policy, catalog = _build_domain(settings, mode, snapshot)
    registration, usage = _build_reporters(settings, maker, redis, client, managed)
    return ControlRuntime(
        settings=settings,
        maker=maker,
        redis=redis,
        client=client,
        snapshot=snapshot,
        entitlements=entitlements,
        policy=policy,
        catalog=catalog,
        registration=registration,
        usage=usage,
        executor=_build_executor(maker),
        reader_verifier=ServiceTokenVerifier(
            local_token=settings.control_reader_token,
            issuer=settings.control_keycloak_issuer,
            allowed_client_ids=tuple(
                c.strip() for c in settings.control_reader_client_ids.split(",") if c.strip()
            ),
            transport=http_transport,
        ),
    )


def _build_client(settings: Settings, transport: object | None) -> ControlPlaneClient:
    provider = build_token_provider(
        local_token=settings.control_service_token,
        issuer=settings.control_keycloak_issuer,
        client_id=settings.control_keycloak_client_id,
        client_secret=settings.control_keycloak_client_secret,
        transport=transport,  # type: ignore[arg-type]  # 已由调用方收窄
    )
    return ControlPlaneClient(
        base_url=settings.control_base_url,
        service_id=SERVICE_ID,
        token_provider=provider,
        transport=transport,  # type: ignore[arg-type]
        timeout_sec=settings.control_http_timeout_sec,
    )


def _build_snapshot(
    settings: Settings,
    maker: async_sessionmaker[AsyncSession],
    client: ControlPlaneClient | None,
    managed: bool,
) -> SnapshotService:
    source: SnapshotSource = client if client is not None else _DisabledSource()
    return SnapshotService(
        source=source,
        store=SqlSnapshotStore(maker),
        applier=SqlIdentityApplier(maker),
        clock=SystemClock(),
        service_id=SERVICE_ID,
        signing_secret=settings.control_signing_secret,
        stale_after_sec=settings.control_stale_after_sec,
        enabled=managed,
    )


def _build_domain(
    settings: Settings, mode: str, snapshot: SnapshotService
) -> tuple[EntitlementService, PolicyService, CatalogService]:
    defaults = QuotaLimits(
        calls_daily=settings.chat_quota_daily_calls,
        calls_monthly=settings.chat_quota_monthly_calls,
        bot_calls_daily=settings.bot_chat_rate_per_minute,
        tool_calls_per_minute=settings.tool_rate_limit_per_minute,
    )
    entitlements = EntitlementService(snapshots=snapshot, mode=mode, default_limits=defaults)
    from kongming.guardrail.policy_scope import install_policy_scope
    from kongming.guardrail.rules import REGISTRY as GUARDRAIL_RULES

    policy = PolicyService(
        snapshots=snapshot,
        mode=mode,
        local_rule_names=frozenset(rule.name for rule in GUARDRAIL_RULES),
    )
    install_policy_scope(policy if mode == "managed" else None)
    catalog = CatalogService(snapshots=snapshot, entitlements=entitlements, mode=mode)
    return entitlements, policy, catalog


def _build_reporters(
    settings: Settings,
    maker: async_sessionmaker[AsyncSession],
    redis: Redis,
    client: ControlPlaneClient | None,
    managed: bool,
) -> tuple[RegistrationService | None, UsageReporter | None]:
    if not managed or client is None:
        return None, None
    registration = RegistrationService(
        sink=client,
        store=SqlRegistrationStore(maker),
        health=SqlHealthProbe(maker, redis),
        service_id=SERVICE_ID,
        instance_id=settings.control_instance_id or SERVICE_ID,
        version=_version(),
        audit=_SqlAuditPort(maker),
    )
    usage = UsageReporter(
        outbox=SqlUsageOutbox(maker),
        sink=client,
        clock=SystemClock(),
        enabled=True,
        buffer_max=settings.control_usage_buffer_max,
    )
    return registration, usage


def _build_executor(maker: async_sessionmaker[AsyncSession]) -> CommandExecutor:
    return CommandExecutor(
        receipts=SqlCommandReceiptStore(maker),
        handlers=LocalCommandActions(maker).handlers,
        audit=_SqlAuditPort(maker),
    )


def _version() -> str:
    from kongming import __version__

    return __version__
