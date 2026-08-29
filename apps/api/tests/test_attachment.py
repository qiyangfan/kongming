"""030 multimodal-input api 侧测试:上传限制/归属隔离/一次性消费/
chat 链路引用校验与摘要回显(测试先行,安全类)。"""
# pyright: reportPrivateUsage=false

import io

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.attachment.models import sniff_mime
from kongming.auth.seed import seed_all
from kongming.config import get_settings
from tests.conftest import MakeUser
from tests.test_chat_query import _auth, _bind, _default_tenant_id, _login

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
_GIF = b"GIF89a" + b"\x00" * 32


def test_sniff_mime() -> None:
    assert sniff_mime(_PNG) == "image/png"
    assert sniff_mime(_GIF) == "image/gif"
    assert sniff_mime(b"RIFF" + b"\x00" * 4 + b"WEBP" + b"\x00") == "image/webp"
    assert sniff_mime(b"RIFF" + b"\x00" * 4 + b"WAVE" + b"\x00") is None  # 伪装 webp
    assert sniff_mime(b"<?xml version=") is None
    assert sniff_mime(b"%PDF-1.4") is None  # 非白名单


async def _mk_user(
    client: AsyncClient, s: AsyncSession, make_user: MakeUser, name: str
) -> tuple[str, str]:
    await seed_all(s, get_settings())
    uid = await make_user(name)
    rid = (
        await s.execute(text("SELECT id FROM public.sys_role WHERE role_key='DEVELOPER'"))
    ).scalar_one()
    await _bind(s, "sys_user_role", uid, str(rid))
    await _bind(s, "sys_user_tenant", uid, await _default_tenant_id(s))
    await s.commit()
    return uid, await _login(client, name)


async def _upload(client: AsyncClient, token: str, data: bytes = _PNG, name: str = "a.png"):
    return await client.post(
        "/chat/attachments",
        headers=_auth(token),
        files={"file": (name, io.BytesIO(data), "image/png")},
    )


