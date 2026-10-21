"""验证所有授权查询都排除已停用角色，确保权限点和数据范围采用一致的角色集合。"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.audit.repository import AuditRepository
from kongming.feedback.repository import FeedbackRepository
from kongming.quality.repository import SqlQualityRepo
from kongming.toolcall.repository import user_has_all_scope


async def _mk_user_with_role(session: AsyncSession, name: str, scope: str, status: str) -> str:
    uid = (
        await session.execute(
            text(
                "INSERT INTO public.sys_user (id, username)"
                " VALUES (gen_random_uuid(), :n) RETURNING id"
            ),
            {"n": name},
        )
    ).scalar_one()
    rid = (
        await session.execute(
            text(
                "INSERT INTO public.sys_role (id, role_key, role_name, data_scope, status)"
                " VALUES (gen_random_uuid(), :k, :k, :ds, :st) RETURNING id"
            ),
            {"k": f"R_{name}", "ds": scope, "st": status},
        )
    ).scalar_one()
    await session.execute(
        text("INSERT INTO public.sys_user_role (user_id, role_id) VALUES (:u, :r)"),
        {"u": uid, "r": rid},
    )
    await session.commit()
    return str(uid)


async def test_all_scope_gate_requires_active_role(session: AsyncSession) -> None:
    """同一角色图上的四处 ALL 判定:启用角色兑现,非启用角色不兑现。"""
    on = await _mk_user_with_role(session, "gate-on", "ALL", "ACTIVE")
    off = await _mk_user_with_role(session, "gate-off", "ALL", "DISABLED")

    assert await AuditRepository(session).user_has_scope_all(user_id=on) is True
    assert await FeedbackRepository(session).user_has_scope_all(user_id=on) is True
    assert await user_has_all_scope(session, on) is True
    assert await SqlQualityRepo(session).user_has_scope_all(user_id=on) is True

    assert await SqlQualityRepo(session).user_has_scope_all(user_id=off) is False
    assert await AuditRepository(session).user_has_scope_all(user_id=off) is False
    assert await FeedbackRepository(session).user_has_scope_all(user_id=off) is False
    assert await user_has_all_scope(session, off) is False
