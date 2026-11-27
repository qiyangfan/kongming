"""共享控制面领域模型(零框架)。

快照是控制面唯一 push 通道的拉取形态:全局版本单调、签名可验、分节载荷。
六节里 tenants/members/coarse_roles/entitlements/policy_packages 属安全类,
缺失或非法即拒绝受保护操作;catalog 属非安全类,可回退 last-known 并标 stale。
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

ControlMode = Literal["standalone", "managed"]

SECURITY_SECTIONS: tuple[str, ...] = (
    "tenants",
    "members",
    "coarse_roles",
    "entitlements",
    "policy_packages",
)
ALL_SECTIONS: tuple[str, ...] = (*SECURITY_SECTIONS, "catalog")


class ControlError(Exception):
    """控制面域错误基类。"""


class SnapshotRejected(ControlError):
    """快照验签失败/格式非法/版本回退/安全节缺失。"""


class ControlUnavailable(ControlError):
    """控制面不可达或未配置(非致命:last-known 继续运行)。"""


class ControlAuthError(ControlError):
    """服务身份被控制面拒绝(401/403):停止本轮,退避后重试。"""


class SecurityContextMissing(ControlError):
    """managed 模式下无任何可用安全类快照:新受保护操作必须拒绝。"""


class CommandRejected(ControlError):
    """命令 action/版本/参数不合法,拒绝执行并回执 failed。"""


class EntitlementMissing(ControlError):
    """managed 模式下租户无有效权益(缺失即 fail-closed)。"""


class TenantResolutionError(ControlError):
    """本地 tenant_id 无法解析为 tenant_key(作用域匹配失败即 fail-closed)。"""


@dataclass(frozen=True)
class TenantRecord:
    tenant_id: str
    tenant_key: str
    tenant_name: str
    status: str = "ACTIVE"


@dataclass(frozen=True)
class MembershipRecord:
    """一条成员关系:用户资料 + 租户 + 粗角色键(同一用户可多租户多条)。"""

    user_id: str
    username: str
    nickname: str
    email: str | None
    status: str
    tenant_key: str
    coarse_role_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class CoarseRoleRecord:
    role_key: str
    role_name: str


@dataclass(frozen=True)
class EntitlementRecord:
    """租户权益;执行匹配按 tenant_key,features 仅展示不推断功能开关。"""

    tenant_id: str
    tenant_key: str
    status: str = "ACTIVE"
    features: tuple[str, ...] = ()
    entitlement_keys: tuple[str, ...] = ()
    quota_calls_daily: int = 0
    quota_calls_monthly: int = 0
    quota_bot_calls_daily: int = 0
    quota_tool_calls_per_minute: int = 0


@dataclass(frozen=True)
class PolicyPackageRecord:
    package_key: str
    version: int
    status: str
    rule_names: tuple[str, ...]
    tenant_keys: tuple[str, ...] = ("*",)
    # 仅随快照展示;执行作用域只按 tenant_keys,本地/快照 UUID 不保证一致
    tenant_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class CatalogEntryRecord:
    bot_key: str
    listing: str
    visibility: str = "TENANTS"
    tenant_keys: tuple[str, ...] = ("*",)
    entitlement_key: str | None = None


@dataclass(frozen=True)
class SnapshotSections:
    tenants: tuple[TenantRecord, ...] = ()
    members: tuple[MembershipRecord, ...] = ()
    coarse_roles: tuple[CoarseRoleRecord, ...] = ()
    entitlements: tuple[EntitlementRecord, ...] = ()
    policy_packages: tuple[PolicyPackageRecord, ...] = ()
    catalog: tuple[CatalogEntryRecord, ...] = ()

    def digest(self) -> str:
        """各节条目数摘要(只进日志/状态,不参与验签)。"""
        return (
            f"tenants={len(self.tenants)},members={len(self.members)},"
            f"roles={len(self.coarse_roles)},entitlements={len(self.entitlements)},"
            f"packages={len(self.policy_packages)},catalog={len(self.catalog)}"
        )


@dataclass(frozen=True)
class ControlSnapshot:
    service_id: str
    version: int
    observed_at: datetime
    sections: SnapshotSections
    etag: str = ""
    signature: str = ""


@dataclass(frozen=True)
class SnapshotView:
    """快照读取视图:安全类来自最近有效快照,带观察时刻与 stale 标记。"""

    present: bool
    version: int = 0
    observed_at: datetime | None = None
    stale: bool = True
    sections: SnapshotSections = field(default_factory=SnapshotSections)


@dataclass(frozen=True)
class FetchResult:
    not_modified: bool
    etag: str = ""
    payload: dict[str, object] | None = None
    raw: bytes = b""
    signature: str = ""
    key_id: str = ""


@dataclass(frozen=True)
class CommandEnvelope:
    command_id: str
    idempotency_key: str
    action: str
    action_version: int
    params: dict[str, object]
    reason: str = ""
    actor: str = ""
    created_at: datetime | None = None


@dataclass(frozen=True)
class CommandOutcome:
    status: Literal["SUCCEEDED", "FAILED", "REJECTED"]
    result: dict[str, object] = field(default_factory=dict[str, object])
    error: str = ""


@dataclass(frozen=True)
class ProductEvent:
    event_id: str
    event_type: str
    occurred_at: datetime
    tenant_id: str
    user_id: str | None = None
    bot_key: str | None = None
    attributes: dict[str, object] = field(default_factory=dict[str, object])


@dataclass(frozen=True)
class HealthStatus:
    ready: bool
    database: bool
    redis: bool
    detail: str = ""


@dataclass(frozen=True)
class RegistrationState:
    service_id: str
    instance_id: str
    version: str
    capabilities: tuple[str, ...]
    last_registered_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
