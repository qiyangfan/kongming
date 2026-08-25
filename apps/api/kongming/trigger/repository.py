"""trigger 存储(031):sys_trigger_definition/sys_trigger_run。"""

import datetime as dt
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.uuid7 import uuid7


def _ts(v: Any) -> dt.datetime | None:
    """ISO 串/None → datetime(asyncpg 客户端类型预检,CAST 帮不上——014 教训)。"""
    if v is None or isinstance(v, dt.datetime):
        return v
    return dt.datetime.fromisoformat(str(v))


class SqlTriggerRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def create(self, **kw: Any) -> str:
        rid = str(uuid7())
        await self._s.execute(
            text(
                "INSERT INTO public.sys_trigger_definition"
                " (id, tenant_id, tg_key, name, type, target_type, target_key,"
                "  cron_expr, min_interval_sec, webhook_path, secret_cipher, adapter,"
                "  notify_url, created_by, next_fire_at)"
                " VALUES (:id, CAST(:t AS uuid), :k, :n, :ty, :tt, :tk, :cron,"
                "         :mis, CAST(:wp AS uuid), :sec, :ad, :nu,"
                "         CAST(:cb AS uuid), CAST(:nf AS timestamptz))"
            ),
            {
                "id": rid,
                "t": kw["tenant_id"],
                "k": kw["tg_key"],
                "n": kw["name"],
                "ty": kw["type"],
                "tt": kw["target_type"],
                "tk": kw["target_key"],
                "cron": kw.get("cron_expr"),
                "mis": kw.get("min_interval_sec", 60),
                "wp": kw.get("webhook_path"),
                "sec": kw.get("secret_cipher", ""),
                "ad": kw.get("adapter", "custom"),
                "nu": kw.get("notify_url", ""),
                "cb": kw["created_by"],
                "nf": _ts(kw.get("next_fire_at")),
            },
        )
        return rid

    async def update(self, **kw: Any) -> bool:
        res = cast(
            "CursorResult[object]",
            await self._s.execute(
                text(
                    "UPDATE public.sys_trigger_definition SET name=:n,"
                    " target_type=:tt, target_key=:tk, cron_expr=:cron,"
                    " min_interval_sec=:mis, adapter=:ad, notify_url=:nu,"
                    " secret_cipher=COALESCE(NULLIF(:sec, ''), secret_cipher),"
                    " next_fire_at=CAST(:nf AS timestamptz), updated_at=now()"
                    " WHERE id=CAST(:i AS uuid) AND tenant_id=CAST(:t AS uuid)"
                ),
                {
                    "n": kw["name"],
                    "tt": kw["target_type"],
                    "tk": kw["target_key"],
                    "cron": kw.get("cron_expr"),
                    "mis": kw.get("min_interval_sec", 60),
                    "nf": _ts(kw.get("next_fire_at")),
                    "ad": kw.get("adapter", "custom"),
                    "nu": kw.get("notify_url", ""),
                    "sec": kw.get("secret_cipher", ""),
                    "i": kw["trigger_id"],
                    "t": kw["tenant_id"],
                },
            ),
        )
        return res.rowcount > 0

    async def get(self, *, tenant_id: str, tg_key: str) -> dict[str, Any] | None:
        row = (
            await self._s.execute(
                text(
                    "SELECT * FROM public.sys_trigger_definition"
                    " WHERE tenant_id=CAST(:t AS uuid) AND tg_key=:k"
                ),
                {"t": tenant_id, "k": tg_key},
            )
        ).mappings().first()
        return dict(row) if row else None

    async def get_by_id(self, *, trigger_id: str) -> dict[str, Any] | None:
        row = (
            await self._s.execute(
                text(
                    "SELECT * FROM public.sys_trigger_definition"
                    " WHERE id=CAST(:i AS uuid)"
                ),
                {"i": trigger_id},
            )
        ).mappings().first()
        return dict(row) if row else None

    async def list_all(self, *, tenant_id: str) -> list[dict[str, Any]]:
        rows = (
            await self._s.execute(
                text(
                    "SELECT * FROM public.sys_trigger_definition"
                    " WHERE tenant_id=CAST(:t AS uuid) ORDER BY created_at DESC, id DESC"
                ),
                {"t": tenant_id},
            )
        ).mappings().all()
        return [dict(r) for r in rows]

    async def set_enabled(self, *, trigger_id: str, tenant_id: str, enabled: bool) -> bool:
        res = cast(
            "CursorResult[object]",
            await self._s.execute(
                text(
                    "UPDATE public.sys_trigger_definition SET enabled=:e, updated_at=now()"
                    " WHERE id=CAST(:i AS uuid) AND tenant_id=CAST(:t AS uuid)"
                ),
                {"e": enabled, "i": trigger_id, "t": tenant_id},
            ),
        )
        return res.rowcount > 0

    async def delete(self, *, trigger_id: str, tenant_id: str) -> bool:
        res = cast(
            "CursorResult[object]",
            await self._s.execute(
                text(
                    "DELETE FROM public.sys_trigger_definition"
                    " WHERE id=CAST(:i AS uuid) AND tenant_id=CAST(:t AS uuid)"
                ),
                {"i": trigger_id, "t": tenant_id},
            ),
        )
        return res.rowcount > 0

    async def find_by_webhook_path(self, *, path: str) -> dict[str, Any] | None:
        row = (
            await self._s.execute(
                text(
                    "SELECT * FROM public.sys_trigger_definition"
                    " WHERE webhook_path=CAST(:p AS uuid)"
                ),
                {"p": path},
            )
        ).mappings().first()
        return dict(row) if row else None

    async def list_due_cron(self, *, now_iso: str) -> list[dict[str, Any]]:
        rows = (
            await self._s.execute(
                text(
                    "SELECT * FROM public.sys_trigger_definition"
                    " WHERE type='CRON' AND enabled AND next_fire_at IS NOT NULL"
                    " AND next_fire_at <= CAST(:now AS timestamptz)"
                ),
                {"now": _ts(now_iso)},
            )
        ).mappings().all()
        return [dict(r) for r in rows]

    async def mark_fired(
        self, *, trigger_id: str, last_fired_iso: str, next_fire_iso: str | None
    ) -> None:
        await self._s.execute(
            text(
                "UPDATE public.sys_trigger_definition"
                " SET last_fired_at=CAST(:lf AS timestamptz),"
                " next_fire_at=CAST(:nf AS timestamptz), updated_at=now()"
                " WHERE id=CAST(:i AS uuid)"
            ),
            {"lf": _ts(last_fired_iso), "nf": _ts(next_fire_iso), "i": trigger_id},
        )

    async def insert_run(
        self,
        *,
        tenant_id: str,
        trigger_id: str,
        status: str,
        error: str,
        duration_ms: int,
        target_run_ref: str,
    ) -> str:
        rid = str(uuid7())
        await self._s.execute(
            text(
                "INSERT INTO public.sys_trigger_run"
                " (id, tenant_id, trigger_id, status, error, duration_ms, target_run_ref)"
                " VALUES (:i, CAST(:t AS uuid), CAST(:g AS uuid), :st, :e, :d, :ref)"
            ),
            {
                "i": rid,
                "t": tenant_id,
                "g": trigger_id,
                "st": status,
                "e": error[:2000],
                "d": duration_ms,
                "ref": target_run_ref,
            },
        )
        return rid

    async def page_runs(
        self, *, tenant_id: str, trigger_id: str, page: int, size: int
    ) -> tuple[int, list[dict[str, Any]]]:
        total = int(
            (
                await self._s.execute(
                    text(
                        "SELECT count(*) FROM public.sys_trigger_run"
                        " WHERE tenant_id=CAST(:t AS uuid) AND trigger_id=CAST(:g AS uuid)"
                    ),
                    {"t": tenant_id, "g": trigger_id},
                )
            ).scalar_one()
        )
        rows = (
            await self._s.execute(
                text(
                    "SELECT * FROM public.sys_trigger_run"
                    " WHERE tenant_id=CAST(:t AS uuid) AND trigger_id=CAST(:g AS uuid)"
                    " ORDER BY created_at DESC, id DESC LIMIT :lim OFFSET :off"
                ),
                {"t": tenant_id, "g": trigger_id, "lim": size, "off": (page - 1) * size},
            )
        ).mappings().all()
        return total, [dict(r) for r in rows]

    async def commit(self) -> None:
        await self._s.commit()
