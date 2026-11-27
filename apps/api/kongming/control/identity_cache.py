"""身份缓存写入:把快照里的租户/成员/粗角色落到本地执行表。

执行层的租户解析与 RBAC 仍读本地表(它们就是 last-known 缓存);本模块只
负责让缓存与快照收敛。缺失的本地角色键不臆造:跳过并告警,由控制面与
服务域配置对齐。成员关系在快照管辖租户内做对账删除,管辖外不动。
"""

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kongming.control.models import SnapshotSections

log = logging.getLogger(__name__)


class SqlTenantKeyResolver:
    """本地 tenant_id → tenant_key;执行层统一经键匹配快照权益/策略。"""

    def __init__(self, maker: async_sessionmaker[AsyncSession]) -> None:
        self._maker = maker

    async def key_for_id(self, tenant_id: str) -> str | None:
        async with self._maker() as s:
            row = (
                await s.execute(
                    text("SELECT tenant_key FROM public.sys_tenant WHERE id = CAST(:id AS uuid)"),
                    {"id": tenant_id},
                )
            ).scalar_one_or_none()
        return None if row is None else str(row)


class SqlIdentityApplier:
    def __init__(self, maker: async_sessionmaker[AsyncSession]) -> None:
        self._maker = maker

    async def apply(self, sections: SnapshotSections, *, service_id: str, version: int) -> None:
        """按快照版本单调应用身份缓存;多实例并发只前进不后退。"""
        async with self._maker() as s:
            await s.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                {"key": f"control.identity:{service_id}"},
            )
            current = (
                await s.execute(
                    text("SELECT version FROM control.identity_state WHERE service_id = :sid"),
                    {"sid": service_id},
                )
            ).scalar_one_or_none()
            if current is not None and int(current) >= version:
                await s.rollback()
                return
            tenant_ids = await _upsert_tenants(s, sections)
            await _upsert_members(s, sections, tenant_ids)
            await _bind_coarse_roles(s, sections)
            await s.execute(
                text(
                    "INSERT INTO control.identity_state (service_id, version, updated_at)"
                    " VALUES (:sid, :ver, now())"
                    " ON CONFLICT (service_id) DO UPDATE SET"
                    "  version = EXCLUDED.version, updated_at = now()"
                ),
                {"sid": service_id, "ver": version},
            )
            await s.commit()


async def _upsert_tenants(s: AsyncSession, sections: SnapshotSections) -> dict[str, str]:
    out: dict[str, str] = {}
    for t in sections.tenants:
        await s.execute(
            text(
                "INSERT INTO public.sys_tenant (id, tenant_key, tenant_name, status)"
                " VALUES (CAST(:id AS uuid), :key, :name, :status)"
                " ON CONFLICT (tenant_key) DO UPDATE SET"
                "  tenant_name = EXCLUDED.tenant_name, status = EXCLUDED.status,"
                "  updated_at = now()"
            ),
            {"id": t.tenant_id, "key": t.tenant_key, "name": t.tenant_name, "status": t.status},
        )
        row = (
            await s.execute(
                text("SELECT id FROM public.sys_tenant WHERE tenant_key = :key"),
                {"key": t.tenant_key},
            )
        ).scalar_one()
        out[t.tenant_key] = str(row)
    return out


async def _upsert_members(
    s: AsyncSession, sections: SnapshotSections, tenant_ids: dict[str, str]
) -> None:
    pairs: set[tuple[str, str]] = set()
    for m in sections.members:
        tenant_id = tenant_ids.get(m.tenant_key)
        if tenant_id is None:
            log.warning("成员引用了快照内不存在的租户键,跳过: %s", m.tenant_key)
            continue
        await s.execute(
            text(
                "INSERT INTO public.sys_user"
                " (id, username, email, nickname, status, updated_at, deleted_at)"
                " VALUES (CAST(:id AS uuid), :u, :e, :n, :st, now(), NULL)"
                " ON CONFLICT (id) DO UPDATE SET"
                "  username = EXCLUDED.username, email = EXCLUDED.email,"
                "  nickname = EXCLUDED.nickname, status = EXCLUDED.status,"
                "  updated_at = now(), deleted_at = NULL"
            ),
            {
                "id": m.user_id,
                "u": m.username,
                "e": m.email,
                "n": m.nickname[:64],
                "st": m.status,
            },
        )
        await s.execute(
            text(
                "INSERT INTO public.sys_user_tenant (user_id, tenant_id, granted_at)"
                " VALUES (CAST(:u AS uuid), CAST(:t AS uuid), now())"
                " ON CONFLICT (user_id, tenant_id) DO NOTHING"
            ),
            {"u": m.user_id, "t": tenant_id},
        )
        pairs.add((m.user_id, tenant_id))
    if not tenant_ids:
        return
    existing = (
        await s.execute(
            text(
                "SELECT ut.user_id, ut.tenant_id FROM public.sys_user_tenant ut"
                " WHERE ut.tenant_id = ANY(CAST(:ids AS uuid[]))"
            ),
            {"ids": list(tenant_ids.values())},
        )
    ).all()
    for user_id, tenant_id in existing:
        if (str(user_id), str(tenant_id)) not in pairs:
            await s.execute(
                text(
                    "DELETE FROM public.sys_user_tenant"
                    " WHERE user_id = CAST(:u AS uuid) AND tenant_id = CAST(:t AS uuid)"
                ),
                {"u": str(user_id), "t": str(tenant_id)},
            )


async def _bind_coarse_roles(s: AsyncSession, sections: SnapshotSections) -> None:
    declared = {r.role_key for r in sections.coarse_roles}
    roles = {
        str(row[0]): str(row[1])
        for row in (
            await s.execute(
                text("SELECT role_key, id FROM public.sys_role WHERE role_key = ANY(:keys)"),
                {"keys": sorted(declared)},
            )
        ).all()
    }
    missing = declared - set(roles)
    if missing:
        log.warning("快照引用了本地不存在的粗角色,跳过绑定: %s", sorted(missing))
    desired: set[tuple[str, str]] = set()
    for m in sections.members:
        for role_key in m.coarse_role_keys:
            role_id = roles.get(role_key)
            if role_id is not None:
                desired.add((m.user_id, role_id))
    if desired:
        for user_id, role_id in desired:
            await s.execute(
                text(
                    "INSERT INTO public.sys_user_role (user_id, role_id)"
                    " VALUES (CAST(:u AS uuid), CAST(:r AS uuid))"
                    " ON CONFLICT (user_id, role_id) DO NOTHING"
                ),
                {"u": user_id, "r": role_id},
            )
    # 仅回收快照声明过的粗角色、且用户仍在快照成员内的绑定;其余授权不触碰
    snapshot_users = {m.user_id for m in sections.members}
    if roles and snapshot_users:
        rows = (
            await s.execute(
                text(
                    "SELECT user_id, role_id FROM public.sys_user_role"
                    " WHERE role_id = ANY(CAST(:ids AS uuid[]))"
                    "   AND user_id = ANY(CAST(:users AS uuid[]))"
                ),
                {"ids": list(roles.values()), "users": sorted(snapshot_users)},
            )
        ).all()
        for user_id, role_id in rows:
            if (str(user_id), str(role_id)) not in desired:
                await s.execute(
                    text(
                        "DELETE FROM public.sys_user_role"
                        " WHERE user_id = CAST(:u AS uuid) AND role_id = CAST(:r AS uuid)"
                    ),
                    {"u": str(user_id), "r": str(role_id)},
                )
