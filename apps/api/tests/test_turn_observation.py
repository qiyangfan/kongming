# pyright: reportPrivateUsage=false
"""观测深化仓储层测试(spec「同会话轮次串联」「节点阶段轨迹持久化」
「轮次质量导出」):串联升序与锚点必含/越权 None/轨迹读取/导出筛选。"""

import json
from typing import Any

import pytest
from sqlalchemy import text

from kongming.auth.seed import seed_all
from kongming.config import get_settings
from kongming.quality.repository import SqlQualityRepo
from kongming.uuid7 import uuid7


@pytest.fixture
async def scope(session: Any, make_user: Any) -> tuple[str, str]:
    from tests.test_chat_query import _default_tenant_id

    await seed_all(session, get_settings())
    uid = await make_user("obs-u")
    return uid, await _default_tenant_id(session)


async def _mk_turn(
    s: Any,
    uid: str,
    tid: str,
    *,
    question: str,
    fallback_type: str | None = None,
    stage_trace: list[dict[str, object]] | None = None,
    offset_sec: int = 0,  # created_at 前推偏移(同毫秒插入会落入 tiebreak,不测时序边界)
    sid: str | None = None,  # 复用会话(串联测试三轮须同会话);缺省新建
) -> str:
    sid = sid or str(uuid7())
    mid = str(uuid7())
    exists = (
        await s.execute(text("SELECT 1 FROM public.chat_session WHERE id = :i"), {"i": sid})
    ).scalar()
    if not exists:
        await s.execute(
            text("INSERT INTO public.chat_session (id, user_id, tenant_id) VALUES (:i,:u,:t)"),
            {"i": sid, "u": uid, "t": tid},
        )
    await s.execute(
        text(
            "INSERT INTO public.chat_message"
            " (id, session_id, role, question, status, fallback_type, stage_trace, created_at)"
            " VALUES (:i, :s, 'ASSISTANT', :q, 'NO_DATA', :fb, CAST(:stg AS jsonb),"
            "          now() - make_interval(secs => :off))"
        ),
        {
            "i": mid,
            "s": sid,
            "q": question,
            "fb": fallback_type,
            "stg": json.dumps(stage_trace, ensure_ascii=False) if stage_trace else None,
            "off": offset_sec,
        },
    )
    await s.commit()
    return mid


class TestSessionContext:
    async def test_ascending_with_anchor_included(
        self, session: Any, scope: tuple[str, str]
    ) -> None:
        uid, tid = scope
        sid = str(uuid7())  # 三轮同会话(串联前提)
        first = await _mk_turn(session, uid, tid, question="第一轮", offset_sec=20, sid=sid)
        second = await _mk_turn(
            session,
            uid,
            tid,
            question="第二轮",
            fallback_type="CLARIFY",
            offset_sec=10,
            sid=sid,
        )
        third = await _mk_turn(
            session,
            uid,
            tid,
            question="第三轮",
            stage_trace=[{"node": "understand", "status": "done"}],
            sid=sid,
        )
        repo = SqlQualityRepo(session)
        # 锚定最新轮:窗口=锚点及此前全部轮,升序,锚点必含
        ctx = await repo.session_context(tenant_ids=[tid], message_id=third, limit=50)
        assert ctx is not None
        ids = [str(it["id"]) for it in ctx["items"]]
        assert ids == [first, second, third]  # 时间升序
        assert ctx["items"][1]["fallback_type"] == "CLARIFY"
        # 锚定中间轮:窗口截到锚点为止(上限内锚点恒在)
        ctx_mid = await repo.session_context(tenant_ids=[tid], message_id=second, limit=50)
        assert ctx_mid is not None
        assert [str(it["id"]) for it in ctx_mid["items"]] == [first, second]

    async def test_invisible_anchor_yields_none(self, session: Any, scope: tuple[str, str]) -> None:
        uid, tid = scope
        mid = await _mk_turn(session, uid, tid, question="x")
        repo = SqlQualityRepo(session)
        # 查询方绑定租户集不含锚点租户 → 收敛视角不可见 → None(404 同形)
        assert (
            await repo.session_context(tenant_ids=[str(uuid7())], message_id=mid, limit=50) is None
        )
        # 锚点不存在 → None(与越权同形 404)
        assert (
            await repo.session_context(tenant_ids=None, message_id=str(uuid7()), limit=50) is None
        )


class TestStageTraceDetail:
    async def test_streamed_turn_returns_trace(self, session: Any, scope: tuple[str, str]) -> None:
        uid, tid = scope
        trace: list[dict[str, object]] = [
            {"node": "understand", "status": "running", "retry": 0, "t": 0}
        ]
        mid = await _mk_turn(session, uid, tid, question="q", stage_trace=trace)
        detail = await SqlQualityRepo(session).stage_trace_detail(tenant_ids=[tid], message_id=mid)
        assert detail is not None
        assert detail["stage_trace"] == trace

    async def test_sync_turn_returns_none_trace(self, session: Any, scope: tuple[str, str]) -> None:
        uid, tid = scope
        mid = await _mk_turn(session, uid, tid, question="q")
        detail = await SqlQualityRepo(session).stage_trace_detail(tenant_ids=[tid], message_id=mid)
        assert detail is not None and detail["stage_trace"] is None  # 同步轮如实为空

    async def test_invisible_or_missing_yields_none(
        self, session: Any, scope: tuple[str, str]
    ) -> None:
        uid, tid = scope
        mid = await _mk_turn(session, uid, tid, question="q")
        repo = SqlQualityRepo(session)
        # 查询方绑定租户集不含锚点租户 → None(路由映射 404,与 score/context 同形防探测)
        assert await repo.stage_trace_detail(tenant_ids=[str(uuid7())], message_id=mid) is None
        # 锚点不存在 → None(与越权同形 404)
        assert await repo.stage_trace_detail(tenant_ids=None, message_id=str(uuid7())) is None


class TestExportRows:
    async def test_filters_apply(self, session: Any, scope: tuple[str, str]) -> None:
        uid, tid = scope
        await _mk_turn(session, uid, tid, question="闲聊轮", fallback_type="CHITCHAT")
        await _mk_turn(session, uid, tid, question="真实轮")
        repo = SqlQualityRepo(session)
        all_rows = await repo.export_rows(
            tenant_ids=[tid],
            status=None,
            datasource_key=None,
            fallback=None,
            start=None,
            end=None,
            limit=100,
        )
        assert len(all_rows) == 2
        fb_rows = await repo.export_rows(
            tenant_ids=[tid],
            status=None,
            datasource_key=None,
            fallback="CHITCHAT",
            start=None,
            end=None,
            limit=100,
        )
        assert len(fb_rows) == 1 and fb_rows[0]["question"] == "闲聊轮"
        none_rows = await repo.export_rows(
            tenant_ids=[tid],
            status=None,
            datasource_key=None,
            fallback="none",
            start=None,
            end=None,
            limit=100,
        )
        assert len(none_rows) == 1 and none_rows[0]["question"] == "真实轮"
        # 行集携带串联/评分依赖字段(session_id/score 列在位)
        assert "session_id" in all_rows[0] and "score" in all_rows[0]
