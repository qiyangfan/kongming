# pyright: reportPrivateUsage=false
"""081 external-knowledge 测试:部署级注册条件 + 控制面出站客户端 + 工具链路。

安全规则类(注册闸/白名单/多租户告警/授权过滤)测试先行(宪法 Article III);
出站客户端用真实 TCP 存根(连接计数可断言),不用 mock transport——
被测对象自建 AsyncClient,mock 传输层反而绕过了真实失败路径。

集成段(真实 PG+Redis,conftest 基建):经 /internal/tool/execute 打全链,
断言审计与「零 LLM 用量」;注册表激活用还原 fixture 防模块全局污染。
"""

import json
import logging
import socket
import threading
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.config import get_settings

LOCAL_URL = "http://127.0.0.1:9"  # 端口哨兵(白名单判定用,不真连)


# ── 纯函数段:注册闸(无基础设施) ──


def test_is_local_base_url_whitelist() -> None:
    from kongming.toolcall.registry import is_local_base_url

    allowed = [
        "http://127.0.0.1:8787",
        "http://localhost:51194",
        "http://LOCALHOST:9000",  # 主机名大小写不敏感
        "https://127.0.0.1",
        "http://host.docker.internal:51194",  # compose 容器→宿主机等价形态
        "http://[::1]:8787",
    ]
    for url in allowed:
        assert is_local_base_url(url), url
    denied = [
        "",
        "   ",
        "http://knowledge.example.com:9000",
        "https://10.0.0.8/api",
        "file:///etc/passwd",
        "not a url",
        "http://localhost.evil.com",  # 后缀伪装非 localhost
    ]
    for url in denied:
        assert not is_local_base_url(url), url


def test_build_registry_gates() -> None:
    from kongming.toolcall.registry import REGISTRY, build_registry

    base = build_registry(None)
    assert "knowledge_search" not in base
    assert set(base) == set(REGISTRY)  # 缺省=静态基表,零回归

    gated = build_registry(LOCAL_URL)
    assert "knowledge_search" in gated
    assert gated["knowledge_search"].perm_code == "tool:knowledge:search"
    assert gated["knowledge_search"].write is False

    assert "knowledge_search" not in build_registry("http://knowledge.example.com")  # 白名单外


@pytest.fixture
def restore_registry() -> Iterator[Any]:
    """激活态还原:模块全局 _active_registry 防跨用例污染。"""
    from kongming.toolcall import registry

    saved = registry.effective()
    try:
        yield registry
    finally:
        registry.activate(saved)


def test_activate_effective_and_filter(restore_registry: Any) -> None:
    registry = restore_registry
    registry.activate(registry.build_registry(LOCAL_URL))
    assert registry.get_tool("knowledge_search") is not None
    assert registry.get_tool("nope") is None
    specs = registry.filter_specs({"tool:knowledge:search"})
    assert [s["name"] for s in specs] == ["knowledge_search"]
    # 未授权权限点看不到(与既有工具同范式)
    assert registry.filter_specs({"tool:sql:validate"})[0]["name"] != "knowledge_search"
    assert "knowledge_search" not in registry.filter_specs(set())


# ── 出站客户端段:真实 TCP 存根(连接计数可断言) ──


class _Stub:
    """一次性 TCP 存根:每个连接交给 handler(conn, 第几次),记录连接次数。

    刻意不用 ASGI/uvicorn:被测客户端自建 AsyncClient,需要的是真实 TCP
    失败面(拒连/accept 后关连/响应形态),裸 socket 存根最贴近。
    """

    def __init__(self, handler: Callable[[socket.socket, int], None]) -> None:
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(8)
        self.port = self._srv.getsockname()[1]
        self._handler = handler
        self._n = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    @property
    def connections(self) -> int:
        with self._lock:
            return self._n

    def _loop(self) -> None:
        self._srv.settimeout(0.2)
        while not self._stop.is_set():
            try:
                conn, _ = self._srv.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            with self._lock:
                self._n += 1
                nth = self._n
            try:
                conn.settimeout(5)
                self._handler(conn, nth)
            except OSError:
                pass
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)
        self._srv.close()


def _http_response(
    body: str, *, status: int = 200, content_type: str = "application/json"
) -> bytes:
    return (
        f"HTTP/1.1 {status} OK\r\nContent-Type: {content_type}\r\n"
        f"Content-Length: {len(body.encode())}\r\nConnection: close\r\n\r\n{body}"
    ).encode()


