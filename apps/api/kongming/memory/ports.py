"""memory 端口:service 依赖反转。零框架纪律:仅 Protocol。"""

from typing import Any, Protocol


class Embedder(Protocol):
    """向量化(agent /internal/embedding 回调通道,单例复用)。"""

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class MemoryOperLogger(Protocol):
    """操作日志端口，由认证域仓储实现；客户端 IP 从请求上下文读取。"""

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

    async def mark_dormant_lazy(self, *, user_id: str, tenant_id: str) -> None: ...

    async def recall(
        self,
        *,
        user_id: str,
        tenant_id: str,
        vector: list[float],
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

    async def get_prefs(self, *, user_id: str, tenant_id: str) -> dict[str, object]: ...

    async def read_prefs_revision(
        self, *, user_id: str, tenant_id: str
    ) -> tuple[dict[str, object], int]: ...

    async def merge_prefs_manual(
        self,
        *,
        user_id: str,
        tenant_id: str,
        values: dict[str, object],
        deletes: list[str],
    ) -> tuple[dict[str, object], int]: ...

    async def clear_prefs_manual(
        self, *, user_id: str, tenant_id: str
    ) -> tuple[dict[str, object], int]: ...

    async def fill_prefs_if_revision(
        self, *, user_id: str, tenant_id: str, values: dict[str, object], base_revision: int
    ) -> bool: ...
