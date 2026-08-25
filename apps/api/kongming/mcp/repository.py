"""mcp 配置仓储(028):sys_mcp_server CRUD + 快照;headers AES-GCM
(008 cipher 复用),解密仅限出站调用路径(get_call_target)——
查询面一律掩码,明文永不出现在列表/详情。"""

import json
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.datasource.cipher import AesGcmCipher
from kongming.mcp.models import SERVER_KEY_RE, McpServerExists, McpServerNotFound

__all__ = ["McpServerExists", "McpServerNotFound", "SqlMcpRepo"]


def _mask_keys(headers_cipher: str, cipher: AesGcmCipher) -> list[str]:
    """密文 → 键名列表(值永不回显);解密失败按无键名(不炸查询面)。"""
    if not headers_cipher:
        return []
    try:
        raw = json.loads(cipher.decrypt(headers_cipher))
    except Exception:  # noqa: BLE001  # 密钥轮换/损坏:查询面降级
        return []
    return [str(k) for k in raw] if isinstance(raw, dict) else []


class SqlMcpRepo:
    def __init__(self, session: AsyncSession, cipher: AesGcmCipher) -> None:
        self._s = session
        self._cipher = cipher

    async def create(
        self,
        *,
        tenant_id: str,
        server_key: str,
        name: str,
        endpoint: str,
        headers: dict[str, str],
    ) -> str:
        cipher_text = self._cipher.encrypt(json.dumps(headers)) if headers else ""
        try:
            row = (
                await self._s.execute(
                    text(
                        "INSERT INTO public.sys_mcp_server"
                        " (id, tenant_id, server_key, name, endpoint, headers_cipher)"
                        " VALUES (gen_random_uuid(), CAST(:t AS uuid), :k, :n, :e, :h)"
                        " RETURNING id"
                    ),
                    {"t": tenant_id, "k": server_key, "n": name, "e": endpoint, "h": cipher_text},
                )
            ).scalar_one()
        except Exception as e:
            if "ux_sys_mcp_server_tenant_key" in str(e):
                raise McpServerExists(server_key) from e
            raise
        return str(row)

    async def update(
        self,
        *,
        server_id: str,
        tenant_id: str,
        name: str,
        endpoint: str,
        headers: dict[str, str | None],  # 值 None=保持现值(D2 编辑语义)
    ) -> None:
        row = await self._get_row(server_id=server_id, tenant_id=tenant_id)
        if row is None:
            raise McpServerNotFound(server_id)
        if any(v is not None for v in headers.values()):
            # 合并:现值解密 → 覆盖更新键 → 重加密(明文不出本方法)
            current: dict[str, str] = {}
            if row["headers_cipher"]:
                raw = json.loads(self._cipher.decrypt(str(row["headers_cipher"])))
                current = {str(k): str(v) for k, v in raw.items()}
            for k, v in headers.items():
                if v is None:
                    continue
                if v == "":  # 显式空串=删除该键
                    current.pop(k, None)
                else:
                    current[k] = v
            cipher_text = self._cipher.encrypt(json.dumps(current)) if current else ""
        else:
            cipher_text = str(row["headers_cipher"])
        await self._s.execute(
            text(
                "UPDATE public.sys_mcp_server SET name=:n, endpoint=:e,"
                " headers_cipher=:h, updated_at=now()"
                " WHERE id=CAST(:i AS uuid) AND tenant_id=CAST(:t AS uuid)"
            ),
            {"n": name, "e": endpoint, "h": cipher_text, "i": server_id, "t": tenant_id},
        )

    async def set_enabled(self, *, server_id: str, tenant_id: str, enabled: bool) -> bool:
        res = cast(  # DML 结果为 CursorResult(chat 仓储同款形态)
            "CursorResult[object]",
            await self._s.execute(
                text(
                    "UPDATE public.sys_mcp_server SET enabled=:e, updated_at=now()"
                    " WHERE id=CAST(:i AS uuid) AND tenant_id=CAST(:t AS uuid)"
                ),
                {"e": enabled, "i": server_id, "t": tenant_id},
            ),
        )
        return res.rowcount > 0

    async def delete(self, *, server_id: str, tenant_id: str) -> bool:
        res = cast(
            "CursorResult[object]",
            await self._s.execute(
                text(
                    "DELETE FROM public.sys_mcp_server"
                    " WHERE id=CAST(:i AS uuid) AND tenant_id=CAST(:t AS uuid)"
                ),
                {"i": server_id, "t": tenant_id},
            ),
        )
        return res.rowcount > 0

    async def commit(self) -> None:
        await self._s.commit()

    async def _get_row(self, *, server_id: str, tenant_id: str) -> dict[str, Any] | None:
        row = (
            await self._s.execute(
                text(
                    "SELECT * FROM public.sys_mcp_server"
                    " WHERE id=CAST(:i AS uuid) AND tenant_id=CAST(:t AS uuid)"
                ),
                {"i": server_id, "t": tenant_id},
            )
        ).mappings().first()
        return dict(row) if row else None

    async def get(self, *, server_id: str, tenant_id: str) -> dict[str, Any] | None:
        """详情(掩码面):header_keys 仅键名。"""
        row = await self._get_row(server_id=server_id, tenant_id=tenant_id)
        if row is None:
            return None
        row["header_keys"] = _mask_keys(str(row.pop("headers_cipher") or ""), self._cipher)
        return row

    async def list_masked(self, *, tenant_id: str) -> list[dict[str, Any]]:
        rows = (
            await self._s.execute(
                text(
                    "SELECT * FROM public.sys_mcp_server"
                    " WHERE tenant_id=CAST(:t AS uuid) ORDER BY created_at DESC, id DESC"
                ),
                {"t": tenant_id},
            )
        ).mappings().all()
        out: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            d["header_keys"] = _mask_keys(str(d.pop("headers_cipher") or ""), self._cipher)
            out.append(d)
        return out

    async def save_snapshot(
        self, *, server_id: str, tenant_id: str, tools: list[dict[str, Any]], error: str
    ) -> None:
        await self._s.execute(
            text(
                "UPDATE public.sys_mcp_server SET tools=CAST(:tools AS jsonb),"
                " last_sync_at=now(), last_sync_error=:e, updated_at=now()"
                " WHERE id=CAST(:i AS uuid) AND tenant_id=CAST(:t AS uuid)"
            ),
            {
                "tools": json.dumps(tools, ensure_ascii=False),
                "e": error[:500],
                "i": server_id,
                "t": tenant_id,
            },
        )

    async def find_enabled_by_tool(
        self, *, tenant_id: str, qualified: str
    ) -> dict[str, Any] | None:
        """动态解析:按注册全名找启用 server(快照 JSONB @> 包含查询)。"""
        row = (
            await self._s.execute(
                text(
                    "SELECT * FROM public.sys_mcp_server"
                    " WHERE tenant_id=CAST(:t AS uuid) AND enabled"
                    " AND tools @> CAST(:q AS jsonb)"
                    " LIMIT 1"
                ),
                {"t": tenant_id, "q": json.dumps([{"qualified": qualified}])},
            )
        ).mappings().first()
        return dict(row) if row else None

    async def list_enabled(self, *, tenant_id: str) -> list[dict[str, Any]]:
        """动态目录面:启用 server 的行(含快照;headers 密文原样,
        调用方不得回显——目录组装只取 tools 快照)。"""
        rows = (
            await self._s.execute(
                text(
                    "SELECT id, tenant_id, server_key, name, endpoint, enabled, tools,"
                    " last_sync_at, last_sync_error"
                    " FROM public.sys_mcp_server"
                    " WHERE tenant_id=CAST(:t AS uuid) AND enabled"
                    " ORDER BY created_at ASC, id ASC"
                ),
                {"t": tenant_id},
            )
        ).mappings().all()
        return [dict(r) for r in rows]

    async def get_call_target(
        self, *, tenant_id: str, server_key: str, require_enabled: bool = True
    ) -> tuple[str, dict[str, str]] | None:
        """出站调用专用:endpoint + 解密 headers(唯一明文出口;
        调用方 MUST NOT 落日志/审计)。require_enabled=True=执行面(防禁用
        竞态);管理面探测(test/sync)传 False——禁用态只读探测无副作用,
        且「禁用→改 endpoint→保存同步」主流程不能被 404 卡死(R1-S1)。"""
        if not SERVER_KEY_RE.fullmatch(server_key):
            return None
        # 两常量 SQL 分叉(不拼字符串,S608 零争议;enabled 列由分支字面量)
        if require_enabled:
            row = (
                await self._s.execute(
                    text(
                        "SELECT endpoint, headers_cipher FROM public.sys_mcp_server"
                        " WHERE tenant_id=CAST(:t AS uuid) AND server_key=:k AND enabled"
                    ),
                    {"t": tenant_id, "k": server_key},
                )
            ).mappings().first()
        else:
            row = (
                await self._s.execute(
                    text(
                        "SELECT endpoint, headers_cipher FROM public.sys_mcp_server"
                        " WHERE tenant_id=CAST(:t AS uuid) AND server_key=:k"
                    ),
                    {"t": tenant_id, "k": server_key},
                )
            ).mappings().first()
        if row is None:
            return None
        headers: dict[str, str] = {}
        if row["headers_cipher"]:
            raw = json.loads(self._cipher.decrypt(str(row["headers_cipher"])))
            headers = {str(k): str(v) for k, v in raw.items()}
        return str(row["endpoint"]), headers