def _hits_body() -> str:
    return json.dumps(
        {
            "query": "口径",
            "top_k": 2,
            "results": [
                {
                    "chunk_id": "c1",
                    "source_name": "refund-policy.md",
                    "note_path": "docs/refund-policy.md",
                    "source_hash": "h1",
                    "anchor": "退款时效",
                    "text": "7 天无理由,运费险另计",
                    "bm25_score": 1.2,
                    "vector_score": 0.9,
                    "hybrid_score": 0.8,
                    "rerank_score": 0.7,
                }
            ],
        },
        ensure_ascii=False,
    )


async def _search(stub: _Stub, *, timeout_ms: int = 3000) -> list[dict[str, Any]]:
    from kongming.toolcall.router import _SikuKnowledgeClient

    client = _SikuKnowledgeClient(base_url=f"http://127.0.0.1:{stub.port}", timeout_ms=timeout_ms)
    return await client.search(query="口径", top_k=2)


async def test_client_hits_passthrough() -> None:
    def ok(conn: socket.socket, nth: int) -> None:
        conn.recv(65536)
        conn.sendall(_http_response(_hits_body()))

    stub = _Stub(ok)
    try:
        hits = await _search(stub)
        assert len(hits) == 1
        assert hits[0]["note_path"] == "docs/refund-policy.md"
        assert hits[0]["anchor"] == "退款时效"
        assert stub.connections == 1
    finally:
        stub.close()


async def test_client_connect_layer_refusal_retries_once() -> None:
    """第一次 accept 后直接关连 → 连接层未送达,重试恰好一次后成功。"""

    def refuse_then_ok(conn: socket.socket, nth: int) -> None:
        if nth == 1:
            conn.recv(65536)
            return  # 关连不发响应(RemoteProtocolClosed)
        conn.recv(65536)
        conn.sendall(_http_response(_hits_body()))

    stub = _Stub(refuse_then_ok)
    try:
        hits = await _search(stub)
        assert len(hits) == 1
        assert stub.connections == 2  # 恰好一次重试,不多不少
    finally:
        stub.close()


async def test_client_connection_refused_raises_unreachable() -> None:
    """无监听端口:两次尝试均拒连 → unreachable 分类上浮。"""
    from kongming.toolcall.models import KnowledgeSourceError

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    dead_port = probe.getsockname()[1]
    probe.close()

    from kongming.toolcall.router import _SikuKnowledgeClient

    client = _SikuKnowledgeClient(base_url=f"http://127.0.0.1:{dead_port}", timeout_ms=2000)
    with pytest.raises(KnowledgeSourceError, match=r"^unreachable:"):
        await client.search(query="x", top_k=1)


async def test_client_http_500_no_retry_protocol() -> None:
    def boom(conn: socket.socket, nth: int) -> None:
        conn.recv(65536)
        conn.sendall(_http_response('{"error":"boom"}', status=500))

    stub = _Stub(boom)
    try:
        with pytest.raises(Exception, match=r"^protocol: 上游 HTTP 500") as ei:
            await _search(stub)
        assert "protocol:" in str(ei.value)
        assert stub.connections == 1  # 响应类错误不重试
    finally:
        stub.close()


async def test_client_malformed_json_protocol() -> None:
    def garbage(conn: socket.socket, nth: int) -> None:
        conn.recv(65536)
        conn.sendall(_http_response("<html>not json</html>"))

    stub = _Stub(garbage)
    try:
        with pytest.raises(Exception, match=r"^protocol:"):
            await _search(stub)
        assert stub.connections == 1
    finally:
        stub.close()


async def test_client_missing_results_field_protocol() -> None:
    def no_results(conn: socket.socket, nth: int) -> None:
        conn.recv(65536)
        conn.sendall(_http_response(json.dumps({"unexpected": True})))

    stub = _Stub(no_results)
    try:
        with pytest.raises(Exception, match=r"^protocol: 检索响应缺 results"):
            await _search(stub)
    finally:
        stub.close()


