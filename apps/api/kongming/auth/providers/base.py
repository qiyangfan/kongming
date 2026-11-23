"""provider 插件基类:声明式端点与凭证读取。

联邦只有Keycloak一家(标准 OIDC),授权码换来的 id_token 由各插件自行验签与归一化,
协议差异止步于子类。
"""

from abc import ABC, abstractmethod

from kongming.config import Settings


class OAuthProviderBase(ABC):
    key: str
    display_name: str
    # OIDC provider:授权前预取发现文档,并在回调中校验 nonce
    oidc: bool = False
    # 是否允许按已验证 email 关联既有账号;Keycloak契约禁止(不同 IdP 间按 email 合并会错绑)
    link_by_verified_email: bool = False
    scope: str

    @abstractmethod
    def client_id(self, s: Settings) -> str | None: ...

    @abstractmethod
    def client_secret(self, s: Settings) -> str | None: ...

    @abstractmethod
    def redirect_uri(self, s: Settings) -> str: ...

    def configured(self, s: Settings) -> bool:
        return bool(s.keycloak_enabled and s.keycloak_issuer) and bool(
            self.client_id(s) and self.client_secret(s)
        )
