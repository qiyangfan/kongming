"""028 mcp-interop 测试:纯函数/客户端双形态/管理面安全矩阵/执行链/
确认流重放/目录合并//mcp 入站(测试先行,安全类)。

假 MCP server=进程内 ASGI 应用(无外部网络;httpx ASGITransport 注入)。
"""
# pyright: reportPrivateUsage=false

import json
import uuid
from collections.abc import Iterator, MutableMapping
from typing import Any, cast

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.seed import seed_all
from kongming.config import get_settings
from kongming.mcp.client import _sse_events
from kongming.mcp.models import merge_discovered, parse_qualified, qualified_name
from tests.conftest import MakeUser
from tests.test_chat_query import _bind, _default_tenant_id, _login
from tests.test_toolcall import (
    INTERNAL,
    _mk_role_with_perms,
    _mk_session,
    _req,
)

# ── 假 MCP server(进程内 ASGI) ──

_FAKE_TOOLS = [
    {
        "name": "read_thing",
        "description": "读取一个东西",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "write_thing",  # 无 readOnlyHint → fail-closed 写工具
        "description": "写一个东西",
        "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}},
    },
    {
        "name": "read_thing",  # 同 server 内重复名(冲突跳过面)
        "description": "重复注册",
        "inputSchema": {"type": "object"},
    },
]


async def _fake_mcp_app(scope: MutableMapping[str, Any], receive: Any, send: Any) -> None:  # noqa: ANN001, ANN202  # ASGI 裸签名
    assert scope["type"] == "http"
    body = b""
    while True:
        msg = await receive()
        if msg["type"] == "http.request":
            body += msg.get("body", b"")
            if not msg.get("more_body"):
                break
    headers = {k.decode(): v.decode() for k, v in scope.get("headers", [])}
    if headers.get("x-test-slow"):
        import asyncio

        await asyncio.sleep(2)  # 客户端 0.2s 超时先于其完成
    req: dict[str, Any]
    try:
        req = json.loads(body or b"{}")
    except json.JSONDecodeError:
        req = {}
    method = req.get("method")
    rpc_id = req.get("id", 1)
    if method == "initialize":
        result: dict[str, Any] = {
            "protocolVersion": "2025-03-26",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "fake", "version": "0"},
        }
    elif method == "tools/list":
        result = {"tools": _FAKE_TOOLS}
    elif method == "tools/call":
        params = cast("dict[str, Any]", req.get("params") or {})
        name = params.get("name")
        args = cast("dict[str, Any]", params.get("arguments") or {})
        if headers.get("x-test-big"):
            result = {
                "content": [{"type": "text", "text": "y" * (1100 * 1024)}],
                "isError": False,
            }
        elif name == "read_thing":
            result = {
                "content": [{"type": "text", "text": f"读到: {args.get('path')}"}],
                "isError": False,
            }
        else:
            result = {
                "content": [{"type": "text", "text": f"写入: {args.get('path')}"}],
                "isError": False,
            }
    else:
        result = {"serverInfo": {"name": "fake"}}  # ping 等杂项
    payload = json.dumps(
        {"jsonrpc": "2.0", "id": rpc_id, "result": result}, ensure_ascii=False
    ).encode()
    if headers.get("x-test-sse"):
        framed = b"event: message\ndata: " + payload + b"\n\n"
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/event-stream")],
            }
        )
        await send({"type": "http.response.body", "body": framed})
        return
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": payload})


async def _mk_tenant_once(s: AsyncSession, key: str = "mcp") -> str:
    """幂等建租户(同测试内多助手复用;_mk_tenant 无 ON CONFLICT 会撞唯一键)。"""
    tid = (
        await s.execute(
            text(
                "INSERT INTO public.sys_tenant (id, tenant_key, tenant_name, status)"
                " VALUES (gen_random_uuid(), :k, :k, 'ACTIVE')"
                " ON CONFLICT (tenant_key) DO NOTHING RETURNING id"
            ),
            {"k": key},
        )
    ).scalar_one_or_none()
    if tid is None:
        tid = (
            await s.execute(
                text("SELECT id FROM public.sys_tenant WHERE tenant_key=:k"), {"k": key}
            )
        ).scalar_one()
    return str(tid)


