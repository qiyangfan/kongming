"""store 存储(033):商店发现/收藏/评分/使用量。"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.like import like_pattern
from kongming.uuid7 import uuid7

_SORT_SQL = {
    "hot": "b.use_count DESC, b.published_at DESC",
    "new": "b.published_at DESC",
    "rating": "avg_score DESC NULLS LAST, rating_count DESC, b.published_at DESC",
}


class SqlStoreRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def publish(
        self, *, bot_id: str, user_id: str, greeting: str, suggestions: list[str]
    ) -> None:
        import json

        await self._s.execute(
            text(
                "UPDATE public.sys_bot_definition"
                " SET published_at=COALESCE(published_at, now()),"
                " published_by=CAST(:u AS uuid),"
                " greeting=:g, suggestions=CAST(:s AS jsonb)"
                " WHERE id=CAST(:i AS uuid)"
            ),
            {
                "u": user_id,
                "i": bot_id,
                "g": greeting[:500],
                "s": json.dumps(suggestions, ensure_ascii=False),
            },
        )

    async def unpublish(self, *, bot_id: str) -> None:
        await self._s.execute(
            text(
                "UPDATE public.sys_bot_definition"
                " SET published_at=NULL, published_by=NULL"
                " WHERE id=CAST(:i AS uuid)"
            ),
            {"i": bot_id},
        )

    async def store_list(
        self, *, tenant_id: str, keyword: str, sort: str, user_id: str = ""
    ) -> list[dict[str, Any]]:
        order = _SORT_SQL.get(sort, _SORT_SQL["new"])
        params: dict[str, Any] = {"t": tenant_id, "fu": user_id}
        base = (
            "SELECT b.*,"
            " AVG(r.score) AS avg_score,"
            " COUNT(r.id) AS rating_count,"
            " (SELECT count(*) FROM public.sys_bot_favorite f"
            "  WHERE f.bot_id=b.id) AS fav_count,"
            " EXISTS(SELECT 1 FROM public.sys_bot_favorite f2"
            "        WHERE f2.bot_id=b.id AND f2.user_id=CAST(:fu AS uuid)) AS favorited"
            " FROM public.sys_bot_definition b"
            " LEFT JOIN public.sys_bot_rating r ON r.bot_id=b.id"
            " WHERE b.tenant_id=CAST(:t AS uuid)"
            " AND b.published_at IS NOT NULL AND b.enabled"
        )
        if keyword:
            # J2:ILIKE 元字符转义(007 R1-3/014 防线;搜「_」不命中全表)
            base += (
                " AND (b.name ILIKE :kw ESCAPE '\\' OR b.bot_key ILIKE :kw ESCAPE '\\'"
                " OR b.system_prompt ILIKE :kw ESCAPE '\\')"
            )
            params["kw"] = like_pattern(keyword)
        # S608 说明:base 为两常量分支拼接,_SORT_SQL 为常量枚举,值全绑定
        rows = (
            await self._s.execute(
                text(base + f" GROUP BY b.id ORDER BY {order}"),  # noqa: S608
                params,
            )
        ).mappings().all()
        return [dict(r) for r in rows]

    async def store_get(self, *, tenant_id: str, bot_key: str) -> dict[str, Any] | None:
        row = (
            await self._s.execute(
                text(
                    "SELECT b.*,"
                    " AVG(r.score) AS avg_score,"
                    " COUNT(r.id) AS rating_count,"
                    " (SELECT count(*) FROM public.sys_bot_favorite f"
                    "  WHERE f.bot_id=b.id) AS fav_count"
                    " FROM public.sys_bot_definition b"
                    " LEFT JOIN public.sys_bot_rating r ON r.bot_id=b.id"
                    " WHERE b.tenant_id=CAST(:t AS uuid) AND b.bot_key=:k"
                    " AND b.published_at IS NOT NULL AND b.enabled"
                    " GROUP BY b.id"
                ),
                {"t": tenant_id, "k": bot_key},
            )
        ).mappings().first()
        return dict(row) if row else None

    # ── 收藏 ──

    async def set_favorite(
        self, *, tenant_id: str, user_id: str, bot_id: str, favorited: bool
    ) -> bool:
        """幂等收藏/取消(spec「重复操作幂等」)——审查循环 F6:原 toggle
        切换语义下网络重试/双击同一 POST 会把已收藏反选(HTTP POST 非幂等,
        违约);改为显式 set:add=INSERT ON CONFLICT DO NOTHING,remove=DELETE,
        两者天然幂等,无需 036⑥ 的 advisory lock 串行(无读-判-写交错)。"""
        if favorited:
            await self._s.execute(
                text(
                    "INSERT INTO public.sys_bot_favorite"
                    " (id, tenant_id, user_id, bot_id)"
                    " VALUES (:i, CAST(:t AS uuid), CAST(:u AS uuid), CAST(:b AS uuid))"
                    " ON CONFLICT (user_id, bot_id) DO NOTHING"
                ),
                {"i": str(uuid7()), "t": tenant_id, "u": user_id, "b": bot_id},
            )
            return True
        await self._s.execute(
            text(
                "DELETE FROM public.sys_bot_favorite"
                " WHERE user_id=CAST(:u AS uuid) AND bot_id=CAST(:b AS uuid)"
            ),
            {"u": user_id, "b": bot_id},
        )
        return False

    async def my_favorites(self, *, tenant_id: str, user_id: str) -> list[dict[str, Any]]:
        rows = (
            await self._s.execute(
                text(
                    "SELECT b.id, b.bot_key, b.name, b.greeting, b.use_count,"
                    " COALESCE(AVG(r.score), 0) AS avg_score, COUNT(r.id) AS rating_count"
                    " FROM public.sys_bot_favorite f"
                    " JOIN public.sys_bot_definition b ON b.id=f.bot_id"
                    " LEFT JOIN public.sys_bot_rating r ON r.bot_id=b.id"
                    " WHERE f.tenant_id=CAST(:t AS uuid) AND f.user_id=CAST(:u AS uuid)"
                    " AND b.published_at IS NOT NULL AND b.enabled"
                    " GROUP BY b.id ORDER BY MAX(f.created_at) DESC"
                ),
                {"t": tenant_id, "u": user_id},
            )
        ).mappings().all()
        return [dict(r) for r in rows]

    # ── 评分 ──

    async def upsert_rating(
        self, *, tenant_id: str, user_id: str, bot_id: str, score: int
    ) -> None:
        await self._s.execute(
            text(
                "INSERT INTO public.sys_bot_rating"
                " (id, tenant_id, user_id, bot_id, score)"
                " VALUES (:i, CAST(:t AS uuid), CAST(:u AS uuid), CAST(:b AS uuid), :s)"
                " ON CONFLICT (user_id, bot_id)"
                " DO UPDATE SET score=EXCLUDED.score, updated_at=now()"
            ),
            {"i": str(uuid7()), "t": tenant_id, "u": user_id, "b": bot_id, "s": score},
        )

    async def my_rating(self, *, user_id: str, bot_id: str) -> int | None:
        return (
            await self._s.execute(
                text(
                    "SELECT score FROM public.sys_bot_rating"
                    " WHERE user_id=CAST(:u AS uuid) AND bot_id=CAST(:b AS uuid)"
                ),
                {"u": user_id, "b": bot_id},
            )
        ).scalar_one_or_none()

    # ── 使用量 ──

    async def incr_use_count(self, *, bot_id: str) -> None:
        await self._s.execute(
            text(
                "UPDATE public.sys_bot_definition SET use_count=use_count+1"
                " WHERE id=CAST(:i AS uuid)"
            ),
            {"i": bot_id},
        )

    async def commit(self) -> None:
        await self._s.commit()
