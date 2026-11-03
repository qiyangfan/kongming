# pyright: reportPrivateUsage=false
"""答案异步评分管线测试:仓储幂等/材料装配守卫/任务降级收敛与落库。

使用真实 PG(test 库,conftest 迁移到 head);agent 用鸭子类型替身
(score_answer 回调契约),不触真实 LLM。"""

import json
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.seed import seed_all
from kongming.chat.scoring_repo import SqlScoreRepo
from kongming.chat.scoring_tasks import _assemble_material, score_answer_task
from kongming.config import get_settings
from kongming.uuid7 import uuid7

_DEC_FULL = {
    "claims": [
        {"text": "上月销售额 100 万", "importance": "critical", "support": "fully_supported"},
        {"text": "明细 3 行", "importance": "minor", "support": "fully_supported"},
    ],
    "sub_questions": [{"question": "上月销售额", "answered": True}],
}


async def _mk_turn(
    s: AsyncSession,
    user_id: str,
    tenant_id: str,
    *,
    status: str = "SUCCESS",
    result_json: str | None = None,
    summary: str | None = "上月销售额 100 万,明细 3 行。",
    question: str = "上月销售额是多少",
) -> str:
    """插最小轮次链(user→session→message),返回 message_id。"""
    sid = str(uuid7())
    mid = str(uuid7())
    await s.execute(
        text("INSERT INTO public.chat_session (id, user_id, tenant_id) VALUES (:id, :u, :t)"),
        {"id": sid, "u": user_id, "t": tenant_id},
    )
    await s.execute(
        text(
            "INSERT INTO public.chat_message"
            " (id, session_id, role, question, result_json, summary, status)"
            " VALUES (:id, :sid, 'ASSISTANT', :q, CAST(:rj AS jsonb), :sum, :st)"
        ),
        {"id": mid, "sid": sid, "q": question, "rj": result_json, "sum": summary, "st": status},
    )
    await s.commit()
    return mid


@pytest.fixture
async def user_ids(session: AsyncSession, make_user: Any) -> tuple[str, str]:
    from tests.test_chat_query import _default_tenant_id

    await seed_all(session, get_settings())
    uid = await make_user("score-u")
    return uid, await _default_tenant_id(session)


_DATA_RESULT = json.dumps(
    {
        "columns": ["month", "amount"],
        "rows": [["2026-09", 1000000]],
        "stats": {"rows": 1},
        "sources": [],
    }
)


class TestScoreRepo:
    async def test_insert_idempotent_and_get(
        self, session: AsyncSession, user_ids: tuple[str, str]
    ) -> None:
        uid, tid = user_ids
        mid = await _mk_turn(session, uid, tid, result_json=_DATA_RESULT)
        repo = SqlScoreRepo(session)
        assert await repo.insert_if_absent(
            message_id=mid,
            score=88,
            grounding=90,
            coverage=85,
            decomposition={"claims": [], "excluded_claims": []},
            score_model="m1",
        )
        again = await repo.insert_if_absent(
            message_id=mid,
            score=10,
            grounding=10,
            coverage=10,
            decomposition={"claims": []},
            score_model="m2",
        )
        assert again is False  # 幂等跳过,既有记录不变
        row = await repo.get_by_message(mid)
        assert row is not None
        assert row["score"] == 88 and row["score_model"] == "m1"
        assert await repo.get_by_message(str(uuid7())) is None


class TestAssembleMaterial:
    async def test_success_data_turn_builds_evidence(
        self, session: AsyncSession, user_ids: tuple[str, str]
    ) -> None:
        uid, tid = user_ids
        mid = await _mk_turn(session, uid, tid, result_json=_DATA_RESULT)
        material, trace_id, tenant_key = await _assemble_material(mid)
        assert material is not None
        assert "上月销售额是多少" in material
        assert "amount" in material and "1000000" in material  # 行列证据
        # 用量归因维度=租户键(default),与问答链路同口径;trace_id 空=无关联用量
        assert tenant_key == "default" and trace_id == ""

    async def test_non_success_or_missing_skip(
        self, session: AsyncSession, user_ids: tuple[str, str]
    ) -> None:
        uid, tid = user_ids
        failed = await _mk_turn(session, uid, tid, status="FAILED", result_json=_DATA_RESULT)
        assert (await _assemble_material(failed))[0] is None
        assert (await _assemble_material(str(uuid7())))[0] is None  # 不存在

    async def test_no_evidence_skips(
        self, session: AsyncSession, user_ids: tuple[str, str]
    ) -> None:
        uid, tid = user_ids
        empty = await _mk_turn(
            session, uid, tid, result_json=json.dumps({"columns": [], "rows": [], "sources": []})
        )
        assert (await _assemble_material(empty))[0] is None


class TestScoreAnswerTask:
    async def test_happy_path_persists_score(
        self, session: AsyncSession, user_ids: tuple[str, str]
    ) -> None:
        uid, tid = user_ids
        mid = await _mk_turn(session, uid, tid, result_json=_DATA_RESULT)

        class _Agent:
            async def score_answer(self, *, material: str, trace_id: str, tenant_key: str) -> dict:
                return {"decomposition": _DEC_FULL, "score_model": "judge-x"}

        await score_answer_task(message_id=mid, agent=_Agent())  # type: ignore[arg-type]
        row = await SqlScoreRepo(session).get_by_message(mid)
        assert row is not None
        assert row["score"] == 100 and row["score_model"] == "judge-x"
        dec = row["decomposition"]
        assert dec["points_covered"] == 1  # 审计分解随档

    async def test_degradation_silent(
        self, session: AsyncSession, user_ids: tuple[str, str]
    ) -> None:
        uid, tid = user_ids
        mid = await _mk_turn(session, uid, tid, result_json=_DATA_RESULT)

        class _DownAgent:
            async def score_answer(self, *, material: str, trace_id: str, tenant_key: str) -> None:
                return None  # 评审降级契约

        class _RaiseAgent:
            async def score_answer(self, *, material: str, trace_id: str, tenant_key: str) -> None:
                raise RuntimeError("boom")

        await score_answer_task(message_id=mid, agent=_DownAgent())  # type: ignore[arg-type]
        await score_answer_task(message_id=mid, agent=_RaiseAgent())  # type: ignore[arg-type]
        assert await SqlScoreRepo(session).get_by_message(mid) is None  # 不落分
