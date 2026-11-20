"""bot 组合根:公开面速率闸、agent 客户端解析与服务装配入口。

服务构造在 bot.service_factory;本模块按函数名转发,既有消费方
(bot/store 路由、trigger/mcp 回调)的 import 路径与调用方式保持不变。
"""

from typing import Any

from kongming.chat.costgate import ChatCostGate
from kongming.config import get_settings
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


def get_bot_service(*args: Any, **kwargs: Any) -> Any:
    """转发到 bot.service_factory.get_bot_service(延迟导入避免环)。"""
    from kongming.bot.service_factory import get_bot_service as _build

    return _build(*args, **kwargs)


def build_bot_service_with_session(*args: Any, **kwargs: Any) -> Any:
    """转发到 bot.service_factory.build_bot_service_with_session(延迟导入避免环)。"""
    from kongming.bot.service_factory import build_bot_service_with_session as _build

    return _build(*args, **kwargs)