def _fake_caller(timeout: float = 15) -> Any:
    from kongming.mcp.client import HttpMcpCaller

    return HttpMcpCaller(
        timeout_sec=cast("int", timeout), transport=ASGITransport(app=_fake_mcp_app)
    )


def _caller_headers(kind: str) -> dict[str, str]:
    return {"X-Test-Sse": "1"} if kind == "sse" else {}


# ── 纯函数 ──


def test_qualified_name_roundtrip() -> None:
    q = qualified_name("fs", "read_file")
    assert q == "mcp__fs__read_file"
    assert parse_qualified(q) == ("fs", "read_file")
    assert parse_qualified("execute_sql") is None
    assert parse_qualified("mcp__bad key__x") is None  # key 形态校验
    assert parse_qualified("mcp__fs__") is None
    assert parse_qualified("mcp__fs__a__b") == ("fs", "a__b")  # 残余全收


def test_merge_discovered_conflict() -> None:
    from kongming.mcp.models import DiscoveredTool

    tools = [
        DiscoveredTool(name="a", description="1", schema={}, read_only=True),
        DiscoveredTool(name="a", description="2", schema={}, read_only=False),
        DiscoveredTool(name="b", description="3", schema={}, read_only=False),
    ]
    entries, conflicts = merge_discovered("fs", tools)
    assert [e["qualified"] for e in entries] == ["mcp__fs__a", "mcp__fs__b"]
    assert entries[0]["description"] == "1"  # 先注册方胜
    assert conflicts == ["mcp__fs__a"]


def test_merge_discovered_overlong_skipped() -> None:
    """R1-A4:注册全名超 64 字符的工具同步期跳过并告警(死工具防注册)。"""
    from kongming.mcp.models import DiscoveredTool

    tools = [DiscoveredTool(name="t" * 70, description="", schema={}, read_only=True)]
    entries, conflicts = merge_discovered("fs", tools)
    assert entries == []
    assert conflicts and conflicts[0].startswith("超长跳过")


def test_sse_events_parse() -> None:
    text = 'event: message\ndata: {"a":1}\n\n: comment\ndata: {"b":\ndata: 2}\n\n'
    assert _sse_events(text) == ['{"a":1}', '{"b":\n2}']
    assert _sse_events("") == []


# ── 客户端(ASGI 假 server) ──


async def test_client_list_tools_and_fail_closed() -> None:
    caller = _fake_caller()
    tools = await caller.list_tools(endpoint="http://fake/mcp", headers={})
    names = [t.name for t in tools]
    assert names[:2] == ["read_thing", "write_thing"]
    assert tools[0].read_only is True
    assert tools[1].read_only is False  # 未标注 fail-closed


async def test_client_sse_response_form() -> None:
    caller = _fake_caller()
    out = await caller.call(
        endpoint="http://fake/mcp",
        headers=_caller_headers("sse"),
        tool_name="read_thing",
        args={"path": "/a"},
    )
    assert "读到: /a" in out["content"]


async def test_client_timeout_classification() -> None:
    """超时分类直证:注入抛 ReadTimeout 的 transport(ASGITransport 不支持
    超时——实测 DID NOT RAISE,故确定性注入)。"""
    import httpx

    from kongming.mcp.client import HttpMcpCaller
    from kongming.mcp.ports import McpCallError

    class _TimeoutTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("simulated", request=request)

    caller = HttpMcpCaller(transport=_TimeoutTransport())
    with pytest.raises(McpCallError) as ei:
        await caller.call(endpoint="http://fake/mcp", headers={}, tool_name="read_thing", args={})
    assert ei.value.kind == "timeout"


