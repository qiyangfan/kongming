"""bot 服务的仓储装配(DI 与非 DI 消费点共用)。

依赖模块(bot.dependencies)与路由经此取得服务实例,自身不碰仓储;
_resolve_agent_client 回指 bot.dependencies,保持 DI 覆写感知不变。
"""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.repository import SqlOperLogRepo, SqlPermRepo
from kongming.bot.adapters import (
    AgentReactRunner,
    BotGuardrailAdapter,
    McpDynamicToolLister,
    UsageSinkAdapter,
)
from kongming.bot.agent_resolver import resolve_agent_client as _resolve_agent_client
from kongming.bot.repository import SqlBotRepo
from kongming.bot.service import BotService
from kongming.chat.agent_client import AgentClient
from kongming.chat.dependencies import get_agent_client
from kongming.db import get_session


def get_bot_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    agent: Annotated[AgentClient, Depends(get_agent_client)],
) -> BotService:
    return BotService(
        SqlBotRepo(session),
        SqlPermRepo(session),
        AgentReactRunner(agent),
        BotGuardrailAdapter(),
        SqlOperLogRepo(session),  # 写操作审计
        McpDynamicToolLister(session),
        UsageSinkAdapter(),  # bot 用量请求内落库(独立短会话)
    )


def build_bot_service_for_agent(agent: AgentClient, session: AsyncSession) -> BotService:
    """给定 agent 客户端与会话构建 Bot 服务(调用方自持客户端,避免回指 chat 依赖)。"""
    return BotService(
        SqlBotRepo(session),
        SqlPermRepo(session),
        AgentReactRunner(agent),
        BotGuardrailAdapter(),
        SqlOperLogRepo(session),  # 写操作审计
        McpDynamicToolLister(session),
        UsageSinkAdapter(),  # bot 用量请求内落库(独立短会话)
    )


def build_bot_service_with_session(session: AsyncSession) -> BotService:
    """为 MCP 入站请求构建 Bot 服务,并复用标准依赖工厂的客户端解析。"""
    return BotService(
        SqlBotRepo(session),
        SqlPermRepo(session),
        AgentReactRunner(_resolve_agent_client()),
        BotGuardrailAdapter(),
        SqlOperLogRepo(session),  # 写操作审计
        McpDynamicToolLister(session),
        UsageSinkAdapter(),  # bot 用量请求内落库(独立短会话)
    )
