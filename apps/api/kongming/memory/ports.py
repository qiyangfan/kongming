"""memory 端口(022):service 依赖反转。零框架纪律:仅 Protocol。"""

from typing import Any, Protocol


class Embedder(Protocol):
    """向量化(agent /internal/embedding 回调通道,009 单例复用)。"""

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class MemoryOperLogger(Protocol):
    """操作日志(auth 域 SqlOperLogRepo 适配,015 ip 链路自动覆盖)。"""

    async def add(
        self,
        user_id: str | None,  # 与 SqlOperLogRepo.add 逐字对齐(位置形态)
        action: str,
        target: str | None,
        detail: dict[str, object],
    ) -> None: ...


class MemoryRepo(Protocol):
    """user_memory 存取(仓储实现);全部操作 user_id+tenant_id 双过滤。"""

    async def commit(self) -> None: ...

    async def mark_dormant_lazy(self) -> None: ...

    async def recall(
        self, *, user_id: str, tenant_id: str, vector: list[float],
        team_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]: ...

    async def insert(
        self,
        *,
        user_id: str,
        tenant_id: str,
        content: str,
        category: str,
        confidence: float,
        source: str,
        vector: list[float] | None,
        team_id: str | None = None,
    ) -> str: ...

    async def exists_identical(self, *, user_id: str, tenant_id: str, content: str) -> bool: ...

    async def list_mine(self, *, user_id: str, tenant_id: str) -> list[dict[str, Any]]: ...

    async def update_mine(
        self,
        *,
        memory_id: str,
        user_id: str,
        tenant_id: str,
        content: str,
        category: str,
        vector: list[float] | None,
    ) -> bool: ...

    async def delete_mine(self, *, memory_id: str, user_id: str, tenant_id: str) -> bool: ...
