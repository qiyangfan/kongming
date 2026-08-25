"""bot 仓储(025):sys_bot_definition / sys_api_key SQL。"""

import json
from collections.abc import Collection
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.db import uuid7


class SqlBotRepo:
    def __init__(self, s: AsyncSession) -> None:
        self._s = s

    async def commit(self) -> None:
        await self._s.commit()

    async def create_bot(self, **kw: Any) -> str:
        row_id = str(uuid7())
        await self._s.execute(
            text(
                "INSERT INTO public.sys_bot_definition"
                " (id, tenant_id, bot_key, name, system_prompt, tools,"
                "  datasource_key, model, enabled, is_public, created_by)"
                " VALUES (:i, :t, :k, :n, :sp, CAST(:tools AS jsonb), :dk, :m,"
                "         :en, :pub, :cb)"
            ),
            {
                "i": row_id,
                "t": kw["tenant_id"],
                "k": kw["bot_key"],
                "n": kw["name"],
                "sp": kw["system_prompt"],
                "tools": json.dumps(kw.get("tools") or []),
                "dk": kw.get("datasource_key", "default"),
                "m": kw.get("model"),
                "en": kw.get("enabled", True),
                "pub": kw.get("is_public", False),
                "cb": kw["created_by"],
            },
        )
        return row_id

    async def get_by_key(self, *, tenant_id: str, bot_key: str) -> dict[str, Any] | None:
        row = (
            await self._s.execute(
                text(
                    "SELECT b.*, t.tenant_key FROM public.sys_bot_definition b"
                    " JOIN public.sys_tenant t ON t.id=b.tenant_id"
                    " WHERE b.tenant_id=:t AND b.bot_key=:k"
                ),
                {"t": tenant_id, "k": bot_key},
            )
        ).mappings().one_or_none()
        return None if row is None else dict(row)

    async def get_by_key_anytenant(self, bot_key: str) -> dict[str, Any] | None:
        """公开 web 嵌入页定位(无租户头;bot_key 全局唯一性由调用方校验)。"""
        row = (
            await self._s.execute(
                text(
                    "SELECT b.*, t.tenant_key FROM public.sys_bot_definition b"
                    " JOIN public.sys_tenant t ON t.id=b.tenant_id"
                    " WHERE b.bot_key=:k"
                ),
                {"k": bot_key},
            )
        ).mappings().one_or_none()
        return None if row is None else dict(row)

    async def get_mine(
        self, *, bot_id: str, tenant_id: str, user_id: str, is_admin: bool,
        team_ids: Collection[str] = (),
    ) -> dict[str, Any] | None:
        sql = (
            "SELECT * FROM public.sys_bot_definition"
            " WHERE id=:i AND tenant_id=:t"
        )
        params: dict[str, Any] = {"i": bot_id, "t": tenant_id}
        if not is_admin:
            # 032:创建者 或 归属用户所在团队(与 list_bots 同口径)
            sql += " AND (created_by=:u OR team_id = ANY(CAST(:tids AS uuid[])))"
            params["u"] = user_id
            params["tids"] = list(team_ids)
        row = (await self._s.execute(text(sql), params)).mappings().one_or_none()
        return None if row is None else dict(row)

    async def list_bots(
        self, *, tenant_id: str, user_id: str, is_admin: bool,
        team_ids: Collection[str] = (),
    ) -> list[dict[str, Any]]:
        sql = (
            "SELECT * FROM public.sys_bot_definition WHERE tenant_id=:t"
        )
        params: dict[str, Any] = {"t": tenant_id}
        if not is_admin:
            sql += " AND (created_by=:u OR team_id = ANY(CAST(:tids AS uuid[])))"
            params["u"] = user_id
            params["tids"] = list(team_ids)
        sql += " ORDER BY created_at DESC"
        rows = (await self._s.execute(text(sql), params)).mappings().all()
        return [dict(r) for r in rows]

    async def update_bot(self, *, bot_id: str, fields: dict[str, Any]) -> bool:
        allowed = {
            "name", "system_prompt", "tools", "datasource_key",
            "model", "enabled", "is_public",
        }
        sets = []
        params: dict[str, Any] = {"i": bot_id}
        for k, v in fields.items():
            if k not in allowed:
                continue
            sets.append(f"{k} = :{k}")
            params[k] = json.dumps(v) if k == "tools" else v
        if not sets:
            return False
        set_clause = ", ".join(sets)
        if "tools" in params:
            set_clause = set_clause.replace("tools = :tools", "tools = CAST(:tools AS jsonb)")
        hit = (
            await self._s.execute(
                text(
                    f"UPDATE public.sys_bot_definition SET {set_clause}, updated_at=now()"  # noqa: S608  # 列名白名单
                    " WHERE id=:i RETURNING id"
                ),
                params,
            )
        ).scalar_one_or_none()
        return hit is not None

    async def find_user_status(self, user_id: str) -> str | None:
        """R1-L3:Bot 创建者账号态(ACTIVE 才可运行)。"""
        return (
            await self._s.execute(
                text("SELECT status FROM public.sys_user WHERE id=:u"), {"u": user_id}
            )
        ).scalar_one_or_none()

    async def find_datasource(self, *, datasource_key: str) -> dict[str, Any] | None:
        """R1-H2:Bot 创建/编辑的数据源归属校验(008 sys_datasource 只读)。"""
        row = (
            await self._s.execute(
                text(
                    "SELECT status, tenant_id FROM public.sys_datasource"
                    " WHERE datasource_key=:k"
                ),
                {"k": datasource_key},
            )
        ).mappings().one_or_none()
        return None if row is None else dict(row)

    # ── api-key ──

    async def insert_api_key(self, **kw: Any) -> str:
        row_id = str(uuid7())
        await self._s.execute(
            text(
                "INSERT INTO public.sys_api_key"
                " (id, tenant_id, bot_id, key_hash, name, scopes)"
                " VALUES (:i, :t, :b, :h, :n, CAST(:sc AS jsonb))"
            ),
            {
                "i": row_id,
                "t": kw["tenant_id"],
                "b": kw["bot_id"],
                "h": kw["key_hash"],
                "n": kw["name"],
                "sc": json.dumps(kw.get("scopes") or {}),
            },
        )
        return row_id

    async def find_api_key(self, *, key_hash: str) -> dict[str, Any] | None:
        row = (
            await self._s.execute(
                text(
                    "SELECT k.*, b.bot_key, b.enabled AS bot_enabled"
                    " FROM public.sys_api_key k"
                    " JOIN public.sys_bot_definition b ON b.id = k.bot_id"
                    " WHERE k.key_hash=:h AND k.revoked_at IS NULL"
                ),
                {"h": key_hash},
            )
        ).mappings().one_or_none()
        return None if row is None else dict(row)

    async def list_keys(self, *, bot_id: str) -> list[dict[str, Any]]:
        rows = (
            await self._s.execute(
                text(
                    "SELECT id, name, scopes, revoked_at, last_used_at, created_at"
                    " FROM public.sys_api_key WHERE bot_id=:b ORDER BY created_at DESC"
                ),
                {"b": bot_id},
            )
        ).mappings().all()
        return [dict(r) for r in rows]

    async def revoke_key(self, *, key_id: str, bot_id: str) -> bool:
        hit = (
            await self._s.execute(
                text(
                    "UPDATE public.sys_api_key SET revoked_at=now()"
                    " WHERE id=:i AND bot_id=:b AND revoked_at IS NULL RETURNING id"
                ),
                {"i": key_id, "b": bot_id},
            )
        ).scalar_one_or_none()
        return hit is not None

    async def touch_key_used(self, *, key_id: str) -> None:
        await self._s.execute(
            text("UPDATE public.sys_api_key SET last_used_at=now() WHERE id=:i"),
            {"i": key_id},
        )
