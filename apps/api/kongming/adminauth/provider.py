"""管理台专用 Keycloak 客户端。

与用户 `kongming-web` 分离:独立 client id/secret/回调/cookie;
登录要求 Keycloak 粗角色 `kongming-admin`,细权限仍由本地 RBAC 判定。
"""

from kongming.auth.providers.keycloak import KeycloakProvider
from kongming.config import Settings


class AdminKeycloakProvider(KeycloakProvider):
    key = "keycloak"
    display_name = "统一身份(管理台)"

    def client_id(self, s: Settings) -> str | None:
        return s.admin_keycloak_client_id or None

    def client_secret(self, s: Settings) -> str | None:
        return s.admin_keycloak_client_secret or None

    def redirect_uri(self, s: Settings) -> str:
        return s.admin_keycloak_redirect_uri

    def issuer(self, s: Settings) -> str | None:
        """管理台复用同一 realm(KM_KEYCLOAK_ISSUER);client 独立。"""
        return s.keycloak_issuer

    def post_logout_uri(self, s: Settings) -> str:
        return s.admin_keycloak_post_logout_redirect_uri

    def enabled(self, s: Settings) -> bool:
        return s.keycloak_enabled

    def required_role(self, s: Settings) -> str | None:
        """管理台粗角色按部署配置校验;构造参数优先(测试/特殊部署)。"""
        return self._required_role or s.admin_keycloak_required_role or None

    def role_client_id(self, s: Settings) -> str | None:
        return self._role_client_id or s.admin_keycloak_client_id or None

    def configured(self, s: Settings) -> bool:
        return bool(
            self.enabled(s) and self.issuer(s) and self.client_id(s) and self.client_secret(s)
        )
