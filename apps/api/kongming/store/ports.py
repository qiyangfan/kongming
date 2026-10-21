"""store 存储端口,供用例层通过接口依赖持久化能力。"""

from typing import Any, Protocol


class StoreRepo(Protocol):
    async def current_version(self, *, bot_id: str) -> dict[str, Any] | None:
        """当前上架版本(id/version_no/snapshot/published_at);未上架 None。"""
        ...

    async def create_version(
        self,
        *,
        bot_id: str,
        tenant_id: str,
        snapshot: dict[str, object],
        user_id: str,
    ) -> tuple[str, int] | None:
        """创建不可变版本快照(单句原子 MAX+1),返回 (版本 id, 版本号);
        None=并发上架撞 version_no(调用方重读重试)。"""
        ...

    async def point_current(
        self, *, bot_id: str, version_id: str, user_id: str, restore_shelf: bool = True
    ) -> None:
        """切换当前上架版本指针(纯指针语义,版本无独立状态机)。
        restore_shelf=True(上架路径)恢复货架标记;False(回滚路径)仅动
        指针——下架态回滚不得复活货架(spec)。"""
        ...

    async def evict_overflow_versions(self, *, bot_id: str, cap: int) -> int:
        """封顶淘汰:非当前版本超上限时淘汰最旧(当前版本恒受保护)。"""
        ...

    async def version_by_no(self, *, bot_id: str, version_no: int) -> dict[str, Any] | None: ...

    async def list_versions(self, *, bot_id: str) -> list[dict[str, Any]]: ...

    async def unpublish(self, *, bot_id: str) -> None: ...

    async def store_list(
        self, *, tenant_id: str, keyword: str, sort: str, user_id: str = ""
    ) -> list[dict[str, Any]]: ...

    async def store_get(
        self, *, tenant_id: str, bot_key: str, user_id: str = ""
    ) -> dict[str, Any] | None: ...

    async def set_favorite(
        self,
        *,
        tenant_id: str,
        user_id: str,
        bot_id: str,
        favorited: bool,
    ) -> bool: ...

    async def my_favorites(self, *, tenant_id: str, user_id: str) -> list[dict[str, Any]]: ...

    async def upsert_rating(
        self, *, tenant_id: str, user_id: str, bot_id: str, score: int
    ) -> None: ...

    async def my_rating(self, *, user_id: str, bot_id: str) -> int | None: ...

    async def incr_use_count(self, *, bot_id: str) -> None: ...

    async def commit(self) -> None: ...
