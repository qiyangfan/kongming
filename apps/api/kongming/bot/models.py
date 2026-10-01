"""bot 领域模型(025):Bot 定义与校验纯函数(零框架)。"""

import re
from collections.abc import Collection
from dataclasses import dataclass, field

from kongming.toolcall.registry import effective

BOT_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


@dataclass(frozen=True)
class BotDef:
    id: str
    tenant_id: str
    bot_key: str
    name: str
    system_prompt: str
    tools: list[str] = field(default_factory=list[str])
    datasource_key: str = "default"
    model: str | None = None
    enabled: bool = True
    is_public: bool = False
    created_by: str = ""


class InvalidBotKey(Exception):
    pass


class ToolSubsetViolation(Exception):
    """工具子集越权(超出创建者权限对应工具集)。"""


def tools_for_perms(perm_codes: list[str], mcp_names: Collection[str] = ()) -> set[str]:
    """权限点集 → 可用工具名集(注册表反查)。
    028:mcp_names=租户已注册 MCP 工具全名集(调用方供);持 tool:mcp:use
    才并入口径(与执行面 L2 同判据)。"""
    names = {d.name for d in effective().values() if d.perm_code in set(perm_codes)}
    if "tool:mcp:use" in set(perm_codes):
        names |= set(mcp_names)
    return names


def validate_tool_subset(
    bot_tools: list[str],
    allowed_tools: set[str],
    registered_names: Collection[str] | None = None,
) -> None:
    """工具子集 ⊆ 创建者权限工具集(spec「越权绑定拒绝」);
    未注册名同拒(防幽灵工具)。028:registered=静态注册表 ∪ MCP 快照名集
    (缺省纯静态,025 既有调用点兼容)。"""
    registered = set(registered_names) if registered_names is not None else set(effective().keys())
    unknown = [t for t in bot_tools if t not in registered]
    if unknown:
        raise ToolSubsetViolation(f"未注册工具: {unknown}")
    overflow = [t for t in bot_tools if t not in allowed_tools]
    if overflow:
        raise ToolSubsetViolation(f"越权工具: {overflow}")
