"""store 端口(033):存储反转,用例层零框架依赖(宪法 Article II)。"""

from typing import Any, Protocol


class StoreRepo(Protocol):
    async def publish(
        self, *, bot_id: str, user_id: str, greeting: str, suggestions: list[str]
    ) -> None: ...

    async def unpublish(self, *, bot_id: str) -> None: ...

    async def store_list(
        self, *, tenant_id: str, keyword: str, sort: str, user_id: str = ""
    ) -> list[dict[str, Any]]: ...

    async def store_get(self, *, tenant_id: str, bot_key: str) -> dict[str, Any] | None: ...

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