async def test_client_read_timeout_protocol() -> None:
    def slow(conn: socket.socket, nth: int) -> None:
        conn.recv(65536)
        import time

        time.sleep(0.6)  # 超过 200ms 超时
        conn.sendall(_http_response(_hits_body()))

    stub = _Stub(slow)
    try:
        with pytest.raises(Exception, match=r"^protocol: 检索读超时"):
            await _search(stub, timeout_ms=200)
        assert stub.connections == 1  # 读超时不重试(响应类)
    finally:
        stub.close()


# ── 集成段:真实 PG+Redis,经 /internal/tool/execute 全链 ──

INTERNAL = {"X-Internal-Token": "test-internal-token"}


def _req(tool: str, args: dict[str, object], **kw: object) -> dict[str, object]:
    base: dict[str, object] = {
        "trace_id": f"tr-{uuid.uuid4().hex[:12]}",
        "tenant_key": "ek",
        "tool_name": tool,
        "args": args,
    }
    base.update(kw)
    return base


async def _mk_tenant(s: AsyncSession, key: str) -> str:
    tid = (
        await s.execute(
            text(
                "INSERT INTO public.sys_tenant (id, tenant_key, tenant_name, status)"
                " VALUES (gen_random_uuid(), :k, :k, 'ACTIVE') RETURNING id"
            ),
            {"k": key},
        )
    ).scalar_one()
    await s.commit()
    return str(tid)


async def _mk_role_with_perms(s: AsyncSession, key: str, codes: list[str]) -> str:
    rid = (
        await s.execute(
            text(
                "INSERT INTO public.sys_role (id, role_key, role_name, data_scope)"
                " VALUES (gen_random_uuid(), :k, :k, 'SELF') RETURNING id"
            ),
            {"k": key},
        )
    ).scalar_one()
    for code in codes:
        await s.execute(
            text(
                "INSERT INTO public.sys_permission (id, perm_code, perm_name)"
                " VALUES (gen_random_uuid(), :c, :c) ON CONFLICT DO NOTHING"
            ),
            {"c": code},
        )
        await s.execute(
            text(
                "INSERT INTO public.sys_role_permission (role_id, permission_id)"
                " SELECT :r, p.id FROM public.sys_permission p WHERE p.perm_code = :c"
                " ON CONFLICT DO NOTHING"
            ),
            {"r": rid, "c": code},
        )
    await s.commit()
    return str(rid)


async def _bind(s: AsyncSession, table: str, user_id: str, target_id: str) -> None:
    col = "role_id" if table == "sys_user_role" else "tenant_id"
    await s.execute(
        text(
            f"INSERT INTO public.{table} (user_id, {col}) VALUES (:u, :t)"  # noqa: S608  # 测试常量
        ),
        {"u": user_id, "t": target_id},
    )
    await s.commit()


async def _user_with_tools(
    s: AsyncSession, make_user: Callable[..., Any], username: str, codes: list[str]
) -> str:
    uid = await make_user(username)
    rid = await _mk_role_with_perms(s, f"role_{username}", codes)
    tid = (
        await s.execute(text("SELECT id FROM public.sys_tenant WHERE tenant_key=:k"), {"k": "ek"})
    ).scalar_one()
    await _bind(s, "sys_user_role", uid, rid)
    await _bind(s, "sys_user_tenant", uid, str(tid))
    return uid


@pytest.fixture(autouse=True)
def _reset_main_logger() -> Iterator[None]:  # pyright: ignore[reportUnusedFunction]
    """会话级 alembic upgrade 的 fileConfig(disable_existing_loggers)会把
    收集期已创建的模块 logger 置 disabled(与 test_sqlfw_engine 同因);本模块
    断言 main 的启动告警,先复启,退出还原。"""
    lg = logging.getLogger("kongming.main")
    orig = lg.disabled
    lg.disabled = False
    yield
    lg.disabled = orig


@pytest.fixture(autouse=True)
async def _tenant(session: AsyncSession) -> None:  # pyright: ignore[reportUnusedFunction]
    await _mk_tenant(session, "ek")


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    from kongming.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


class _FakeKnowledgePort:
    """KnowledgeSearchPort 假件:命中剧本化 / 失败分类可注入。"""

    def __init__(self, *, hits: list[dict[str, object]] | None = None, error: str | None = None):
        self._hits = hits if hits is not None else []
        self._error = error
        self.calls: list[dict[str, object]] = []

    async def search(self, *, query: str, top_k: int) -> list[dict[str, object]]:
        self.calls.append({"query": query, "top_k": top_k})
        if self._error is not None:
            from kongming.toolcall.models import KnowledgeSourceError

            raise KnowledgeSourceError(self._error)
        return self._hits


