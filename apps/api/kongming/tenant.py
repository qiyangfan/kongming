"""租户解析单一真理源(B1-B10 收口)。

chat/feedback 仓储与 memory/semantic/bot 路由的五处同构 SQL
(显式 X-Tenant-Id 白名单校验 + 缺省默认租户:default 优先、
最早授予、t.id tiebreak)全部收敛到本模块——语义演进
(ACTIVE 过滤/tiebreak)只改一处,防漂移。
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def bound_tenant_id(session: AsyncSession, *, user_id: str, tenant_key: str) -> str | None:
    """显式租户白名单校验:命中(绑定+ACTIVE)返回 tenant_id,否则 None。"""
    tid = (
        await session.execute(
            text(
                "SELECT t.id FROM public.sys_tenant t"
                " JOIN public.sys_user_tenant ut ON ut.tenant_id = t.id"
                " WHERE t.tenant_key = :k AND ut.user_id = :u AND t.status = 'ACTIVE'"
            ),
            {"k": tenant_key, "u": user_id},
        )
    ).scalar_one_or_none()
    return None if tid is None else str(tid)


async def default_tenant(session: AsyncSession, *, user_id: str) -> tuple[str, str] | None:
    """用户默认租户:default 优先,否则最早授予(multitenant spec);
    返回 (tenant_id, tenant_key);t.id tiebreak 保证同刻双授确定性收口
    (与 datasource 域同名查询逐字同构)。"""
    row = (
        (
            await session.execute(
                text(
                    "SELECT t.id, t.tenant_key FROM public.sys_tenant t"
                    " JOIN public.sys_user_tenant ut ON ut.tenant_id = t.id"
                    " WHERE ut.user_id = :u AND t.status = 'ACTIVE'"
                    " ORDER BY (t.tenant_key = 'default') DESC, ut.granted_at ASC, t.id"
                    " LIMIT 1"
                ),
                {"u": user_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    return None if row is None else (str(row["id"]), str(row["tenant_key"]))


async def resolve_tenant(
    session: AsyncSession, *, user_id: str, tenant_key: str | None
) -> tuple[str, str] | None:
    """显式 key 白名单校验;缺省取默认租户。返回 (tenant_id, tenant_key);
    无有效租户 None(403/400 形态由调用方决定)。"""
    if tenant_key is not None:
        tid = await bound_tenant_id(session, user_id=user_id, tenant_key=tenant_key)
        return None if tid is None else (tid, tenant_key)
    return await default_tenant(session, user_id=user_id)
