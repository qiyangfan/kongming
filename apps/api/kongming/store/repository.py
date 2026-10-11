"""store 存储(033;104 版本化):商店发现/收藏/评分/使用量 + 上架版本快照。

104 版本化语义:商店列表/详情/收藏/直跑一律消费「当前上架版本」快照
(sys_bot_version,经 current_version_id 指针 join);定义行只服务草稿态
与既有渠道。快照读侧定型键集见 kongming.store.snapshot(单源)。
"""

import json
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

# 收藏态/计数子查询单源(别名统一 f2;store_list/store_get 上下文 f 空闲,
# my_favorites 的 f 被主查询占用——三处共用,防改一漏二)
_FAV_COUNT_SQL = (
    " (SELECT count(*) FROM public.sys_bot_favorite f2  WHERE f2.bot_id=b.id) AS fav_count"
)
_FAVORITED_SQL = (
    " EXISTS(SELECT 1 FROM public.sys_bot_favorite f2"
    "        WHERE f2.bot_id=b.id"
    "        AND f2.user_id=CAST(NULLIF(:fu,'') AS uuid)) AS favorited"
)

# 快照消费 join(104):货架语义=有当前上架版本;未指向版本的在架行
# (迁移不变量外)不出现在商店——fail-safe 不裸奔草稿内容
_SHELF_FROM_SQL = (
    " FROM public.sys_bot_definition b"
    " JOIN public.sys_bot_version v ON v.id=b.current_version_id"
    " LEFT JOIN public.sys_bot_rating r ON r.bot_id=b.id"
)
_SHELF_WHERE_SQL = (
    " WHERE b.tenant_id=CAST(:t AS uuid) AND b.published_at IS NOT NULL AND b.enabled"
)


class SqlStoreRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    # ── 上架版本(104) ──

    async def current_version(self, *, bot_id: str) -> dict[str, Any] | None:
        row = (
            (
                await self._s.execute(
                    text(
                        "SELECT v.id, v.version_no, v.snapshot, v.published_at"
                        " FROM public.sys_bot_definition b"
                        " JOIN public.sys_bot_version v ON v.id=b.current_version_id"
                        " WHERE b.id=CAST(:i AS uuid)"
                    ),
                    {"i": bot_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        return dict(row) if row else None

    async def next_version_no(self, *, bot_id: str) -> int:
        return int(
            (
                await self._s.execute(
                    text(
                        "SELECT COALESCE(MAX(version_no), 0) + 1"
                        " FROM public.sys_bot_version WHERE bot_id=CAST(:i AS uuid)"
                    ),
                    {"i": bot_id},
                )
            ).scalar_one()
        )

    async def create_version(
        self,
        *,
        bot_id: str,
        tenant_id: str,
        version_no: int,
        snapshot: dict[str, object],
        user_id: str,
    ) -> str:
        vid = str(uuid7())
        await self._s.execute(
            text(
                "INSERT INTO public.sys_bot_version"
                " (id, tenant_id, bot_id, version_no, snapshot, published_by)"
                " VALUES (:i, CAST(:t AS uuid), CAST(:b AS uuid), :n,"
                "         CAST(:s AS jsonb), CAST(:u AS uuid))"
            ),
            {
                "i": vid,
                "t": tenant_id,
                "b": bot_id,
                "n": version_no,
                "s": json.dumps(snapshot, ensure_ascii=False),
                "u": user_id,
            },
        )
        return vid

    async def point_current(
        self, *, bot_id: str, version_id: str, user_id: str, restore_shelf: bool = True
    ) -> None:
        """指针切换;上架路径(restore_shelf=True)恢复货架标记:
        published_at=首次上架时间(排序/展示口径不变)、published_by=最近
        上架人(幂等复用路径同样恢复,支撑下架后同内容再上架);
        回滚路径(restore_shelf=False)仅动指针——下架态不复活货架。"""
        shelf_sql = (
            ", published_at=COALESCE(published_at, now()), published_by=CAST(:u AS uuid)"
            if restore_shelf
            else ""
        )
        await self._s.execute(
            text(
                "UPDATE public.sys_bot_definition"  # noqa: S608  shelf_sql 两常量分支,值全绑定
                " SET current_version_id=CAST(:v AS uuid)"
                + shelf_sql
                + " WHERE id=CAST(:i AS uuid)"
            ),
            {"v": version_id, "u": user_id, "i": bot_id},
        )

    async def evict_overflow_versions(self, *, bot_id: str, cap: int) -> int:
        """封顶淘汰:保留最新 cap 个「非当前」版本,更旧者淘汰;
        当前版本恒不受淘汰(指针悬空=货架断供,禁止)。"""
        zero = "00000000-0000-0000-0000-000000000000"
        cur = (
            "COALESCE((SELECT current_version_id FROM public.sys_bot_definition"  # noqa: S608  常量段
            f" WHERE id=CAST(:i AS uuid)), '{zero}'::uuid)"
        )
        rows = (
            await self._s.execute(
                text(
                    "DELETE FROM public.sys_bot_version"  # noqa: S608  cur 常量段,值全绑定
                    " WHERE bot_id=CAST(:i AS uuid)"
                    f" AND id <> {cur}"
                    " AND version_no <= ("
                    "   SELECT version_no FROM public.sys_bot_version"
                    "   WHERE bot_id=CAST(:i AS uuid)"
                    f"   AND id <> {cur}"
                    "   ORDER BY version_no DESC"
                    "   OFFSET :cap LIMIT 1"
                    ") RETURNING id"
                ),
                {"i": bot_id, "cap": cap},
            )
        ).all()
        return len(rows)

    async def version_by_no(self, *, bot_id: str, version_no: int) -> dict[str, Any] | None:
        row = (
            (
                await self._s.execute(
                    text(
                        "SELECT id, version_no, snapshot, published_at"
                        " FROM public.sys_bot_version"
                        " WHERE bot_id=CAST(:i AS uuid) AND version_no=:n"
                    ),
                    {"i": bot_id, "n": version_no},
                )
            )
            .mappings()
            .one_or_none()
        )
        return dict(row) if row else None

    async def list_versions(self, *, bot_id: str) -> list[dict[str, Any]]:
        rows = (
            (
                await self._s.execute(
                    text(
                        "SELECT v.version_no, v.snapshot, v.published_at,"
                        " (v.id=b.current_version_id) AS is_current"
                        " FROM public.sys_bot_version v"
                        " JOIN public.sys_bot_definition b ON b.id=v.bot_id"
                        " WHERE v.bot_id=CAST(:i AS uuid)"
                        " ORDER BY v.version_no DESC"
                    ),
                    {"i": bot_id},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def unpublish(self, *, bot_id: str) -> None:
        """下架=撤货架标记(published_at/published_by);版本历史与指针保留
        (104:下架态回滚仅恢复指针,历史不因下架丢失)。"""
        await self._s.execute(
            text(
                "UPDATE public.sys_bot_definition"
                " SET published_at=NULL, published_by=NULL"
                " WHERE id=CAST(:i AS uuid)"
            ),
            {"i": bot_id},
        )

    # ── 发现(消费当前上架版本快照,104) ──

    async def store_list(
        self, *, tenant_id: str, keyword: str, sort: str, user_id: str = ""
    ) -> list[dict[str, Any]]:
        order = _SORT_SQL.get(sort, _SORT_SQL["new"])
        params: dict[str, Any] = {"t": tenant_id, "fu": user_id}
        base = (
            "SELECT b.id, b.bot_key, b.use_count, b.published_at,"
            " v.snapshot->>'name' AS name,"
            " v.snapshot->>'greeting' AS greeting,"
            " AVG(r.score) AS avg_score,"
            " COUNT(r.id) AS rating_count,"
            + _FAV_COUNT_SQL
            + ", "
            + _FAVORITED_SQL
            + _SHELF_FROM_SQL
            + _SHELF_WHERE_SQL
        )
        if keyword:
            # J2:ILIKE 元字符转义(007 R1-3/014 防线;搜「_」不命中全表);
            # 104:搜索对象=快照名/系统提示(货架内容),bot_key 为不可变标识
            base += (
                " AND (v.snapshot->>'name' ILIKE :kw ESCAPE '\\'"
                " OR b.bot_key ILIKE :kw ESCAPE '\\'"
                " OR v.snapshot->>'system_prompt' ILIKE :kw ESCAPE '\\')"
            )
            params["kw"] = like_pattern(keyword)
        # S608 说明:base 为两常量分支拼接,_SORT_SQL 为常量枚举,值全绑定
        rows = (
            (
                await self._s.execute(
                    text(base + " GROUP BY b.id, v.id" + f" ORDER BY {order}"),  # noqa: S608
                    params,
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def store_get(
        self, *, tenant_id: str, bot_key: str, user_id: str = ""
    ) -> dict[str, Any] | None:
        row = (
            (
                await self._s.execute(
                    text(
                        "SELECT b.id, b.bot_key, b.use_count, b.published_at,"
                        " v.version_no,"
                        " v.published_at AS version_published_at,"
                        " v.snapshot->>'name' AS name,"
                        " v.snapshot->>'greeting' AS greeting,"
                        " v.snapshot->'suggestions' AS suggestions,"
                        " v.snapshot->'tools' AS tools,"
                        " v.snapshot->>'system_prompt' AS snapshot_system_prompt,"
                        " AVG(r.score) AS avg_score,"
                        " COUNT(r.id) AS rating_count,"
                        + _FAV_COUNT_SQL
                        + ", "
                        + _FAVORITED_SQL
                        + _SHELF_FROM_SQL
                        + _SHELF_WHERE_SQL
                        + " AND b.bot_key=:k"
                        " GROUP BY b.id, v.id"
                    ),
                    {"t": tenant_id, "k": bot_key, "fu": user_id},
                )
            )
            .mappings()
            .first()
        )
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
            (
                await self._s.execute(
                    text(
                        "SELECT b.id, b.bot_key,"  # noqa: S608  常量段拼接,值全绑定
                        " v.snapshot->>'name' AS name,"
                        " v.snapshot->>'greeting' AS greeting,"
                        " b.use_count, b.published_at,"
                        " COALESCE(AVG(r.score), 0) AS avg_score, COUNT(r.id) AS rating_count,"
                        + _FAV_COUNT_SQL
                        + ", TRUE AS favorited"
                        " FROM public.sys_bot_favorite f"
                        " JOIN public.sys_bot_definition b ON b.id=f.bot_id"
                        " JOIN public.sys_bot_version v ON v.id=b.current_version_id"
                        " LEFT JOIN public.sys_bot_rating r ON r.bot_id=b.id"
                        " WHERE f.tenant_id=CAST(:t AS uuid) AND f.user_id=CAST(:u AS uuid)"
                        " AND b.tenant_id=CAST(:t AS uuid)"
                        " AND b.published_at IS NOT NULL AND b.enabled"
                        " GROUP BY b.id, v.id ORDER BY MAX(f.created_at) DESC"
                    ),
                    {"t": tenant_id, "u": user_id},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    # ── 评分 ──

    async def upsert_rating(self, *, tenant_id: str, user_id: str, bot_id: str, score: int) -> None:
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