@pytest.fixture
def registered(
    monkeypatch: pytest.MonkeyPatch, restore_registry: Any
) -> Iterator[_FakeKnowledgePort]:
    """激活注册表注入 knowledge_search + 组合根假端口(HTTP 层全链可打)。"""
    fake = _FakeKnowledgePort(
        hits=[
            {
                "chunk_id": "c1",
                "source_name": "refund-policy.md",
                "note_path": "docs/refund-policy.md",
                "anchor": "退款时效",
                "text": "7 天无理由",
            }
        ]
    )
    restore_registry.activate(restore_registry.build_registry(LOCAL_URL))
    import kongming.toolcall.router as router_mod

    monkeypatch.setattr(router_mod, "_build_knowledge_client", lambda: fake)
    yield fake


async def test_unconfigured_tool_not_registered(
    client: AsyncClient, session: AsyncSession, make_user: Callable[..., Any]
) -> None:
    """未配置端点(默认):工具对全链不存在——执行被拒且落审计。"""
    uid = await _user_with_tools(session, make_user, "ek_absent", ["tool:knowledge:search"])
    req = _req("knowledge_search", {"query": "口径"}, user_id=uid)
    r = await client.post("/internal/tool/execute", headers=INTERNAL, json=req)
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"
    assert "TOOL_NOT_REGISTERED" in (r.json()["reason"] or "")
    audits = (
        (
            await session.execute(
                text("SELECT status FROM public.sys_tool_audit WHERE trace_id=:t"),
                {"t": req["trace_id"]},
            )
        )
        .mappings()
        .all()
    )
    assert [a["status"] for a in audits] == ["REJECTED"]


async def test_registered_executes_with_observation_and_audit(
    client: AsyncClient,
    session: AsyncSession,
    make_user: Callable[..., Any],
    registered: _FakeKnowledgePort,
) -> None:
    uid = await _user_with_tools(session, make_user, "ek_ok", ["tool:knowledge:search"])
    req = _req("knowledge_search", {"query": "退款时效口径", "top_k": 3}, user_id=uid)
    r = await client.post("/internal/tool/execute", headers=INTERNAL, json=req)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "executed"
    result = body["result"]
    assert result["count"] == 1
    hit = result["hits"][0]
    assert hit["note_path"] == "docs/refund-policy.md"
    assert hit["anchor"] == "退款时效"
    assert hit["text"] == "7 天无理由"
    # 端口入参透传(top_k 缺省另有单测覆盖)
    assert registered.calls == [{"query": "退款时效口径", "top_k": 3}]
    # 审计落行;检索不产 LLM 用量(零 sys_llm_usage 新行)
    audits = (
        (
            await session.execute(
                text("SELECT status, tool_name FROM public.sys_tool_audit WHERE trace_id=:t"),
                {"t": req["trace_id"]},
            )
        )
        .mappings()
        .all()
    )
    assert [(a["status"], a["tool_name"]) for a in audits] == [("EXECUTED", "knowledge_search")]
    usage = (await session.execute(text("SELECT COUNT(*) FROM public.sys_llm_usage"))).scalar_one()
    assert usage == 0


async def test_registered_default_top_k(
    client: AsyncClient,
    session: AsyncSession,
    make_user: Callable[..., Any],
    registered: _FakeKnowledgePort,
) -> None:
    uid = await _user_with_tools(session, make_user, "ek_default", ["tool:knowledge:search"])
    req = _req("knowledge_search", {"query": "x"}, user_id=uid)
    r = await client.post("/internal/tool/execute", headers=INTERNAL, json=req)
    assert r.json()["status"] == "executed"
    assert registered.calls == [{"query": "x", "top_k": 5}]  # 缺省 5(注册表 schema 标注)


