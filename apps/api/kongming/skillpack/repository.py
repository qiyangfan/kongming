"""skillpack 仓储(056):skill_pack / skill_pack_binding SQL(租户双过滤)。

零框架纪律:本模块只依赖 sqlalchemy text/session。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.db import uuid7


class SqlSkillPackRepo:
    def __init__(self, s: AsyncSession) -> None:
        self._s = s

    async def commit(self) -> None:
        await self._s.commit()

    async def insert_pack(
        self,
        *,
        tenant_id: str,
        pack_key: str,
        name: str,
        version: str,
        description: str,
        content: dict[str, Any],
    ) -> str:
        pid = str(uuid7())
        await self._s.execute(
            text(
                "INSERT INTO public.skill_pack"
                " (id, tenant_id, pack_key, name, version, description, content, status)"
                " VALUES (:i, :t, :k, :n, :v, :d, CAST(:c AS jsonb), 'ACTIVE')"
            ),
            {
                "i": pid,
                "t": tenant_id,
                "k": pack_key,
                "n": name,
                "v": version,
                "d": description,
                "c": json.dumps(content, ensure_ascii=False),
            },
        )
        return pid

    async def get_pack(self, *, pack_id: str, tenant_id: str) -> dict[str, Any] | None:
        row = (
            (
                await self._s.execute(
                    text(
                        "SELECT id, tenant_id, pack_key, name, version, description,"
                        " content, status, created_at, updated_at"
                        " FROM public.skill_pack WHERE id=:i AND tenant_id=:t"
                    ),
                    {"i": pack_id, "t": tenant_id},
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        d = dict(row)
        raw = d.get("content")
        if isinstance(raw, (str, bytes)):
            try:
                parsed = json.loads(raw)
            except (TypeError, ValueError):
                parsed = {}
            d["content"] = parsed if isinstance(parsed, dict) else {}
        return d

    async def list_packs(self, *, tenant_id: str) -> list[dict[str, Any]]:
        rows = (
            (
                await self._s.execute(
                    text(
                        "SELECT id, pack_key, name, version, description, status, updated_at"
                        " FROM public.skill_pack WHERE tenant_id=:t ORDER BY pack_key"
                    ),
                    {"t": tenant_id},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def update_pack(
        self,
        *,
        pack_id: str,
        tenant_id: str,
        name: str | None = None,
        description: str | None = None,
        content: dict[str, Any] | None = None,
        status: str | None = None,
    ) -> bool:
        sets = []
        params: dict[str, Any] = {"i": pack_id, "t": tenant_id}
        if name is not None:
            sets.append("name=:n")
            params["n"] = name
        if description is not None:
            sets.append("description=:d")
            params["d"] = description
        if content is not None:
            sets.append("content=CAST(:c AS jsonb)")
            params["c"] = json.dumps(content, ensure_ascii=False)
        if status is not None:
            sets.append("status=:s")
            params["s"] = status
        if not sets:
            return False
        hit = (
            await self._s.execute(
                text(
                    f"UPDATE public.skill_pack SET {', '.join(sets)}, updated_at=now()"  # noqa: S608
                    " WHERE id=:i AND tenant_id=:t RETURNING id"
                ),
                params,
            )
        ).scalar_one_or_none()
        return hit is not None

    async def set_binding(self, *, pack_id: str, datasource_key: str, enabled: bool) -> None:
        await self._s.execute(
            text(
                "INSERT INTO public.skill_pack_binding"
                " (id, pack_id, datasource_key, enabled)"
                " VALUES (:i, :p, :d, :e)"
                " ON CONFLICT (pack_id, datasource_key)"
                " DO UPDATE SET enabled=:e, updated_at=now()"
            ),
            {
                "i": str(uuid7()),
                "p": pack_id,
                "d": datasource_key,
                "e": enabled,
            },
        )

    async def list_bindings(self, *, pack_id: str) -> list[dict[str, Any]]:
        rows = (
            (
                await self._s.execute(
                    text(
                        "SELECT datasource_key, enabled, updated_at"
                        " FROM public.skill_pack_binding WHERE pack_id=:p"
                        " ORDER BY datasource_key"
                    ),
                    {"p": pack_id},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def load_enabled(self, *, tenant_id: str, datasource_key: str) -> list[dict[str, Any]]:
        rows = (
            (
                await self._s.execute(
                    text(
                        "SELECT p.id, p.pack_key, p.name, p.version, p.content"
                        " FROM public.skill_pack p"
                        " JOIN public.skill_pack_binding b ON b.pack_id = p.id"
                        " WHERE p.tenant_id=:t AND p.status='ACTIVE'"
                        "   AND b.datasource_key=:d AND b.enabled=true"
                        " ORDER BY p.pack_key"
                    ),
                    {"t": tenant_id, "d": datasource_key},
                )
            )
            .mappings()
            .all()
        )
        out: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            raw = d.get("content")
            if isinstance(raw, (str, bytes)):
                try:
                    parsed = json.loads(raw)
                except (TypeError, ValueError):
                    parsed = {}
                d["content"] = parsed if isinstance(parsed, dict) else {}
            out.append(d)
        return out
