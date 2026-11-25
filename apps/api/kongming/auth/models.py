"""auth 领域模型与领域错误:纯 dataclass,不依赖 Web 框架或数据库。

枚举值与 baseline CHECK 约束同源(字符串字面量,禁 ORM Enum/魔数)。
"""

from dataclasses import dataclass, field
from typing import Literal

UserStatus = Literal["ACTIVE", "DISABLED"]
ProviderKey = Literal["KEYCLOAK"]


@dataclass(frozen=True)
class User:
    id: str
    username: str
    email: str | None
    nickname: str
    status: UserStatus
    deleted: bool = False

    @property
    def active(self) -> bool:
        return self.status == "ACTIVE" and not self.deleted


@dataclass(frozen=True)
class Role:
    id: str
    role_key: str
    role_name: str
    data_scope: str
    sql_scope: str
    sort: int = 0


@dataclass(frozen=True)
class Tenant:
    id: str
    tenant_key: str
    tenant_name: str


@dataclass(frozen=True)
class TenantRef:
    tenant_key: str
    tenant_name: str


@dataclass(frozen=True)
class NormalizedProfile:
    """联邦 profile 归一化形态(ADR-006):协议差异止步于各 provider 插件。"""

    external_id: str
    username: str
    display_name: str
    email: str | None
    email_verified: bool
    avatar_url: str | None


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"  # noqa: S105 —— 固定字面量,非密钥


@dataclass(frozen=True)
class AuthenticatedUser:
    """已通过验签的访问令牌身份:仅含用户 ID,账号状态由相应数据库检查确认。"""

    user_id: str


@dataclass(frozen=True)
class RbacUser:
    """功能权限检查专用:账号有效性与启用角色授予的有效权限集合。"""

    user_id: str
    perm_codes: frozenset[str]


@dataclass(frozen=True)
class MeInfo:
    user_id: str
    username: str
    nickname: str
    role_keys: list[str] = field(default_factory=list[str])
    perm_codes: list[str] = field(default_factory=list[str])
    tenants: list[TenantRef] = field(default_factory=list[TenantRef])


# ---- 领域错误(router 统一映射状态码;message 即用户可见文案) ----


class AuthError(Exception):
    """auth 域错误基类。"""


class AccountDisabled(AuthError):
    """账号已禁用/软删。"""


class TokenInvalid(AuthError):
    """令牌无效/过期/已吊销/类型不符。"""


class StateInvalid(AuthError):
    """联邦 state 伪造/过期/已消费。"""


class ProviderNotFound(AuthError):
    """provider 未注册或未配置。"""


class ProviderExchangeFailed(AuthError):
    """联邦 code 交换/profile 拉取失败(provider 侧拒绝或异常)。"""
