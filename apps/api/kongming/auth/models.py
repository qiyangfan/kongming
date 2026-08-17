"""auth 领域模型与领域错误:纯 dataclass,零框架依赖(宪法 Article II)。

枚举值与 baseline CHECK 约束同源(字符串字面量,禁 ORM Enum/魔数)。
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

UserStatus = Literal["ACTIVE", "DISABLED"]
ProviderKey = Literal["GITHUB", "GOOGLE", "WECOM", "DINGTALK", "FEISHU"]


@dataclass(frozen=True)
class User:
    id: str
    username: str
    email: str | None
    password_hash: str | None  # None = 纯联邦账号(无本地密码)
    nickname: str
    status: UserStatus
    password_changed_at: datetime | None
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
    must_change_password: bool
    token_type: str = "Bearer"  # noqa: S105 —— 固定字面量,非密钥


@dataclass(frozen=True)
class MeInfo:
    user_id: str
    username: str
    nickname: str
    role_keys: list[str] = field(default_factory=list)
    perm_codes: list[str] = field(default_factory=list)
    tenants: list[TenantRef] = field(default_factory=list)
    must_change_password: bool = False


# ---- 领域错误(router 统一映射状态码;message 即用户可见文案) ----


class AuthError(Exception):
    """auth 域错误基类。"""


class LoginFailed(AuthError):
    """凭证错误(用户名不存在与密码错误同形,防枚举)。"""


class AccountDisabled(AuthError):
    """账号已禁用/软删(密码校验通过后才可暴露)。"""


class AccountLocked(AuthError):
    """连续失败触发锁定;retry_after_sec 供提示与 429 头。"""

    def __init__(self, retry_after_sec: int) -> None:
        super().__init__(f"账号已锁定,请 {max(1, (retry_after_sec + 59) // 60)} 分钟后重试")
        self.retry_after_sec = retry_after_sec


class TokenInvalid(AuthError):
    """令牌无效/过期/已吊销/类型不符。"""


class StateInvalid(AuthError):
    """联邦 state 伪造/过期/已消费。"""


class ProviderNotFound(AuthError):
    """provider 未注册或未配置。"""


class CurrentPasswordWrong(AuthError):
    """自助改密:当前密码校验失败。"""


class NoLocalPassword(AuthError):
    """纯联邦账号无本地密码,不可自助改密。"""
