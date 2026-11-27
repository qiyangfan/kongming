"""控制面端口(typing.Protocol):用例层只依赖本模块,不触 HTTP/SQL。"""

from datetime import datetime
from typing import Protocol

from cryptography.hazmat.primitives.asymmetric import rsa

from kongming.control.models import (
    CommandEnvelope,
    CommandOutcome,
    ControlSnapshot,
    FetchResult,
    HealthStatus,
    ProductEvent,
    SnapshotSections,
    SnapshotView,
)


class Clock(Protocol):
    def now(self) -> datetime: ...


class TokenProvider(Protocol):
    async def token(self) -> str: ...


class SnapshotSource(Protocol):
    """控制快照拉取(带 ETag;304 返回 not_modified)。"""

    async def fetch(self, *, service_id: str, etag: str) -> FetchResult: ...


class SnapshotKeySource(Protocol):
    """按快照签名 kid 解析 RSA 公钥；未知 kid 返回 None。"""

    async def key(self, key_id: str) -> rsa.RSAPublicKey | None: ...


class SnapshotStore(Protocol):
    """last-known 持久化。保存必须保持版本单调。"""

    async def load(self, *, service_id: str) -> ControlSnapshot | None: ...

    async def save(self, snapshot: ControlSnapshot) -> bool:
        """返回是否写入(旧版本不得覆盖新版本)。"""
        ...


class SnapshotApplier(Protocol):
    """把安全类快照落到本地执行缓存(租户/成员/粗角色),按版本单调。"""

    async def apply(self, sections: SnapshotSections, *, service_id: str, version: int) -> None: ...


class TenantKeyResolver(Protocol):
    """本地 tenant_id → tenant_key;执行层作用域匹配的唯一锚点。"""

    async def key_for_id(self, tenant_id: str) -> str | None: ...


class CommandSource(Protocol):
    async def claim(self, *, service_id: str, instance_id: str) -> list[CommandEnvelope]: ...

    async def ack(
        self,
        *,
        service_id: str,
        command_id: str,
        idempotency_key: str,
        outcome: CommandOutcome,
    ) -> None: ...


class CommandReceiptStore(Protocol):
    async def get(self, *, idempotency_key: str) -> CommandOutcome | None: ...

    async def begin(
        self,
        *,
        idempotency_key: str,
        command_id: str,
        action: str,
        action_version: int,
        source: str,
        actor: str,
    ) -> bool:
        """抢占执行权:键不存在才返回 True;已存在(执行中/已完成)返回 False。"""
        ...

    async def finish(
        self,
        *,
        idempotency_key: str,
        status: str,
        result: dict[str, object],
    ) -> None: ...


class UsageSink(Protocol):
    async def send(self, events: list[ProductEvent]) -> None: ...


class UsageOutbox(Protocol):
    """断网缓冲:持久化未投递事件,恢复后补发。"""

    async def add(self, event: ProductEvent) -> None: ...

    async def pending(self, *, limit: int) -> list[ProductEvent]: ...

    async def delivered(self, *, event_ids: list[str]) -> None: ...

    async def failed(self, *, event_ids: list[str], next_attempt_at: datetime) -> None: ...

    async def count_pending(self) -> int: ...

    async def trim_to(self, *, limit: int) -> int:
        """丢弃最旧的未投递事件,返回丢弃条数(缓冲上限策略)。"""
        ...


class RegistrationSink(Protocol):
    async def register(
        self,
        *,
        service_id: str,
        instance_id: str,
        version: str,
        capabilities: tuple[str, ...],
        admin_capabilities: tuple[str, ...],
        entitlement_keys: tuple[str, ...],
        health: HealthStatus,
    ) -> None: ...

    async def heartbeat(
        self,
        *,
        service_id: str,
        instance_id: str,
        version: str,
        health: HealthStatus,
    ) -> None: ...


class RegistrationStore(Protocol):
    async def record_attempt(
        self,
        *,
        service_id: str,
        instance_id: str,
        version: str,
        capabilities: tuple[str, ...],
        error: str | None,
        registered: bool,
    ) -> None: ...

    async def record_heartbeat(
        self, *, service_id: str, instance_id: str, error: str | None
    ) -> None: ...


class HealthProbe(Protocol):
    async def check(self) -> HealthStatus: ...


class AuditPort(Protocol):
    async def log(
        self, *, actor: str | None, action: str, target: str, detail: dict[str, object]
    ) -> None: ...


class IdentityCache(Protocol):
    """运行期身份解析(租户/成员/粗角色)所需的本地缓存写入口。"""

    async def apply_snapshot(self, sections: SnapshotSections) -> None: ...


class SnapshotReader(Protocol):
    async def view(self) -> SnapshotView: ...


class EntitlementReader(Protocol):
    async def for_tenant(self, tenant_key: str) -> object | None: ...
