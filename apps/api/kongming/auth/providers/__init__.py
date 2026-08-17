"""provider 注册表(ADR-006):新增 provider = 一个插件文件 + 此处一行。

003 国内三家(企微/钉钉/飞书)按同一形态接入;路由与前端不随 provider 增减改动。
"""

from kongming.auth.ports import OAuthProvider
from kongming.auth.providers.github import GitHubProvider
from kongming.auth.providers.google import GoogleProvider

REGISTRY: dict[str, OAuthProvider] = {
    p.key: p for p in (GitHubProvider(), GoogleProvider())
}
