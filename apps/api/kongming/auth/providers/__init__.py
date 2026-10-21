"""provider 注册表(ADR-006):新增 provider = 一个插件文件 + 此处一行。

国内三家(企微/钉钉/飞书)按同一形态接入;路由与前端不随 provider 增减改动。
"""

from kongming.auth.ports import OAuthProvider
from kongming.auth.providers.dingtalk import DingTalkProvider
from kongming.auth.providers.feishu import FeishuProvider
from kongming.auth.providers.github import GitHubProvider
from kongming.auth.providers.google import GoogleProvider
from kongming.auth.providers.wecom import WecomProvider

# 显式宽型中间变量:dict 不变,具体 provider 元组推导直赋宽型会被 pyright 拒
_providers: tuple[OAuthProvider, ...] = (
    GitHubProvider(),
    GoogleProvider(),
    WecomProvider(),
    DingTalkProvider(),
    FeishuProvider(),
)

REGISTRY: dict[str, OAuthProvider] = {p.key: p for p in _providers}
