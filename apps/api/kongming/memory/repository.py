"""memory 仓储:user_memory SQL(向量召回双过滤/惰性休眠/CRUD)。

隔离铁律(spec):所有读写强制 user_id+tenant_id 双过滤;归属校验与
chat 404 同形(不存在/越权不区分)。
"""

import json
import logging
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.db import uuid7

log = logging.getLogger(__name__)

RECALL_TOP_K = 3
_DORMANT_DAYS = 90
_DORMANT_CONFIDENCE = 0.3


def _vec_literal(vector: list[float]) -> str:
    """pgvector 文本字面量(repr 全精度;与 rag 域实现保持一致)。"""
    return "[" + ",".join(repr(float(v)) for v in vector) + "]"


class SqlMemoryRepo:
    def __init__(self, s: AsyncSession) -> None:
        self._s = s

    async def commit(self) -> None:
        await self._s.commit()

    async def mark_dormant_lazy(self, *, user_id: str, tenant_id: str) -> None:
        """惰性休眠标记(召回路径顺带):低置信或 90 天未命中 → dormant。

        强制 user_id+tenant_id 双过滤(与召回同隔离铁律)——仅作用本人
        本租户,禁止跨用户跨租户批量置位。
        """
        await self._s.execute(
            text(
                "UPDATE public.user_memory SET dormant = true"
                " WHERE user_id = CAST(:u AS uuid) AND tenant_id = CAST(:t AS uuid)"
                "   AND dormant = false AND (confidence < :c"
                "   OR (last_hit_at IS NOT NULL AND last_hit_at < now() - interval '90 days')"
                "   OR (last_hit_at IS NULL AND created_at < now() - interval '90 days'))"
            ),
            {"u": user_id, "t": tenant_id, "c": _DORMANT_CONFIDENCE},
        )

    async def recall(
        self,
        *,
        user_id: str,
        tenant_id: str,
        vector: list[float],
        team_ids: list[str] | None = None,  # 团队记忆双路
    ) -> list[dict[str, Any]]:
        """向量召回 top-K(双过滤+非休眠+有向量);命中刷 last_hit_at(同事务)。
        team_ids 非空时并集团队记忆(user_id 本人个人记忆 ∪ 成员团队
        团队记忆[team_id 非空],同向量距离混排 top-K)。"""
        if team_ids:
            params: dict[str, Any] = {
                "u": user_id,
                "t": tenant_id,
                "vec": _vec_literal(vector),
                "k": RECALL_TOP_K,
                "tids": team_ids,
            }
        else:
            params = {"u": user_id, "t": tenant_id, "vec": _vec_literal(vector), "k": RECALL_TOP_K}
        if team_ids:
            sql = (
                "SELECT id, content, category, confidence, source"
                " FROM public.user_memory"
                " WHERE (user_id = :u OR team_id = ANY(:tids)) AND tenant_id = :t"
                " AND dormant = false AND embedding IS NOT NULL"
                " ORDER BY embedding <=> CAST(:vec AS halfvec) LIMIT :k"
            )
        else:
            # 个人查询只取 team_id 为空的记录，团队记忆需具备成员身份才能召回。
            sql = (
                "SELECT id, content, category, confidence, source"
                " FROM public.user_memory"
                " WHERE (user_id = :u AND team_id IS NULL) AND tenant_id = :t"
                " AND dormant = false AND embedding IS NOT NULL"
                " ORDER BY embedding <=> CAST(:vec AS halfvec) LIMIT :k"
            )
        rows = (await self._s.execute(text(sql), params)).mappings().all()
        if rows:
            await self._s.execute(
                text("UPDATE public.user_memory SET last_hit_at = now() WHERE id = ANY(:ids)"),
                {"ids": [r["id"] for r in rows]},
            )
        return [dict(r) for r in rows]

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
        team_id: str | None = None,  # 团队记忆(非空=归属团队)
    ) -> str:
        row_id = str(uuid7())
        await self._s.execute(
            text(
                "INSERT INTO public.user_memory"
                " (id, user_id, tenant_id, content, category, confidence, source,"
                "  embedding, team_id)"
                " VALUES (:i, :u, :t, :c, :cat, :conf, :src,"
                "         CAST(:vec AS halfvec), CAST(:tm AS uuid))"
            ),
            {
                "i": row_id,
                "u": user_id,
                "t": tenant_id,
                "c": content,
                "cat": category,
                "conf": confidence,
                "src": source,
                "vec": _vec_literal(vector) if vector else None,
                "tm": team_id,
            },
        )
        return row_id

    async def exists_identical(self, *, user_id: str, tenant_id: str, content: str) -> bool:
        """隐式落库去重:同人同租户同内容不重复落。"""
        return bool(
            (
                await self._s.execute(
                    text(
                        "SELECT 1 FROM public.user_memory"
                        " WHERE user_id=:u AND tenant_id=:t AND content=:c"
                    ),
                    {"u": user_id, "t": tenant_id, "c": content},
                )
            ).scalar_one_or_none()
        )

    async def list_mine(self, *, user_id: str, tenant_id: str) -> list[dict[str, Any]]:
        rows = (
            (
                await self._s.execute(
                    text(
                        "SELECT id, content, category, confidence, source, dormant,"
                        "       last_hit_at, created_at, team_id"
                        " FROM public.user_memory WHERE user_id=:u AND tenant_id=:t"
                        " ORDER BY created_at DESC"
                    ),
                    {"u": user_id, "t": tenant_id},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def update_mine(
        self,
        *,
        memory_id: str,
        user_id: str,
        tenant_id: str,
        content: str,
        category: str,
        vector: list[float] | None,
    ) -> bool:
        """编辑(归属收口):编辑即唤醒(dormant=false)+重算向量;命中行数=1 成功。"""
        hit = (
            await self._s.execute(
                text(
                    "UPDATE public.user_memory SET content=:c, category=:cat,"
                    " embedding=COALESCE(CAST(:vec AS halfvec), embedding),"  # 失败保原值
                    " dormant=false, updated_at=now()"
                    " WHERE id=:i AND user_id=:u AND tenant_id=:t"
                    " RETURNING id"
                ),
                {
                    "i": memory_id,
                    "u": user_id,
                    "t": tenant_id,
                    "c": content,
                    "cat": category,
                    "vec": _vec_literal(vector) if vector else None,
                },
            )
        ).scalar_one_or_none()
        return hit is not None

    async def delete_mine(self, *, memory_id: str, user_id: str, tenant_id: str) -> bool:
        hit = (
            await self._s.execute(
                text(
                    "DELETE FROM public.user_memory"
                    " WHERE id=:i AND user_id=:u AND tenant_id=:t"
                    " RETURNING id"
                ),
                {"i": memory_id, "u": user_id, "t": tenant_id},
            )
        ).scalar_one_or_none()
        return hit is not None

    # ── user_prefs 偏好槽位与人工版本 manual_revision ──

    async def get_prefs(self, *, user_id: str, tenant_id: str) -> dict[str, object]:
        prefs, _rev = await self.read_prefs_revision(user_id=user_id, tenant_id=tenant_id)
        return prefs

    @staticmethod
    def _parse_prefs_column(value: object) -> dict[str, object]:
        """json/jsonb 列归一化(asyncpg 常回 str,与 system.parse_json_agg 同口径)。"""
        if isinstance(value, dict):
            return {str(k): v for k, v in cast("dict[str, object]", value).items()}
        if isinstance(value, (str, bytes)):
            try:
                obj = json.loads(value)
            except (TypeError, ValueError):
                return {}
            if isinstance(obj, dict):
                return {str(k): v for k, v in cast("dict[str, object]", obj).items()}
            return {}
        return {}

    async def read_prefs_revision(
        self, *, user_id: str, tenant_id: str
    ) -> tuple[dict[str, object], int]:
        """读取 (prefs, manual_revision);行不存在=已知版本 0(非未知态)。"""
        row = (
            await self._s.execute(
                text(
                    "SELECT prefs, manual_revision FROM public.user_prefs"
                    " WHERE user_id=:u AND tenant_id=:t"
                ),
                {"u": user_id, "t": tenant_id},
            )
        ).one_or_none()
        if row is None:
            return {}, 0
        return self._parse_prefs_column(row[0]), int(row[1])

    async def merge_prefs_manual(
        self, *, user_id: str, tenant_id: str, values: dict[str, object], deletes: list[str]
    ) -> tuple[dict[str, object], int]:
        """人工短事务合并:单语句 UPSERT 行锁内基于最新 prefs 合并并
        递增 manual_revision;调用方保证 values/deletes 已归一化且非全空。"""
        row = (
            await self._s.execute(
                text(
                    """
                    INSERT INTO public.user_prefs
                      (id, user_id, tenant_id, prefs, manual_revision, updated_at, created_at)
                    VALUES (:i, :u, :t,
                            (CAST(:p AS jsonb) - CAST(:dels AS text[])), 1, now(), now())
                    ON CONFLICT (user_id, tenant_id) DO UPDATE SET
                      prefs = ((public.user_prefs.prefs::jsonb || CAST(:p AS jsonb))
                               - CAST(:dels AS text[])),
                      manual_revision = public.user_prefs.manual_revision + 1,
                      updated_at = now()
                    RETURNING prefs, manual_revision
                    """
                ),
                {
                    "i": str(uuid7()),
                    "u": user_id,
                    "t": tenant_id,
                    "p": json.dumps(values, ensure_ascii=False),
                    "dels": deletes,
                },
            )
        ).one()
        return self._parse_prefs_column(row[0]), int(row[1])

    async def clear_prefs_manual(
        self, *, user_id: str, tenant_id: str
    ) -> tuple[dict[str, object], int]:
        """清空:保留空行且已空仍递增版本——旧任务不得因重建行回到
        版本 0 而复活偏好。"""
        row = (
            await self._s.execute(
                text(
                    """
                    INSERT INTO public.user_prefs
                      (id, user_id, tenant_id, prefs, manual_revision, updated_at, created_at)
                    VALUES (:i, :u, :t, '{}'::jsonb, 1, now(), now())
                    ON CONFLICT (user_id, tenant_id) DO UPDATE SET
                      prefs = '{}'::jsonb,
                      manual_revision = public.user_prefs.manual_revision + 1,
                      updated_at = now()
                    RETURNING prefs, manual_revision
                    """
                ),
                {"i": str(uuid7()), "u": user_id, "t": tenant_id},
            )
        ).one()
        return self._parse_prefs_column(row[0]), int(row[1])

    async def fill_prefs_if_revision(
        self, *, user_id: str, tenant_id: str, values: dict[str, object], base_revision: int
    ) -> bool:
        """自动补齐:人工版本与快照一致才落;只补缺失槽位(已有值
        不覆盖、永不删除);不一致放弃(无返回行)。自动写入不递增版本。"""
        row = (
            await self._s.execute(
                text(
                    """
                    INSERT INTO public.user_prefs
                      (id, user_id, tenant_id, prefs, manual_revision, updated_at, created_at)
                    VALUES (:i, :u, :t, CAST(:p AS jsonb), 0, now(), now())
                    ON CONFLICT (user_id, tenant_id) DO UPDATE SET
                      prefs = (public.user_prefs.prefs::jsonb || COALESCE((
                          SELECT jsonb_object_agg(k, v)
                          FROM jsonb_each(CAST(:p AS jsonb)) AS e(k, v)
                          WHERE NOT (public.user_prefs.prefs::jsonb ? k)
                        ), '{}'::jsonb)),
                      updated_at = now()
                    WHERE public.user_prefs.manual_revision = :rev
                    RETURNING manual_revision
                    """
                ),
                {
                    "i": str(uuid7()),
                    "u": user_id,
                    "t": tenant_id,
                    "p": json.dumps(values, ensure_ascii=False),
                    "rev": base_revision,
                },
            )
        ).one_or_none()
        return row is not None
