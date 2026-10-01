"""角色启用状态在所有授权读取点同一道闸(086)。

背景:`sys_role.status` 原先只有 `datascope.user_role_scopes` 一处读它,权限点联查与
四处 `data_scope='ALL'` 判定都不读——于是「禁用一个 ALL 角色」在不同链路上得到相反结果:
数据范围侧消失(进而降级为不过滤),可见面侧照旧放宽。这些用例钉住「要么全程参与,
要么完全不参与」这条口径,新增按角色联查的读取点时必须一并覆盖。
"""

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
