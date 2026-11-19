"""task 组合根适配器:bot 域运行时解析 → CollabBotResolver 端口。"""

from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession


class CollabBotResolver:
    """协作 Bot 运行时解析适配(沿用 bot 域运行时口径:租户内存在
    + 启用 + 创建者用户 ACTIVE;人格与工具子集原样带出,工具交集在执行面收)。"""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def resolve(self, *, tenant_id: str, bot_key: str) -> dict[str, object] | None:
        from kongming.bot.repository import SqlBotRepo

        repo = SqlBotRepo(self._s)
        row = await repo.get_by_key(tenant_id=tenant_id, bot_key=bot_key)
        if row is None or not bool(row.get("enabled")):
            return None
        creator = str(row.get("created_by") or "")
        if creator and await repo.find_user_status(creator) != "ACTIVE":
            return None
        tools_raw = row.get("tools")
        tools = (
            [str(t) for t in cast("list[object]", tools_raw)] if isinstance(tools_raw, list) else []
        )
        return {
            "bot_key": bot_key,
            "system_prompt": str(row.get("system_prompt") or ""),
            "tools": tools,
        }