async def test_upload_limits_and_magic(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「类型与大小限制」:伪造 Content-Type/非图片/超限全拒。"""
    _, token = await _mk_user(client, session, make_user, "att_up1")
    r = await _upload(client, token)
    assert r.status_code == 201, r.text
    assert r.json()["mime"] == "image/png"
    # 伪装 Content-Type 的非图片(text 内容冒充 png)
    r2 = await client.post(
        "/chat/attachments",
        headers=_auth(token),
        files={"file": ("evil.png", io.BytesIO(b"#!/bin/bash\necho hi"), "image/png")},
    )
    assert r2.status_code == 400
    assert r2.json()["detail"] == "ATTACHMENT_TYPE_INVALID"
    # 审计落库
    n = (
        await session.execute(
            text("SELECT count(*) FROM public.sys_oper_log WHERE action='attachment.upload'")
        )
    ).scalar_one()
    assert n >= 1


async def test_non_default_tenant_upload_reference(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """R1-S1 回归:非默认租户上传的附件按该租户归属(上传端点读
    X-Tenant-Id);上传后引用过归属校验(不再 404)。"""
    uid, token = await _mk_user(client, session, make_user, "att_t1")
    # 绑定第二租户 sakila(种子既有)并以其上传
    sakila_tid = (
        await session.execute(
            text(
                "INSERT INTO public.sys_tenant (id, tenant_key, tenant_name, status)"
                " VALUES (gen_random_uuid(), 'sakila', 'sakila', 'ACTIVE')"
                " ON CONFLICT (tenant_key) DO UPDATE SET tenant_key=EXCLUDED.tenant_key"
                " RETURNING id"
            )
        )
    ).scalar_one()
    await _bind(session, "sys_user_tenant", uid, str(sakila_tid))
    await session.commit()
    r = await client.post(
        "/chat/attachments",
        headers={**_auth(token), "X-Tenant-Id": "sakila"},
        files={"file": ("t.png", io.BytesIO(_PNG), "image/png")},
    )
    assert r.status_code == 201, r.text
    att_id = r.json()["attachment_id"]
    # 落库租户=sakila
    row_tenant = (
        await session.execute(
            text(
                "SELECT t.tenant_key FROM public.sys_attachment a"
                " JOIN public.sys_tenant t ON t.id=a.tenant_id"
                " WHERE a.id=CAST(:i AS uuid)"
            ),
            {"i": att_id},
        )
    ).scalar_one()
    assert row_tenant == "sakila"
    # 该租户下引用过归属校验(非 404;agent 可达性不定,断言不是 404/403)
    r2 = await client.post(
        "/chat/query",
        headers={**_auth(token), "X-Tenant-Id": "sakila"},
        json={"question": "看图", "persist": False, "attachment_ids": [att_id]},
    )
    assert r2.status_code == 200
    # 默认租户视角引用同一附件 → 404 同形(跨租户隔离)
    r3 = await client.post(
        "/chat/query",
        headers=_auth(token),
        json={"question": "看图", "persist": False, "attachment_ids": [att_id]},
    )
    assert r3.status_code == 404


async def test_cross_user_reference_404(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「跨用户引用拒绝」:B 引用 A 的附件 → 404 同形,不进回调。"""
    _, token_a = await _mk_user(client, session, make_user, "att_a")
    _, token_b = await _mk_user(client, session, make_user, "att_b")
    att_id = (await _upload(client, token_a)).json()["attachment_id"]
    r = await client.post(
        "/chat/query",
        headers=_auth(token_b),
        json={"question": "看图", "persist": False, "attachment_ids": [att_id]},
    )
    assert r.status_code == 404


async def test_internal_content_one_time_consume(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「一次性消费」:取一次 200+文件删;二次 404;无令牌 403。"""
    uid, token = await _mk_user(client, session, make_user, "att_c1")
    att_id = (await _upload(client, token)).json()["attachment_id"]
    from kongming.attachment.repository import ATTACH_DIR

    path = ATTACH_DIR / f"{att_id}.png"
    assert path.exists()
    internal = {"X-Internal-Token": "test-internal-token"}
    r = await client.get(
        f"/internal/attachment/{att_id}/content",
        headers=internal,
        params={"tenant_key": "default", "user_id": uid},
    )
    assert r.status_code == 200
    assert r.content == _PNG
    assert not path.exists()  # 文件即删
    r2 = await client.get(
        f"/internal/attachment/{att_id}/content",
        headers=internal,
        params={"tenant_key": "default", "user_id": uid},
    )
    assert r2.status_code == 404  # 二次取用拒
    r3 = await client.get(
        f"/internal/attachment/{att_id}/content",
        params={"tenant_key": "default", "user_id": uid},
    )
    assert r3.status_code == 403  # 无内部令牌(中间件)


async def test_query_with_attachments_echo(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「带附件问答落库」:归属过检 → 消息摘要落库回显(agent 替身
    免真 LLM——附件内容由 agent 侧预处理,api 只验归属+透传+落摘要)。"""
    uid, token = await _mk_user(client, session, make_user, "att_q1")
    att_id = (await _upload(client, token)).json()["attachment_id"]
    r = await client.post(
        "/chat/query",
        headers=_auth(token),
        json={"question": "看图", "persist": True, "attachment_ids": [att_id]},
    )
    # agent 可达性由 dev 环境决定;此处断言归属校验通过(非 404)且
    # 若 agent 在线则全链 200 含摘要
    assert r.status_code == 200
    body = r.json()
    sid = body["session_id"]
    r2 = await client.get(f"/chat/sessions/{sid}/messages", headers=_auth(token))
    msgs = r2.json()
    user_msg = next(m for m in msgs if m["role"] == "USER")
    raw = (
        await session.execute(
            text("SELECT attachments FROM public.chat_message WHERE id=CAST(:i AS uuid)"),
            {"i": user_msg["id"]},
        )
    ).scalar_one()
    assert raw and raw[0].get("filename") == "a.png", (
        f"落库异常: {raw!r} 回显: {user_msg['attachments']!r}"
    )
    assert user_msg["attachments"][0]["filename"] == "a.png"
    assert user_msg["attachments"][0]["size_bytes"] == len(_PNG)
    assert uid is not None
