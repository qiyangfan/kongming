"""billing 组合根适配器:审计注入(请求会话,actor 可空=匿名回调场景)。"""

from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.repository import SqlOperLogRepo


class BillingOper:
    def __init__(self, session: AsyncSession, actor: str | None) -> None:
        self._r = SqlOperLogRepo(session)
        self._a = actor

    async def log(self, *, action: str, target: str, detail: dict[str, object]) -> None:
        await self._r.add(self._a, action, target, detail)
