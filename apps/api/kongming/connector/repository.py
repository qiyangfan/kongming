"""connector 仓储:tool_http_connectors CRUD;凭证 AES-GCM(cipher
复用,与 webhook secret 同一加密机制),解密仅限出站装配路径(get_call_target)
与掩码尾号提取——查询/列表面一律掩码,明文永不出现在任何接口响应。"""

import json
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.connector.models import mask_secret
from kongming.datasource.cipher import AesGcmCipher
from kongming.db import uuid7

_MASKED_COLUMNS = (
    "id",
    "tenant_id",
    "key",
    "name",
    "description",
    "method",
    "path_template",
    "params",
    "allowed_hosts",
    "auth_type",
    "requires_confirmation",
    "timeout_ms",
    "enabled",
    "created_at",
    "updated_at",
)


def _tail_mask(secret_cipher: str, cipher: AesGcmCipher) -> tuple[bool, str]:
    """密文 → (有无凭证, 尾号掩码);解密失败按有凭证全星(查询面降级不炸)。"""
    if not secret_cipher:
        return False, ""
    try:
        return True, mask_secret(cipher.decrypt(secret_cipher))
    except Exception:  # noqa: BLE001  # 密钥轮换/损坏:形态保留、明文不出
        return True, "****"


class SqlConnectorRepo:
    def __init__(self, session: AsyncSession, cipher: AesGcmCipher) -> None:
        self._s = session
        self._cipher = cipher

    async def create(self, row: dict[str, Any]) -> str:
        connector_id = str(uuid7())
        await self._s.execute(
            text(
                "INSERT INTO public.tool_http_connectors"
                " (id, tenant_id, key, name, description, method, path_template,"
                "  params, allowed_hosts, auth_type, auth_secret_enc,"
                "  requires_confirmation, timeout_ms, enabled, created_by)"
                " VALUES (CAST(:id AS uuid), CAST(:t AS uuid), :k, :n, :d, :m, :pt,"
                "  CAST(:params AS jsonb), CAST(:hosts AS jsonb), :at, :ase,"
                "  :rc, :tm, :en, CAST(:cb AS uuid))"
            ),
            {
                "id": connector_id,
                "t": row["tenant_id"],
                "k": row["key"],
                "n": row["name"],
                "d": row.get("description") or "",
                "m": row["method"],
                "pt": row["path_template"],
                "params": json_dumps(row.get("params") or []),
                "hosts": json_dumps(row.get("allowed_hosts") or []),
                "at": row["auth_type"],
                "ase": row.get("auth_secret_enc"),
                "rc": bool(row.get("requires_confirmation")),
                "tm": int(row["timeout_ms"]),
                "en": "ENABLED",
                "cb": row["created_by"],
            },
        )
        return connector_id

    async def update(self, *, connector_id: str, tenant_id: str, fields: dict[str, Any]) -> bool:
        """按字段更新(仅白名单列;auth_secret_enc 缺键=保留现值,凭证空框
        语义的存储侧)。两常量 SQL 分叉(不拼字符串,S608 零争议;mcp 仓储
        同款形态)。"""
        params: dict[str, Any] = {
            "i": connector_id,
            "t": tenant_id,
            "n": fields["name"],
            "d": fields.get("description") or "",
            "m": fields["method"],
            "pt": fields["path_template"],
            "params": json_dumps(fields.get("params") or []),
            "hosts": json_dumps(fields.get("allowed_hosts") or []),
            "at": fields["auth_type"],
            "rc": bool(fields.get("requires_confirmation")),
            "tm": int(fields["timeout_ms"]),
        }
        if "auth_secret_enc" in fields:  # 显式键=替换或清空(None)
            sql = (
                "UPDATE public.tool_http_connectors SET name=:n, description=:d,"
                " method=:m, path_template=:pt, params=CAST(:params AS jsonb),"
                " allowed_hosts=CAST(:hosts AS jsonb), auth_type=:at,"
                " requires_confirmation=:rc, timeout_ms=:tm, auth_secret_enc=:ase,"
                " updated_at=now()"
                " WHERE id=CAST(:i AS uuid) AND tenant_id=CAST(:t AS uuid)"
            )
            params["ase"] = fields["auth_secret_enc"]
        else:
            sql = (
                "UPDATE public.tool_http_connectors SET name=:n, description=:d,"
                " method=:m, path_template=:pt, params=CAST(:params AS jsonb),"
                " allowed_hosts=CAST(:hosts AS jsonb), auth_type=:at,"
                " requires_confirmation=:rc, timeout_ms=:tm, updated_at=now()"
                " WHERE id=CAST(:i AS uuid) AND tenant_id=CAST(:t AS uuid)"
            )
        res = cast(
            "CursorResult[object]",
            await self._s.execute(text(sql), params),
        )
        return res.rowcount > 0

    async def set_enabled(self, *, connector_id: str, tenant_id: str, enabled: str) -> bool:
        res = cast(
            "CursorResult[object]",
            await self._s.execute(
                text(
                    "UPDATE public.tool_http_connectors SET enabled=:e, updated_at=now()"
                    " WHERE id=CAST(:i AS uuid) AND tenant_id=CAST(:t AS uuid)"
                ),
                {"e": enabled, "i": connector_id, "t": tenant_id},
            ),
        )
        return res.rowcount > 0

    async def delete(self, *, connector_id: str, tenant_id: str) -> bool:
        res = cast(
            "CursorResult[object]",
            await self._s.execute(
                text(
                    "DELETE FROM public.tool_http_connectors"
                    " WHERE id=CAST(:i AS uuid) AND tenant_id=CAST(:t AS uuid)"
                ),
                {"i": connector_id, "t": tenant_id},
            ),
        )
        return res.rowcount > 0

    async def _get_row(self, *, connector_id: str, tenant_id: str) -> dict[str, Any] | None:
        row = (
            (
                await self._s.execute(
                    text(
                        "SELECT * FROM public.tool_http_connectors"
                        " WHERE id=CAST(:i AS uuid) AND tenant_id=CAST(:t AS uuid)"
                    ),
                    {"i": connector_id, "t": tenant_id},
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    async def get_masked(self, *, connector_id: str, tenant_id: str) -> dict[str, Any] | None:
        """详情(掩码面):auth_secret_enc 换成 has_secret/secret_mask。"""
        row = await self._get_row(connector_id=connector_id, tenant_id=tenant_id)
        if row is None:
            return None
        cipher_text = str(row.pop("auth_secret_enc") or "")
        has, tail = _tail_mask(cipher_text, self._cipher)
        row["has_secret"] = has
        row["secret_mask"] = tail
        row.pop("created_by", None)
        return {k: row[k] for k in _MASKED_COLUMNS} | {
            "has_secret": has,
            "secret_mask": tail,
        }

    async def list_masked(self, *, tenant_id: str) -> list[dict[str, Any]]:
        rows = (
            (
                await self._s.execute(
                    text(
                        "SELECT * FROM public.tool_http_connectors"
                        " WHERE tenant_id=CAST(:t AS uuid)"
                        " ORDER BY created_at DESC, id DESC"
                    ),
                    {"t": tenant_id},
                )
            )
            .mappings()
            .all()
        )
        out: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            cipher_text = str(d.pop("auth_secret_enc") or "")
            has, tail = _tail_mask(cipher_text, self._cipher)
            d.pop("created_by", None)
            out.append(
                {k: d[k] for k in _MASKED_COLUMNS} | {"has_secret": has, "secret_mask": tail}
            )
        return out

    async def list_enabled(self, *, tenant_id: str) -> list[dict[str, Any]]:
        """注入装配面:启用行(密文列不出——装配只需声明字段,调用方不得回显)。"""
        rows = (
            (
                await self._s.execute(
                    text(
                        "SELECT id, tenant_id, key, name, description, method,"
                        " path_template, params, allowed_hosts, auth_type,"
                        " requires_confirmation, timeout_ms, enabled"
                        " FROM public.tool_http_connectors"
                        " WHERE tenant_id=CAST(:t AS uuid) AND enabled='ENABLED'"
                        " ORDER BY created_at ASC, id ASC"
                    ),
                    {"t": tenant_id},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def find_enabled_by_key(self, *, tenant_id: str, key: str) -> dict[str, Any] | None:
        """执行解析面:按 key 找启用行(密文列不出;禁用/删除即「未注册」)。"""
        row = (
            (
                await self._s.execute(
                    text(
                        "SELECT id, tenant_id, key, name, description, method,"
                        " path_template, params, allowed_hosts, auth_type,"
                        " requires_confirmation, timeout_ms, enabled"
                        " FROM public.tool_http_connectors"
                        " WHERE tenant_id=CAST(:t AS uuid) AND key=:k"
                        " AND enabled='ENABLED'"
                    ),
                    {"t": tenant_id, "k": key},
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    async def get_call_target(self, *, tenant_id: str, key: str) -> dict[str, Any] | None:
        """出站装配专用:启用行 + 解密材料(唯一明文出口;调用方 MUST NOT 落
        日志/审计/任何响应)。"""
        row = (
            (
                await self._s.execute(
                    text(
                        "SELECT id, tenant_id, key, name, description, method,"
                        " path_template, params, allowed_hosts, auth_type,"
                        " auth_secret_enc, requires_confirmation, timeout_ms, enabled"
                        " FROM public.tool_http_connectors"
                        " WHERE tenant_id=CAST(:t AS uuid) AND key=:k"
                        " AND enabled='ENABLED'"
                    ),
                    {"t": tenant_id, "k": key},
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        d = dict(row)
        cipher_text = str(d.pop("auth_secret_enc") or "")
        d["auth_secret_plain"] = self._cipher.decrypt(cipher_text) if cipher_text else None
        return d

    async def commit(self) -> None:
        await self._s.commit()


def json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)
