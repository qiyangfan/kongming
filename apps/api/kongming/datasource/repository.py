"""datasource 域 SQLAlchemy 仓储实现(唯一 import sqlalchemy 的 datasource 文件)。

显式 SQL(text)同 system 域约定;行 → 领域 dataclass 的映射集中在 _to_* 函数。
审计复用 auth 域 SqlOperLogRepo(同会话=同事务,组合模式)。
"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming import tenant
from kongming.auth.repository import SqlOperLogRepo
from kongming.datasource.models import Datasource, DatasourceOption, ResolvedDatasource
from kongming.sql_util import contains_like_pattern

# 关键字对 key/name 不区分大小写包含匹配;ESCAPE '\' + contains_like_pattern 转义按字面匹配
_FILTER = "(:kw = '' OR d.datasource_key ILIKE :p ESCAPE '\\' OR d.name ILIKE :p ESCAPE '\\')"


def _to_datasource(row: dict[str, Any]) -> Datasource:
    """管理面视图行(不含 password_enc——密文永不出管理面 DTO)。"""
    return Datasource(
        id=str(row["id"]),
        datasource_key=str(row["datasource_key"]),
        name=str(row["name"]),
        tenant_id=str(row["tenant_id"]),
        tenant_key=str(row["tenant_key"]),
        tenant_name=str(row["tenant_name"]),
        host=str(row["host"]),
        port=int(row["port"]),
        database=str(row["database"]),
        username=str(row["username"]),
        default_schema=str(row["default_schema"]),
        readonly=bool(row["readonly"]),
        status=str(row["status"]),
        created_at=row["created_at"],
    )


class SqlDatasourceRepo:
    def __init__(self, s: AsyncSession) -> None:
        self._s = s
        self._oper = SqlOperLogRepo(s)  # 审计与业务同会话=同事务(组合模式)

    # ---- 管理面 ----

    async def count(self, keyword: str) -> int:
        n = await self._s.scalar(
            text(
                f"SELECT count(*) FROM public.sys_datasource d WHERE {_FILTER}"  # noqa: S608  # 常量模板,参数化绑定
            ),
            {"kw": keyword, "p": contains_like_pattern(keyword)},
        )
        return int(n or 0)

    async def list_page(self, keyword: str, limit: int, offset: int) -> list[Datasource]:
        rows = (
            await self._s.execute(
                text(
                    "SELECT d.*, t.tenant_key, t.tenant_name"  # noqa: S608  # 常量模板,参数化绑定
                    " FROM public.sys_datasource d"
                    " JOIN public.sys_tenant t ON t.id = d.tenant_id"
                    f" WHERE {_FILTER}"
                    " ORDER BY d.created_at DESC, d.id DESC"
                    " LIMIT :lim OFFSET :off"
                ),
                {
                    "kw": keyword,
                    "p": contains_like_pattern(keyword),
                    "lim": limit,
                    "off": offset,
                },
            )
        ).mappings()
        return [_to_datasource(dict(r)) for r in rows]

    async def get(self, ds_id: str) -> Datasource | None:
        row = (
            (
                await self._s.execute(
                    text(
                        "SELECT d.*, t.tenant_key, t.tenant_name"
                        " FROM public.sys_datasource d"
                        " JOIN public.sys_tenant t ON t.id = d.tenant_id"
                        " WHERE d.id=:i"
                    ),
                    {"i": ds_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        return _to_datasource(dict(row)) if row else None

    async def get_password_enc(self, ds_id: str) -> str | None:
        v = await self._s.scalar(
            text("SELECT password_enc FROM public.sys_datasource WHERE id=:i"), {"i": ds_id}
        )
        return None if v is None else str(v)

    async def key_taken(self, datasource_key: str) -> bool:
        n = await self._s.scalar(
            text("SELECT count(*) FROM public.sys_datasource WHERE datasource_key=:k"),
            {"k": datasource_key},
        )
        return int(n or 0) > 0

    async def tenant_active(self, tenant_id: str) -> bool:
        n = await self._s.scalar(
            text("SELECT count(*) FROM public.sys_tenant WHERE id=:i AND status='ACTIVE'"),
            {"i": tenant_id},
        )
        return int(n or 0) > 0

    async def insert(
        self,
        *,
        ds_id: str,
        datasource_key: str,
        name: str,
        tenant_id: str,
        host: str,
        port: int,
        database: str,
        username: str,
        password_enc: str,
        default_schema: str,
        readonly: bool,
    ) -> None:
        await self._s.execute(
            text(
                "INSERT INTO public.sys_datasource"
                " (id, datasource_key, name, tenant_id, host, port, database, username,"
                "  password_enc, default_schema, readonly)"
                " VALUES (:i, :k, :n, :t, :h, :p, :db, :u, :pe, :ds, :ro)"
            ),
            {
                "i": ds_id,
                "k": datasource_key,
                "n": name,
                "t": tenant_id,
                "h": host,
                "p": port,
                "db": database,
                "u": username,
                "pe": password_enc,
                "ds": default_schema,
                "ro": readonly,
            },
        )

    async def update(
        self,
        ds_id: str,
        *,
        name: str,
        host: str,
        port: int,
        database: str,
        username: str,
        password_enc: str | None,
        default_schema: str,
        readonly: bool,
    ) -> None:
        # password_enc None=不改(COALESCE 保持现值)
        await self._s.execute(
            text(
                "UPDATE public.sys_datasource SET"
                " name=:n, host=:h, port=:p, database=:db, username=:u,"
                " password_enc=coalesce(:pe, password_enc), default_schema=:ds,"
                " readonly=:ro, updated_at=now()"
                " WHERE id=:i"
            ),
            {
                "n": name,
                "h": host,
                "p": port,
                "db": database,
                "u": username,
                "pe": password_enc,
                "ds": default_schema,
                "ro": readonly,
                "i": ds_id,
            },
        )

    async def update_status(self, ds_id: str, status: str) -> None:
        await self._s.execute(
            text("UPDATE public.sys_datasource SET status=:st, updated_at=now() WHERE id=:i"),
            {"st": status, "i": ds_id},
        )

    async def delete(self, ds_id: str) -> None:
        await self._s.execute(text("DELETE FROM public.sys_datasource WHERE id=:i"), {"i": ds_id})

    # ---- 执行面 ----

    async def find_for_execute(self, datasource_key: str) -> ResolvedDatasource | None:
        row = (
            (
                await self._s.execute(
                    text(
                        "SELECT datasource_key, tenant_id, host, port, database, username,"
                        " password_enc, default_schema, status"
                        " FROM public.sys_datasource WHERE datasource_key=:k"
                    ),
                    {"k": datasource_key},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return ResolvedDatasource(
            datasource_key=str(row["datasource_key"]),
            tenant_id=str(row["tenant_id"]),
            host=str(row["host"]),
            port=int(row["port"]),
            database=str(row["database"]),
            username=str(row["username"]),
            password_enc=str(row["password_enc"]),
            default_schema=str(row["default_schema"]),
            status=str(row["status"]),
        )

    async def is_usable(self, datasource_key: str, tenant_id: str) -> bool:
        """DatasourceChecker 端口实现(chat 入口预检):存在+启用+归属。"""
        row = await self.find_for_execute(datasource_key)
        return row is not None and row.status == "ACTIVE" and row.tenant_id == tenant_id

    # ---- 选项接口租户复校(租户判定统一走 kongming.tenant) ----

    async def find_bound_tenant(self, user_id: str, tenant_key: str) -> str | None:
        return await tenant.bound_tenant_id(self._s, user_id=user_id, tenant_key=tenant_key)

    async def find_default_tenant(self, user_id: str) -> tuple[str, str] | None:
        return await tenant.default_tenant(self._s, user_id=user_id)

    async def list_enabled_by_tenant(self, tenant_id: str) -> list[DatasourceOption]:
        rows = (
            await self._s.execute(
                text(
                    "SELECT datasource_key, name FROM public.sys_datasource"
                    " WHERE tenant_id=:t AND status='ACTIVE'"
                    " ORDER BY datasource_key"
                ),
                {"t": tenant_id},
            )
        ).mappings()
        return [
            DatasourceOption(datasource_key=str(r["datasource_key"]), name=str(r["name"]))
            for r in rows
        ]

    # ---- 操作审计 ----

    async def log_oper(
        self, actor_id: str, action: str, target: str | None, detail: dict[str, object]
    ) -> None:
        await self._oper.add(actor_id, action, target, detail)

    # ---- 事务 ----

    async def commit(self) -> None:
        await self._s.commit()

    async def rollback(self) -> None:
        await self._s.rollback()
