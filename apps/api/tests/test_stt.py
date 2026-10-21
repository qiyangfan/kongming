"""语音转写端点测试:mock 引擎注入(引擎 Protocol 接口反转),
覆盖成功转写/空结果/超限/引擎不可用 503/权限 403;审计断言(转写正文
不进日志)。真实推理测试见文件末(模型在位才跑,缺失 skip)。
"""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.seed import seed_all
from kongming.config import get_settings
from kongming.main import app
from kongming.stt.ports import EngineUnavailable, WhisperEngine
from kongming.stt.router import get_stt_service
from kongming.stt.service import AudioTooLarge, SttService
from tests.conftest import MakeUser


class _FakeEngine:
    def __init__(self, out: str | None = None, err: Exception | None = None) -> None:
        self.out = out
        self.err = err
        self.received: bytes | None = None

    async def transcribe(self, audio: bytes) -> str:
        self.received = audio
        if self.err is not None:
            raise self.err
        return self.out or ""


def _svc(engine: WhisperEngine) -> SttService:
    return SttService(engine)


@pytest.fixture
def fake_stt() -> Any:
    holder: dict[str, SttService] = {}

    def _factory() -> SttService:
        return holder["svc"]

    app.dependency_overrides[get_stt_service] = _factory
    yield holder
    app.dependency_overrides.pop(get_stt_service, None)


def _install(holder: dict[str, SttService], svc: SttService) -> None:
    holder["svc"] = svc


# ============ 用例层(纯 service 逻辑) ============


async def test_transcribe_success_folds_whitespace() -> None:
    eng = _FakeEngine("  各上映\n年份的  影片数量 ")
    text = await _svc(eng).transcribe(b"audio-bytes")
    assert text == "各上映 年份的 影片数量"
    assert eng.received == b"audio-bytes"


async def test_transcribe_empty_result_passes_through() -> None:
    """纯静音→空串(不假装成功;前端提示未识别)。"""
    assert await _svc(_FakeEngine("")).transcribe(b"silence") == ""


async def test_transcribe_oversize_rejected_before_engine() -> None:
    eng = _FakeEngine("x")
    with pytest.raises(AudioTooLarge):
        await _svc(eng).transcribe(b"a" * (15 * 1024 * 1024 + 1))
    assert eng.received is None  # 超限不触引擎


async def test_transcribe_engine_unavailable_wrapped() -> None:
    with pytest.raises(EngineUnavailable):
        await _svc(_FakeEngine(err=EngineUnavailable("模型缺失"))).transcribe(b"a")
    # 引擎任意异常收敛为 EngineUnavailable(503 形态,不泄内部细节)
    with pytest.raises(EngineUnavailable):
        await _svc(_FakeEngine(err=RuntimeError("internal-detail"))).transcribe(b"a")


# ============ 端点形态(mock 引擎依赖覆盖) ============


async def _mk_user_with_chat_perm(
    client: AsyncClient, s: AsyncSession, make_user: MakeUser, username: str
) -> str:
    """chat:execute + default 租户用户(test_chat_query 同款形态)。"""
    await seed_all(s, get_settings())
    uid = await make_user(username)
    rid = (
        await s.execute(text("SELECT id FROM public.sys_role WHERE role_key='DEVELOPER'"))
    ).scalar_one()
    await s.execute(
        text("INSERT INTO public.sys_user_role (user_id, role_id) VALUES (:u, :r)"),
        {"u": uid, "r": str(rid)},
    )
    tid = (
        await s.execute(text("SELECT id FROM public.sys_tenant WHERE tenant_key='default'"))
    ).scalar_one()
    await s.execute(
        text("INSERT INTO public.sys_user_tenant (user_id, tenant_id) VALUES (:u, :t)"),
        {"u": uid, "t": str(tid)},
    )
    await s.commit()
    r = await client.post("/auth/login", json={"username": username, "password": "Passw0rd!"})
    assert r.status_code == 200
    return str(r.json()["access_token"])


async def test_endpoint_roundtrip_and_audit(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_stt: dict[str, SttService],
) -> None:
    """200 + 文本;审计 action 落行且转写正文不进 detail。"""
    token = await _mk_user_with_chat_perm(client, session, make_user, "stt_user")
    _install(fake_stt, _svc(_FakeEngine("各上映年份的影片数量")))
    r = await client.post(
        "/chat/stt",
        files={"file": ("a.webm", b"fake-audio", "audio/webm")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert r.json()["text"] == "各上映年份的影片数量"
    rows = (
        await session.execute(
            text(
                "SELECT detail FROM public.sys_oper_log"
                " WHERE action='chat.stt.transcribe' ORDER BY created_at DESC LIMIT 1"
            )
        )
    ).all()
    assert rows and "各上映" not in str(rows[0][0])


async def test_endpoint_unavailable_503(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_stt: dict[str, SttService],
) -> None:
    token = await _mk_user_with_chat_perm(client, session, make_user, "stt_una")
    _install(fake_stt, _svc(_FakeEngine(err=EngineUnavailable("模型缺失"))))
    r = await client.post(
        "/chat/stt",
        files={"file": ("a.webm", b"fake-audio", "audio/webm")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 503
    assert "STT_UNAVAILABLE" in r.json()["detail"]


async def test_endpoint_oversize_413(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_stt: dict[str, SttService],
) -> None:
    token = await _mk_user_with_chat_perm(client, session, make_user, "stt_big")
    _install(fake_stt, _svc(_FakeEngine("x")))
    r = await client.post(
        "/chat/stt",
        files={"file": ("a.webm", b"a" * (15 * 1024 * 1024 + 1), "audio/webm")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 413


async def test_endpoint_no_perm_403(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    await seed_all(session, get_settings())
    uid = await make_user("stt_noperm")
    rid = (
        await session.execute(
            text(
                "INSERT INTO public.sys_role (id, role_key, role_name, data_scope)"
                " VALUES (gen_random_uuid(), 'NO_STT', 'x', 'SELF') RETURNING id"
            )
        )
    ).scalar_one()
    await session.execute(
        text("INSERT INTO public.sys_user_role (user_id, role_id) VALUES (:u, :r)"),
        {"u": uid, "r": str(rid)},
    )
    tid = (
        await session.execute(text("SELECT id FROM public.sys_tenant WHERE tenant_key='default'"))
    ).scalar_one()
    await session.execute(
        text("INSERT INTO public.sys_user_tenant (user_id, tenant_id) VALUES (:u, :t)"),
        {"u": uid, "t": str(tid)},
    )
    await session.commit()
    r = await client.post("/auth/login", json={"username": "stt_noperm", "password": "Passw0rd!"})
    token = str(r.json()["access_token"])
    r = await client.post(
        "/chat/stt",
        files={"file": ("a.webm", b"x", "audio/webm")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 403


# ============ 真实推理(模型在位才跑;缺失 skip) ============


def _fw_available() -> bool:
    import importlib.util

    return importlib.util.find_spec("faster_whisper") is not None


@pytest.mark.skipif(not _fw_available(), reason="faster-whisper 未安装")
async def test_real_engine_with_silence_wav() -> None:
    """真实引擎真跑:1s 静音 wav(PyAV 解码+模型转写全链)——断言返回
    字符串即通(无语音内容,不做语义断言;模型预下载配方见 HANDOFF)。"""
    import io
    import wave

    from kongming.stt.engine import FasterWhisperEngine

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 16000)
    text = await FasterWhisperEngine("tiny").transcribe(buf.getvalue())
    assert isinstance(text, str)
