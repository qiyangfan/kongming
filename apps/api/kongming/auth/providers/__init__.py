"""provider 注册表:孔明只接Keycloak统一身份(ADR-0010),不再注册本地社交 provider。

注册表保留「键 → 插件」形态,回调与路由按 provider 键取用。
"""

from kongming.auth.ports import OAuthProvider
from kongming.auth.providers.keycloak import KeycloakProvider

# 显式宽型中间变量:dict 不变,具体 provider 元组推导直赋宽型会被 pyright 拒
_providers: tuple[OAuthProvider, ...] = (KeycloakProvider(),)

REGISTRY: dict[str, OAuthProvider] = {p.key: p for p in _providers}
