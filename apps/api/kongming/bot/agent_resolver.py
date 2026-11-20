"""DI 覆写感知的 agent 客户端解析入口。

单独成模块以避免 bot.dependencies 与 bot.service_factory 互相引用形成环:
service_factory 需要本函数,bot.dependencies 再做一次转导出保持既有 import 路径。
"""

from typing import cast

from kongming.chat.agent_client import AgentClient


def resolve_agent_client() -> AgentClient:
    """解析 agent 客户端:主 app 的 dependency_overrides 存在覆写时走覆写,否则直调工厂。"""
    from kongming.app_registry import get_bound_app
    from kongming.chat.dependencies import get_agent_client

    override = get_bound_app().dependency_overrides.get(get_agent_client)
    if override is not None:
        return cast("AgentClient", override())
    return get_agent_client()
