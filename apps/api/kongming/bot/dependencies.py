"""bot 组合根:服务工厂(DI 与非 DI 消费点共用)与公开面速率闸。"""

from typing import Annotated, cast

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.repository import SqlOperLogRepo, SqlPermRepo
from kongming.bot.adapters import (
    AgentReactRunner,
    BotGuardrailAdapter,
    McpDynamicToolLister,
    UsageSinkAdapter,
)
from kongming.bot.service import BotService
from kongming.chat.agent_client import AgentClient
from kongming.chat.costgate import ChatCostGate
from kongming.chat.dependencies import get_agent_client
from kongming.config import get_settings
from kongming.db import get_session
from kongming.redis_client import get_redis
from kongming.toolcall.ratelimit import RedisRateLimiter


def build_bot_rate_gate() -> ChatCostGate:
    """公开嵌入面速率闸(仅 (bot_key, ip) 分钟级;无用户/租户维度),
    复用 chat 域零框架闸模块(单一 fail-open 口径,不复制限流逻辑)。"""
    settings = get_settings()
    return ChatCostGate(
        limiter=RedisRateLimiter(get_redis()),
        bot_rate_per_minute=settings.bot_chat_rate_per_minute,
    )


def get_bot_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    agent: Annotated[AgentClient, Depends(get_agent_client)],
) -> BotService:
    from kongming.bot.repository import SqlBotRepo

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
    """为 MCP 入站请求构建 Bot 服务，并复用标准依赖工厂的客户端解析。"""
    from kongming.bot.repository import SqlBotRepo

    return BotService(
        SqlBotRepo(session),
        SqlPermRepo(session),
        AgentReactRunner(_resolve_agent_client()),
        BotGuardrailAdapter(),
        SqlOperLogRepo(session),  # 写操作审计
        McpDynamicToolLister(session),
        UsageSinkAdapter(),  # bot 用量请求内落库(独立短会话)
    )


def _resolve_agent_client() -> AgentClient:
    """DI 覆写感知的 agent 客户端解析(非 DI 消费点唯一入口):主 app 的
    dependency_overrides 存在覆写时走覆写(测试替身),否则直调工厂。"""
    from kongming.app_registry import get_bound_app
    from kongming.chat.dependencies import get_agent_client

    override = get_bound_app().dependency_overrides.get(get_agent_client)
    if override is not None:
        return cast("AgentClient", override())
    return get_agent_client()