async def test_client_call_truncation() -> None:
    caller = _fake_caller()
    out = await caller.call(
        endpoint="http://fake/mcp",
        headers={"X-Test-Big": "1"},
        tool_name="read_thing",
        args={},
    )
    assert out["truncated"] is True
    assert out["original_size"] > 1024 * 1024
    assert len(out["content"].encode("utf-8")) <= 1024 * 1024  # 语义截断整洁


def test_validate_args_hostile_schema_converges() -> None:
    """R1-S2:畸形/敌意 schema 不再逃逸为 500——收敛 ARGS_INVALID。"""
    from kongming.toolcall.models import ToolDef
    from kongming.toolcall.service import validate_args

    tool = ToolDef(
        name="mcp__evil__x",
        description="",
        parameters={"$ref": "#/definitions/nope"},
        perm_code="tool:mcp:use",
        write=False,
        require_confirm=False,
    )
    err = validate_args(tool, {"a": 1})
    assert err is not None and err.startswith("ARGS_INVALID")
    tool2 = ToolDef(
        name="mcp__evil__y",
        description="",
        parameters={"type": "nonexistent"},
        perm_code="tool:mcp:use",
        write=False,
        require_confirm=False,
    )
    assert validate_args(tool2, {"a": 1}) is not None


async def test_client_json_id_mismatch_rejected() -> None:
    """R1-T2:JSON 形态响应 id 不匹配拒收(与 SSE 形态同判据)。"""
    import httpx

    from kongming.mcp.client import HttpMcpCaller
    from kongming.mcp.ports import McpCallError

    class _WrongIdTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=json.dumps(
                    {"jsonrpc": "2.0", "id": 999, "result": {"content": []}}
                ).encode(),
                headers={"content-type": "application/json"},
            )

    caller = HttpMcpCaller(transport=_WrongIdTransport())
    with pytest.raises(McpCallError, match="id 不匹配"):
        await caller.call(endpoint="http://fake/mcp", headers={}, tool_name="x", args={})


# ── 管理面(HTTP + 覆写 caller) ──


async def _mcp_admin(client: AsyncClient, s: AsyncSession, make_user: MakeUser, name: str):
    """持 system:mcp:manage 的用户(独立租户 mcp)+ 覆写服务(假 caller)。"""
    await seed_all(s, get_settings())
    uid = await make_user(name)
    rid = await _mk_role_with_perms(s, f"role_{name}", ["system:mcp:manage"])
    tid_str = await _mk_tenant_once(s)
    await _bind(s, "sys_user_role", uid, rid)
    await _bind(s, "sys_user_tenant", uid, tid_str)
    await s.commit()
    return uid, await _login(client, name)


def _override_mcp_service() -> None:
    from fastapi import Depends

    from kongming.auth.repository import SqlOperLogRepo
    from kongming.db import get_session
    from kongming.main import app
    from kongming.mcp.repository import SqlMcpRepo
    from kongming.mcp.router import get_mcp_service
    from kongming.mcp.service import McpService

    class _NoopOper:
        def __init__(self, s: AsyncSession) -> None:
            self._r = SqlOperLogRepo(s)

        async def log(self, *, action: str, target: str, detail: dict[str, Any]) -> None:
            await self._r.add(None, action, target, detail)

    def _factory(s: AsyncSession = Depends(get_session)) -> McpService:  # noqa: B008
        from kongming.datasource.cipher import get_cipher

        return McpService(
            SqlMcpRepo(s, get_cipher(get_settings().datasource_encrypt_key)),
            _fake_caller(),
            _NoopOper(s),
        )

    app.dependency_overrides[get_mcp_service] = _factory


def _clear_mcp_override() -> None:
    from kongming.main import app
    from kongming.mcp.router import get_mcp_service

    app.dependency_overrides.pop(get_mcp_service, None)