async def test_knowledge_unreachable_degrades_to_error_observation(
    client: AsyncClient,
    session: AsyncSession,
    make_user: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
    restore_registry: Any,
) -> None:
    fake = _FakeKnowledgePort(error="unreachable: 知识库拒连(ConnectError)")
    restore_registry.activate(restore_registry.build_registry(LOCAL_URL))
    import kongming.toolcall.router as router_mod

    monkeypatch.setattr(router_mod, "_build_knowledge_client", lambda: fake)
    uid = await _user_with_tools(session, make_user, "ek_down", ["tool:knowledge:search"])
    req = _req("knowledge_search", {"query": "x"}, user_id=uid)
    r = await client.post("/internal/tool/execute", headers=INTERNAL, json=req)
    assert r.status_code == 200  # 不 500:降级为错误观察
    body = r.json()
    assert body["status"] == "error"
    assert (body["reason"] or "").startswith("unreachable:")
    audits = (
        (
            await session.execute(
                text("SELECT status, error_reason FROM public.sys_tool_audit WHERE trace_id=:t"),
                {"t": req["trace_id"]},
            )
        )
        .mappings()
        .all()
    )
    assert [a["status"] for a in audits] == ["ERROR"]
    assert audits[0]["error_reason"].startswith("unreachable:")


async def test_perm_denied_even_when_registered(
    client: AsyncClient,
    session: AsyncSession,
    make_user: Callable[..., Any],
    registered: _FakeKnowledgePort,
) -> None:
    uid = await _user_with_tools(session, make_user, "ek_noperm", ["tool:sql:validate"])
    req = _req("knowledge_search", {"query": "x"}, user_id=uid)
    r = await client.post("/internal/tool/execute", headers=INTERNAL, json=req)
    assert r.json()["status"] == "rejected"
    assert "TOOL_PERMISSION_DENIED" in (r.json()["reason"] or "")
    assert registered.calls == []  # L2 权限层先于执行,端口零调用


async def test_args_schema_invalid(
    client: AsyncClient,
    session: AsyncSession,
    make_user: Callable[..., Any],
    registered: _FakeKnowledgePort,
) -> None:
    uid = await _user_with_tools(session, make_user, "ek_args", ["tool:knowledge:search"])
    req = _req("knowledge_search", {"query": "x", "top_k": 99}, user_id=uid)  # 越上界 10
    r = await client.post("/internal/tool/execute", headers=INTERNAL, json=req)
    assert r.json()["status"] == "rejected"
    assert "ARGS_INVALID" in (r.json()["reason"] or "")
    assert registered.calls == []


# ── 启动装配:注册条件与多租户告警(main._knowledge_endpoint,真实租户计数) ──


@pytest.fixture
def siku_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[str], None]]:
    """KM_SIKU_BASE_URL 环境注入 + settings 缓存刷新(用完还原)。"""

    def _set(url: str) -> None:
        monkeypatch.setenv("KM_SIKU_BASE_URL", url)
        get_settings.cache_clear()

    yield _set
    get_settings.cache_clear()


async def test_endpoint_gate_unconfigured(session: AsyncSession, siku_env: Any) -> None:
    from kongming.main import _knowledge_endpoint

    siku_env("")
    assert await _knowledge_endpoint(session) is None  # 零回归缺省


async def test_endpoint_gate_non_local_host(session: AsyncSession, siku_env: Any) -> None:
    from kongming.main import _knowledge_endpoint

    siku_env("http://knowledge.example.com:9000")
    assert await _knowledge_endpoint(session) is None  # 白名单外 fail-closed


async def test_endpoint_gate_single_tenant_ok(session: AsyncSession, siku_env: Any) -> None:
    from kongming.main import _knowledge_endpoint

    siku_env("http://127.0.0.1:51194")  # 本文件 fixture 只建 1 个租户
    assert await _knowledge_endpoint(session) == "http://127.0.0.1:51194"


async def test_endpoint_gate_multitenant_warns_but_registers(
    session: AsyncSession, siku_env: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """082:单租户闸降级告警——多租户不再拒绝注册,共享检索面显性化。"""
    from kongming.main import _knowledge_endpoint

    await _mk_tenant(session, "ek_second")
    siku_env("http://127.0.0.1:51194")
    with caplog.at_level(logging.WARNING, logger="kongming.main"):
        endpoint = await _knowledge_endpoint(session)
    assert endpoint == "http://127.0.0.1:51194"  # 注册发生(闸已降级)
    assert any("租户数=2" in r.getMessage() and "共享" in r.getMessage() for r in caplog.records), (
        f"应有多租户共享面告警,实际: {[r.getMessage() for r in caplog.records]}"
    )