async def test_admin_crud_masked_and_cipher_at_rest(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    _, token = await _mcp_admin(client, session, make_user, "mcp_admin1")
    _override_mcp_service()
    try:
        r = await client.post(
            "/system/mcp",
            headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": "mcp"},
            json={
                "server_key": "fs",
                "name": "文件服务",
                "endpoint": "http://fake/mcp",
                "headers": {"Authorization": "Bearer secret-token-123"},
            },
        )
        assert r.status_code == 201, r.text
        row = r.json()
        assert row["header_keys"] == ["Authorization"]
        assert "secret-token-123" not in r.text  # 创建响应不回显明文
        # 创建即同步:快照落库(2 个有效工具,1 个冲突跳过)
        assert len(row["tools"]) == 2
        assert "mcp__fs__read_thing" in row["last_sync_error"]  # 冲突告警
        # 密文落库:库内无明文
        cipher_text = (
            await session.execute(
                text("SELECT headers_cipher FROM public.sys_mcp_server WHERE id=CAST(:i AS uuid)"),
                {"i": row["id"]},
            )
        ).scalar_one()
        assert "secret-token-123" not in cipher_text
        assert cipher_text != ""
        # 列表掩码
        r2 = await client.get(
            "/system/mcp",
            headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": "mcp"},
        )
        assert "secret-token-123" not in r2.text
        assert r2.json()[0]["header_keys"] == ["Authorization"]
    finally:
        _clear_mcp_override()


async def test_admin_perm_403_and_cross_tenant_404(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    _, token = await _mcp_admin(client, session, make_user, "mcp_admin2")
    # 无权限用户
    uid2 = await make_user("mcp_noperm")
    await _bind(session, "sys_user_tenant", uid2, await _default_tenant_id(session))
    await session.commit()
    token2 = await _login(client, "mcp_noperm")
    r = await client.get("/system/mcp", headers={"Authorization": f"Bearer {token2}"})
    assert r.status_code == 403
    # 跨租户 404 同形:在 mcp 租户建行,另一租户用户操作
    _override_mcp_service()
    try:
        r2 = await client.post(
            "/system/mcp",
            headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": "mcp"},
            json={"server_key": "fs2", "name": "x", "endpoint": "http://fake/mcp"},
        )
        sid = r2.json()["id"]
    finally:
        _clear_mcp_override()
    # 另一租户用户(持管理权限但绑 default 租户)
    uid3 = await make_user("mcp_other")
    rid3 = await _mk_role_with_perms(session, "role_mcp_other", ["system:mcp:manage"])
    await _bind(session, "sys_user_role", uid3, rid3)
    await _bind(session, "sys_user_tenant", uid3, await _default_tenant_id(session))
    await session.commit()
    token3 = await _login(client, "mcp_other")
    r3 = await client.delete(
        f"/system/mcp/{sid}",
        headers={"Authorization": f"Bearer {token3}"},  # 缺省 default 租户
    )
    assert r3.status_code == 404


async def test_admin_key_fullmatch_bypass(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """008 R1 教训:尾换行绕过——领域层 fullmatch 判 400。"""
    _, token = await _mcp_admin(client, session, make_user, "mcp_admin3")
    r = await client.post(
        "/system/mcp",
        headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": "mcp"},
        json={"server_key": "bad\n", "name": "x", "endpoint": "http://fake/mcp"},
    )
    assert r.status_code == 400


# ── 执行链(覆写 toolcall 组合根,resolver+caller 都走假 server) ──


async def _tool_user(s: AsyncSession, make_user: MakeUser, name: str, codes: list[str]) -> str:
    uid = await make_user(name)
    rid = await _mk_role_with_perms(s, f"role_{name}", codes)
    tid_str = await _mk_tenant_once(s)
    await _bind(s, "sys_user_role", uid, rid)
    await _bind(s, "sys_user_tenant", uid, tid_str)
    await s.commit()
    return uid


def _override_toolcall() -> None:
    """覆写 toolcall _get_service:resolver/caller 走假 server,其余真实件。"""
    from fastapi import Depends

    from kongming.auth.repository import SqlPermRepo
    from kongming.datasource.cipher import get_cipher
    from kongming.db import get_session
    from kongming.main import app
    from kongming.mcp.repository import SqlMcpRepo
    from kongming.mcp.service import McpService
    from kongming.redis_client import get_redis
    from kongming.toolcall.ratelimit import RedisRateLimiter
    from kongming.toolcall.repository import SqlToolCallRepo, ToolAuditWriter
    from kongming.toolcall.router import (
        _ChatReplyStore,
        _get_service,
        _InternalSqlExecutor,
        _McpDynamicResolver,
        _McpToolCaller,
        _RagServiceAdapter,
    )
    from kongming.toolcall.service import ToolCallService

    def _factory(s: AsyncSession = Depends(get_session)) -> ToolCallService:  # noqa: B008
        mcp_svc = McpService(
            SqlMcpRepo(s, get_cipher(get_settings().datasource_encrypt_key)),
            _fake_caller(),
        )
        return ToolCallService(
            repo=SqlToolCallRepo(s),
            writer=ToolAuditWriter(),
            perms=SqlPermRepo(s),
            limiter=RedisRateLimiter(get_redis()),
            sql_executor=_InternalSqlExecutor(s),
            rag_retriever=_RagServiceAdapter(s),
            reply_store=_ChatReplyStore(s),
            intent=None,
            sandbox=None,
            resolver=_McpDynamicResolver(mcp_svc),
            mcp_caller=_McpToolCaller(mcp_svc),
            rate_limit_per_minute=600,
            confirm_ttl_minutes=15,
        )

    app.dependency_overrides[_get_service] = _factory


def _clear_toolcall_override() -> None:
    from kongming.main import app
    from kongming.toolcall.router import _get_service

    app.dependency_overrides.pop(_get_service, None)


async def _register_fake_server(client: AsyncClient, token: str, key: str = "fs") -> str:
    _override_mcp_service()
    try:
        r = await client.post(
            "/system/mcp",
            headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": "mcp"},
            json={"server_key": key, "name": "假服务", "endpoint": "http://fake/mcp"},
        )
        assert r.status_code == 201, r.text
        return str(r.json()["id"])
    finally:
        _clear_mcp_override()


async def test_execute_mcp_readonly_tool(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「授权调用成功并落审计」:只读标注工具直接执行,结果回灌。"""
    _, token = await _mcp_admin(client, session, make_user, "mcp_exec1")
    await _register_fake_server(client, token)
    uid = await _tool_user(session, make_user, "mcp_u1", ["tool:mcp:use"])
    _override_toolcall()
    try:
        r = await client.post(
            "/internal/tool/execute",
            headers=INTERNAL,
            json=_req(
                "mcp__fs__read_thing",
                {"path": "/etc/hosts"},
                user_id=uid,
                tenant_key="mcp",
            ),
        )
    finally:
        _clear_toolcall_override()
    body = r.json()
    assert body["status"] == "executed", body
    # cast 仅定型 Any-or-dict 并集,运行时不变
    assert "读到: /etc/hosts" in cast("dict[str, str]", body["result"] or {}).get("content", "")


async def test_execute_unannotated_goes_confirm(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「未标注工具走确认流」:无 readOnlyHint → PENDING_CONFIRM,
    确认后真执行(动态解析重放不误拒)。"""
    _, token = await _mcp_admin(client, session, make_user, "mcp_exec2")
    await _register_fake_server(client, token)
    uid = await _tool_user(session, make_user, "mcp_u2", ["tool:mcp:use", "chat:execute"])
    sid = await _mk_session(session, uid, "mcp")
    token_u = await _login(client, "mcp_u2")
    _override_toolcall()
    try:
        r = await client.post(
            "/internal/tool/execute",
            headers=INTERNAL,
            json=_req(
                "mcp__fs__write_thing",
                {"path": "/data/x"},
                user_id=uid,
                tenant_key="mcp",
                idempotency_key=f"mcp-{uid[:8]}",
            ),
        )
        body = r.json()
        assert body["status"] == "confirm_required", body
        confirm_id = body["confirm_id"]
        r2 = await client.post(
            "/chat/tool-confirm",
            headers={"Authorization": f"Bearer {token_u}", "X-Tenant-Id": "mcp"},
            json={"confirm_id": confirm_id, "decision": "confirm", "session_id": sid},
        )
    finally:
        _clear_toolcall_override()
    assert r2.status_code == 200, r2.text
    assert r2.json()["status"] == "executed"
    assert "写入: /data/x" in json.dumps(r2.json(), ensure_ascii=False)


async def test_execute_without_perm_denied(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「无权限拒绝」:无 tool:mcp:use 直调按越权拒绝。"""
    _, token = await _mcp_admin(client, session, make_user, "mcp_exec3")
    await _register_fake_server(client, token)
    uid = await _tool_user(session, make_user, "mcp_u3", ["tool:sql:execute"])
    _override_toolcall()
    try:
        r = await client.post(
            "/internal/tool/execute",
            headers=INTERNAL,
            json=_req("mcp__fs__read_thing", {"path": "/a"}, user_id=uid, tenant_key="mcp"),
        )
    finally:
        _clear_toolcall_override()
    body = r.json()
    assert body["status"] == "rejected"
    assert "TOOL_PERMISSION_DENIED" in (body["reason"] or "")


async def test_disabled_server_tool_unregistered(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「禁用后工具消失」:禁用后调用 TOOL_NOT_REGISTERED。"""
    _, token = await _mcp_admin(client, session, make_user, "mcp_exec4")
    sid = await _register_fake_server(client, token)
    _override_mcp_service()
    try:
        r = await client.post(
            f"/system/mcp/{sid}/disable",
            headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": "mcp"},
        )
        assert r.status_code == 200
    finally:
        _clear_mcp_override()
    uid = await _tool_user(session, make_user, "mcp_u4", ["tool:mcp:use"])
    _override_toolcall()
    try:
        r2 = await client.post(
            "/internal/tool/execute",
            headers=INTERNAL,
            json=_req("mcp__fs__read_thing", {"path": "/a"}, user_id=uid, tenant_key="mcp"),
        )
    finally:
        _clear_toolcall_override()
    assert r2.json()["status"] == "rejected"
    assert "TOOL_NOT_REGISTERED" in (r2.json()["reason"] or "")


async def test_tools_catalog_merges_mcp(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「目录合并」:GET /system/tools 含 MCP 工具与 server 溯源。"""
    _, token = await _mcp_admin(client, session, make_user, "mcp_cat1")
    await _register_fake_server(client, token)
    # 目录端点要 system:tool:manage——本用户加授
    uid = (
        await session.execute(text("SELECT id FROM public.sys_user WHERE username='mcp_cat1'"))
    ).scalar_one()
    rid = (
        await session.execute(text("SELECT id FROM public.sys_role WHERE role_key='role_mcp_cat1'"))
    ).scalar_one()
    pid = (
        await session.execute(
            text("SELECT id FROM public.sys_permission WHERE perm_code='system:tool:manage'")
        )
    ).scalar_one()
    await session.execute(
        text(
            "INSERT INTO public.sys_role_permission (role_id, permission_id)"
            " VALUES (:r, :p) ON CONFLICT DO NOTHING"
        ),
        {"r": str(rid), "p": str(pid)},
    )
    await session.commit()
    r = await client.get(
        "/system/tools",
        headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": "mcp"},
    )
    assert r.status_code == 200, r.text
    names = {t["name"]: t for t in r.json()["tools"]}
    assert "mcp__fs__read_thing" in names
    assert names["mcp__fs__read_thing"]["server_key"] == "fs"
    assert names["mcp__fs__read_thing"]["write"] is False
    assert names["mcp__fs__write_thing"]["require_confirm"] is True  # fail-closed
    assert "execute_sql" in names  # 静态集零回归
    assert uid is not None


async def test_disabled_server_manageable(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """R1-S1 回归:禁用 server 仍可编辑(保存同步不回滚)/测试/同步——
    管理面探测与执行面分流(require_enabled=False)。"""
    _, token = await _mcp_admin(client, session, make_user, "mcp_admin4")
    sid = await _register_fake_server(client, token)
    _override_mcp_service()
    try:
        r = await client.post(
            f"/system/mcp/{sid}/disable",
            headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": "mcp"},
        )
        assert r.status_code == 200
        # 禁用态编辑(此前 404 且修改静默丢弃)
        r2 = await client.put(
            f"/system/mcp/{sid}",
            headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": "mcp"},
            json={"name": "改名后", "endpoint": "http://fake/mcp", "headers": {}},
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["name"] == "改名后"  # 修改落库(未被回滚)
        # 禁用态连通性测试
        r3 = await client.post(
            f"/system/mcp/{sid}/test",
            headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": "mcp"},
        )
        assert r3.json()["ok"] is True
        # 禁用态同步
        r4 = await client.post(
            f"/system/mcp/{sid}/sync",
            headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": "mcp"},
        )
        assert r4.json()["ok"] is True
    finally:
        _clear_mcp_override()


async def test_intent_count_includes_mcp_write(session: AsyncSession) -> None:
    """R1-A1:020 意图审核计数并入 mcp__ 前缀(MCP 写工具频控不失效)。"""
    from kongming.toolcall.repository import SqlToolCallRepo

    await seed_all(session, get_settings())
    tid = (await session.execute(text("SELECT id FROM public.sys_tenant LIMIT 1"))).scalar_one()
    uid = (await session.execute(text("SELECT id FROM public.sys_user LIMIT 1"))).scalar_one()
    trace = f"tr-{uuid.uuid4().hex[:12]}"
    for name in ("create_system_announcement", "mcp__fs__write_thing", "mcp__x__y"):
        await session.execute(
            text(
                "INSERT INTO public.sys_tool_audit"
                " (id, trace_id, user_id, tenant_id, tool_name, status)"
                " VALUES (gen_random_uuid(), :t, :u, :t2, :n, 'EXECUTED')"
            ),
            {"t": trace, "u": str(uid), "t2": str(tid), "n": name},
        )
    await session.commit()
    n = await SqlToolCallRepo(session).count_write_calls(trace, ["create_system_announcement"])
    assert n == 3  # 静态 1 + mcp__ 前缀 2(left() 前缀口径)


# ── /mcp 入站 ──


@pytest.fixture
def fake_agent() -> Iterator[dict[str, Any]]:
    """025 同款 agent 替身注入(test_bot 的 fixture 为其模块私有,此处自建)。"""
    from kongming.chat.router import get_agent_client
    from kongming.main import app
    from tests.test_bot import FakeAgent

    holder: dict[str, FakeAgent] = {}
    app.dependency_overrides[get_agent_client] = lambda: holder["agent"]
    yield holder
    app.dependency_overrides.pop(get_agent_client, None)


async def _bot_with_key(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, Any],
) -> str:
    from tests.test_bot import BOT_PAYLOAD, FakeAgent, _user

    fake_agent["agent"] = FakeAgent()
    _, token = await _user(client, session, make_user, "mcp_botowner")
    r = await client.post(
        "/system/bots",
        headers={"Authorization": f"Bearer {token}"},
        json=BOT_PAYLOAD,
    )
    bot_id = r.json()["id"]
    r2 = await client.post(
        f"/system/bots/{bot_id}/keys?name=mcp", headers={"Authorization": f"Bearer {token}"}
    )
    assert r2.status_code == 201
    return str(r2.json()["api_key"])


async def test_mcp_inbound_full_chain(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, Any],
) -> None:
    """spec「完整调用链」:initialize → tools/list(恰 1 个绑定 Bot)→
    tools/call(channel=mcp 审计)。"""
    plain = await _bot_with_key(client, session, make_user, fake_agent)
    r1 = await client.post(
        "/mcp",
        headers={"X-Api-Key": plain},
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
    )
    assert r1.json()["result"]["serverInfo"]["name"] == "kongming"
    r2 = await client.post(
        "/mcp",
        headers={"X-Api-Key": plain},
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    )
    tools = r2.json()["result"]["tools"]
    assert [t["name"] for t in tools] == ["bot__after_sale"]
    r3 = await client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {plain}"},  # Bearer 形态同认
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "bot__after_sale", "arguments": {"question": "你好"}},
        },
    )
    content = r3.json()["result"]["content"]
    assert content[0]["text"] == "Bot 应答"
    # 渠道溯源直证:Bot 运行经 025 链路收到 channel="mcp"
    assert fake_agent["agent"].calls[0]["channel"] == "mcp"


async def test_mcp_inbound_invalid_key_and_unknown_method(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, Any],
) -> None:
    plain = await _bot_with_key(client, session, make_user, fake_agent)
    r = await client.post(
        "/mcp",
        headers={"X-Api-Key": "km_forged"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    assert r.status_code == 401
    r2 = await client.post(
        "/mcp",
        headers={"X-Api-Key": plain},
        json={"jsonrpc": "2.0", "id": 9, "method": "resources/list"},
    )
    assert r2.json()["error"]["code"] == -32601
    # notifications 202 无 body
    r3 = await client.post(
        "/mcp",
        headers={"X-Api-Key": plain},
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
    )
    assert r3.status_code == 202
    # 其他 key 的 Bot 名调用 → 未知工具(跨 key 不可调用)
    r4 = await client.post(
        "/mcp",
        headers={"X-Api-Key": plain},
        json={
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "bot__other", "arguments": {"question": "x"}},
        },
    )
    assert r4.json()["error"]["code"] == -32602


async def test_mcp_inbound_question_cap_and_error_surface(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, Any],
) -> None:
    """R1-A3/A7:question 超 2000 拒(-32602);Bot 失败轮透 error+isError。"""
    plain = await _bot_with_key(client, session, make_user, fake_agent)
    r = await client.post(
        "/mcp",
        headers={"X-Api-Key": plain},
        json={
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "bot__after_sale", "arguments": {"question": "长" * 2001}},
        },
    )
    assert r.json()["error"]["code"] == -32602
    assert "2000" in r.json()["error"]["message"]
    # A7:Bot 运行失败(agent 替身返 success=False)透 error 文本
    from tests.test_bot import AgentReply as _BotReply

    class _FailAgent:
        async def query_react(self, **kw: object) -> _BotReply:
            return _BotReply(
                success=False, verdict="ERROR", summary="", error="助手暂不可用,请稍后重试"
            )

        async def rewrite_title(self, *, question: str) -> None:
            return None

    fake_agent["agent"] = _FailAgent()
    r2 = await client.post(
        "/mcp",
        headers={"X-Api-Key": plain},
        json={
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {"name": "bot__after_sale", "arguments": {"question": "在吗"}},
        },
    )
    result = r2.json()["result"]
    assert result["isError"] is True
    assert "暂不可用" in result["content"][0]["text"]


def test_has_redos_pattern_detection() -> None:
    """审查循环 F1:回溯型 pattern(jsonschema 校验可卡死 worker)在 sync 期
    即被识别降级;合法 pattern 不误伤。"""
    from kongming.mcp.models import has_redos_pattern

    def _schema(pattern: str) -> dict[str, Any]:
        return {"type": "object", "properties": {"x": {"type": "string", "pattern": pattern}}}

    assert has_redos_pattern(_schema("(a+)+b"))
    assert has_redos_pattern(_schema(r"(\w+)*y"))
    assert has_redos_pattern({"properties": {"x": {"pattern": "a" * 513}}})  # 超长
    assert not has_redos_pattern(_schema("^[a-z]+$"))
    assert not has_redos_pattern(_schema("(a|b)+"))
    assert not has_redos_pattern({"type": "object"})
