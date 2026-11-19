# pyright: reportPrivateUsage=false
"""覆盖工具清单、出口白名单、脱敏、重试和停用。"""

from typing import Any, cast

import httpx
import pytest

from kongming.toolgateway.gateway import ToolGateway
from kongming.toolgateway.models import (
    DOMAIN_TOOLS,
    RetryClass,
    load_manifest,
)

# ── 域清单装载(纯函数)──


def _raw_manifest(**over: Any) -> list[dict[str, Any]]:
    domain: dict[str, Any] = {
        "id": "knowledge",
        "endpoint": "http://127.0.0.1:9901/api/search",
        "whitelist_hosts": ["127.0.0.1", "localhost"],
        "timeout_ms": 5000,
        "quota_per_minute": 60,
        "redaction": "allowlist:knowledge",
    }
    domain.update(over)
    return [
        domain,
        dict(domain, id="web", endpoint="http://10.0.0.1/x", whitelist_hosts=["10.0.0.1"]),
    ]


def test_manifest_malformed_single_domain_dropped() -> None:
    """畸形单域剔除并告警,其余域正常注册,不裸崩。"""

    raw = _raw_manifest()
    raw.append({"id": "broken", "endpoint": "http://x.example"})  # 缺白名单
    domains, warnings = load_manifest(raw)
    assert set(domains) == {"knowledge", "web"}
    assert any("broken" in w for w in warnings)
    # 标识冲突同判:重名域剔除后者
    dup = _raw_manifest() + [dict(_raw_manifest()[0], endpoint="http://127.0.0.1:2/s")]
    domains2, warn2 = load_manifest(dup)
    assert set(domains2) == {"knowledge", "web"}
    assert any("knowledge" in w for w in warn2)


def test_domain_without_policy_not_registered() -> None:
    """清单声明域但无策略器实现 → 不注册(零感知),落告警。"""
    raw = _raw_manifest()
    gateway = ToolGateway(manifest=load_manifest(raw)[0], policies={"web": _OkPolicy()})
    assert gateway.handles("web")
    assert not gateway.handles("knowledge")  # 无策略器 → 不注册
    assert gateway.warnings


def test_domain_tools_mapping_single_source() -> None:
    """工具名→域映射单源(审计域标签与配额闸共用;收录 websearch 域)。"""
    assert DOMAIN_TOOLS == {
        "web_search": "websearch",
    }


# ── 策略器桩 ──


class _OkPolicy:
    """白名单可配、不脱敏的测试策略器(分类:连接族/unreachable 前缀=连接类)。"""

    def __init__(self, whitelist_hosts: tuple[str, ...] = ("127.0.0.1", "localhost")) -> None:
        self._hosts = frozenset(whitelist_hosts)

    def exit_host_allowed(self, host: str) -> bool:
        return host.lower() in self._hosts

    def validate_args(self, args: dict[str, object]) -> str | None:
        return None

    def redact(self, response: object) -> object:
        return response

    def classify(self, exc: Exception | None, *, delivered: bool) -> RetryClass:
        if exc is None:
            return RetryClass.OK
        family = (
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.RemoteProtocolError,
            httpx.ReadError,
            httpx.WriteError,
            httpx.CloseError,
        )
        if isinstance(exc, family):
            return RetryClass.UNDELIVERED
        # 与真实 KnowledgePolicy 同径:执行器收敛的 unreachable: 前缀=连接类
        if str(exc).startswith("unreachable:"):
            return RetryClass.UNDELIVERED
        return RetryClass.PROTOCOL


class _RedactPolicy(_OkPolicy):
    def redact(self, response: object) -> object:
        from typing import cast

        body = cast("dict[str, object] | None", response if isinstance(response, dict) else None)
        raw = body.get("results") if body is not None else None
        if not isinstance(raw, list):
            return {"results": []}
        out: list[dict[str, object]] = []
        for h in cast("list[object]", raw):
            if isinstance(h, dict):
                hd = cast("dict[str, object]", h)
                out.append({k: hd.get(k) for k in ("source_name", "text")})
        return {"results": out}


class _MockExecutor:
    """脚本化执行器:记录调用,按队列返回/抛错。"""

    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.calls: list[dict[str, object]] = []

    async def __call__(self, *, url: str, payload: dict[str, object], timeout_s: float) -> object:
        self.calls.append({"url": url, "payload": payload, "timeout_s": timeout_s})
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _gateway(executor: Any, **kw: Any) -> ToolGateway:
    manifest = load_manifest(_raw_manifest(**kw))[0]
    return ToolGateway(manifest=manifest, policies={"knowledge": _OkPolicy()}, executor=executor)


# ── 出口白名单(零网络请求)──


@pytest.mark.asyncio
async def test_exit_whitelist_rejects_without_network() -> None:
    """端点主机不在白名单 → 策略器拒绝,执行器零调用(不发出网络请求)。"""
    executor = _MockExecutor([{"results": []}])
    gw = ToolGateway(
        manifest=load_manifest(_raw_manifest(whitelist_hosts=["localhost"]))[0],
        policies={"knowledge": _OkPolicy(whitelist_hosts=("localhost",))},
        executor=executor,
    )
    out = await gw.execute(
        tool_name="knowledge",
        args={"query": "x"},
        domain="knowledge",
        trace_id="t",
        tenant_id="tenant",
        user_id="u",
    )
    assert out["status"] == "rejected"
    assert "policy" in str(out.get("reason"))
    assert executor.calls == []  # 零网络请求


@pytest.mark.asyncio
async def test_policy_whitelist_from_manifest_spec() -> None:
    """白名单单源:策略器白名单必须来自清单 spec(host.docker.internal 形态)。"""
    from kongming.toolgateway.websearch import WebSearchPolicy

    policy = WebSearchPolicy(whitelist_hosts=("api.tavily.com", "host.docker.internal"))
    assert policy.exit_host_allowed("host.docker.internal")
    assert not policy.exit_host_allowed("evil.example.com")


# ── 脱敏先于观察 ──


@pytest.mark.asyncio
async def test_redact_before_observation() -> None:
    """响应脱敏先于观察:敏感字段(score/internal_id)不进工具观察。"""
    executor = _MockExecutor(
        [{"results": [{"source_name": "n", "text": "t", "score": 0.9, "internal_id": "x"}]}]
    )
    gw = ToolGateway(
        manifest=load_manifest(_raw_manifest())[0],
        policies={"knowledge": _RedactPolicy()},
        executor=executor,
    )
    out = await gw.execute(
        tool_name="knowledge",
        args={"query": "x"},
        domain="knowledge",
        trace_id="t",
        tenant_id="tenant",
        user_id="u",
    )
    assert out["status"] == "executed"
    result = cast("dict[str, object]", out["result"])
    hits = cast("list[dict[str, object]]", result["results"])
    assert set(hits[0]) == {"source_name", "text"}


# ── 重试分类 ──


@pytest.mark.asyncio
async def test_undelivered_retried_once() -> None:
    """连接类(未送达)失败:至多重试一次,第二次成功即成功。"""
    executor = _MockExecutor(
        [httpx.ConnectError("refused"), {"results": [{"source_name": "n", "text": "t"}]}]
    )
    gw = _gateway(executor)
    out = await gw.execute(
        tool_name="knowledge",
        args={"query": "x"},
        domain="knowledge",
        trace_id="t",
        tenant_id="tenant",
        user_id="u",
    )
    assert out["status"] == "executed"
    assert len(executor.calls) == 2


@pytest.mark.asyncio
async def test_protocol_error_no_retry() -> None:
    """响应类(已送达 5xx)不重试:单次调用即失败。"""
    executor = _MockExecutor([RuntimeError("protocol: 上游 HTTP 500")])
    gw = _gateway(executor)
    out = await gw.execute(
        tool_name="knowledge",
        args={"query": "x"},
        domain="knowledge",
        trace_id="t",
        tenant_id="tenant",
        user_id="u",
    )
    assert out["status"] == "error"
    assert "protocol" in str(out.get("reason"))
    assert len(executor.calls) == 1  # 不重试


@pytest.mark.asyncio
async def test_connect_timeout_is_connection_class() -> None:
    """连接超时=未送达,归连接类重试一次;读超时=已送达,归响应类不重试。"""
    executor = _MockExecutor([httpx.ConnectTimeout("connect"), {"results": []}])
    gw = _gateway(executor)
    out = await gw.execute(
        tool_name="knowledge",
        args={"query": "x"},
        domain="knowledge",
        trace_id="t",
        tenant_id="tenant",
        user_id="u",
    )
    assert out["status"] == "executed"
    assert len(executor.calls) == 2

    executor2 = _MockExecutor([httpx.TimeoutException("read")])
    gw2 = _gateway(executor2)
    out2 = await gw2.execute(
        tool_name="knowledge",
        args={"query": "x"},
        domain="knowledge",
        trace_id="t",
        tenant_id="tenant",
        user_id="u",
    )
    assert out2["status"] == "error"
    assert len(executor2.calls) == 1


@pytest.mark.asyncio
async def test_connect_timeout_real_executor_unreachable_retry_once() -> None:
    """用 MockTransport 固定 ConnectTimeout,避免异常类型受网络环境影响。

    验证连接错误只重试一次,并由真实 WebSearchPolicy 生成 unreachable 文案。
    """
    import httpx as _httpx

    from kongming.toolgateway.websearch import WebSearchExecutor, WebSearchPolicy

    def _handler(request: _httpx.Request) -> _httpx.Response:
        raise _httpx.ConnectTimeout("connect", request=request)

    calls: list[int] = []

    class _Counting(WebSearchExecutor):
        async def __call__(
            self, *, url: str, payload: dict[str, object], timeout_s: float
        ) -> object:
            calls.append(1)
            return await super().__call__(url=url, payload=payload, timeout_s=timeout_s)

    gw = ToolGateway(
        manifest=load_manifest(_raw_manifest())[0],
        policies={"knowledge": WebSearchPolicy(whitelist_hosts=("127.0.0.1", "localhost"))},
        executor=_Counting(adapter="tavily", api_key="k", transport=_httpx.MockTransport(_handler)),
    )
    out = await gw.execute(
        tool_name="web_search",
        args={"query": "x"},
        domain="knowledge",
        trace_id="t",
        tenant_id="t",
        user_id="u",
    )
    assert out["status"] == "error"
    assert str(out["reason"]) == "unreachable: 联网检索拒连(ConnectTimeout)"
    assert len(calls) == 2  # 连接类恰好重试一次


@pytest.mark.asyncio
async def test_decoding_error_real_executor_protocol_no_retry() -> None:
    """真实执行器 + MockTransport 坏 gzip:解码失败(DecodingError∈RequestError,
    非 TransportError/ValueError)按 protocol 前缀收口,不裸抛、不重试。"""
    import httpx as _httpx

    from kongming.toolgateway.websearch import WebSearchExecutor, WebSearchPolicy

    def _handler(request: _httpx.Request) -> _httpx.Response:
        return _httpx.Response(200, headers={"Content-Encoding": "gzip"}, content=b"not-gzip")

    calls: list[int] = []

    class _Counting(WebSearchExecutor):
        async def __call__(
            self, *, url: str, payload: dict[str, object], timeout_s: float
        ) -> object:
            calls.append(1)
            return await super().__call__(url=url, payload=payload, timeout_s=timeout_s)

    gw = ToolGateway(
        manifest=load_manifest(_raw_manifest())[0],
        policies={"knowledge": WebSearchPolicy(whitelist_hosts=("127.0.0.1", "localhost"))},
        executor=_Counting(adapter="tavily", api_key="k", transport=_httpx.MockTransport(_handler)),
    )
    out = await gw.execute(
        tool_name="web_search",
        args={"query": "x"},
        domain="knowledge",
        trace_id="t",
        tenant_id="t",
        user_id="u",
    )
    assert out["status"] == "error"
    assert str(out["reason"]) == "protocol: 响应解码失败(DecodingError)"
    assert len(calls) == 1  # 响应类不重试


# ── 停用域零感知 ──


@pytest.mark.asyncio
async def test_disabled_domain_zero_awareness() -> None:
    """停用域:handles 即刻 False,直接执行被拒(policy 分类)。"""
    executor = _MockExecutor([{"results": []}])
    gw = _gateway(executor)
    assert gw.handles("knowledge")
    gw.disable("knowledge")
    assert not gw.handles("knowledge")
    out = await gw.execute(
        tool_name="knowledge",
        args={"query": "x"},
        domain="knowledge",
        trace_id="t",
        tenant_id="tenant",
        user_id="u",
    )
    assert out["status"] == "rejected"
    assert executor.calls == []
    gw.enable("knowledge")
    assert gw.handles("knowledge")


# ── 组合根接线回归 ──


@pytest.fixture
def restore_registry() -> Any:
    """激活态与停用集还原(模块全局防跨用例污染:中间断言失败也不残留)。"""
    from kongming.toolcall import registry

    saved = registry.effective()
    try:
        yield registry
    finally:
        registry.set_runtime_disabled(frozenset())
        registry.activate(saved)


@pytest.mark.asyncio
async def test_domain_quota_gate_wired(
    client: Any,
    session: Any,
    make_user: Any,
    monkeypatch: Any,
    restore_registry: Any,
) -> None:
    """域配额第二维度接线:超限即 RATE_LIMITED(域配额文案),不触达执行器。"""
    from kongming.config import get_settings
    from kongming.toolcall import dependencies as router_mod
    from tests.test_websearch_domain import _FakeWebSearchPort
    from tests.tool_helpers import _mk_tenant, _req, _user_with_tools

    registry = restore_registry
    registry.activate(registry.build_registry(websearch_provider="tavily", websearch_api_key="k"))
    monkeypatch.setattr(get_settings(), "toolgateway_websearch_quota", 1)

    fake = _FakeWebSearchPort()
    monkeypatch.setattr(router_mod, "_build_websearch_client", lambda: fake)
    await _mk_tenant(session, "ek")  # _user_with_tools 绑定 ek 租户(其模块 fixture 在此不生效)
    uid = await _user_with_tools(session, make_user, "qwire", ["tool:websearch:search"])
    headers = {"X-Internal-Token": "test-internal-token"}
    req = _req("web_search", {"query": "网关"}, user_id=uid, tenant_key="ek")
    r1 = await client.post("/internal/tool/execute", headers=headers, json=req)
    assert r1.status_code == 200 and r1.json()["status"] == "executed", r1.text
    r2 = await client.post("/internal/tool/execute", headers=headers, json=req)
    body = r2.json()
    assert body["status"] == "rate_limited", body
    assert "域配额" in str(body.get("reason"))


@pytest.mark.asyncio
async def test_disabled_domain_disappears_from_injection(restore_registry: Any) -> None:
    """停用集 → 注入集与分发解析同源消失(spec 域治理面;与「域从未存在」同形)。"""
    registry = restore_registry
    registry.activate(registry.build_registry(websearch_provider="tavily", websearch_api_key="k"))
    registry.set_runtime_disabled(frozenset({"web_search"}))
    assert registry.get_tool("web_search") is None  # 分发解析即拒
    codes = {"tool:websearch:search"}
    assert all(s["name"] != "web_search" for s in registry.filter_specs(codes))
    registry.set_runtime_disabled(frozenset())
    assert registry.get_tool("web_search") is not None
    assert [s["name"] for s in registry.filter_specs(codes)] == ["web_search"]


@pytest.mark.asyncio
async def test_domain_governance_routes(
    client: Any,
    session: Any,
    make_user: Any,
    monkeypatch: Any,
    restore_registry: Any,
) -> None:
    """验证域管理接口的权限、未知域、停用和恢复行为:
    停用 → internal execute 拒(TOOL_NOT_REGISTERED)且审计带 domain;
    启用恢复 executed。停用集为进程内存态(单进程部署形态),重启随部署清单复位。"""
    from kongming.auth.seed import seed_all
    from kongming.config import get_settings
    from kongming.toolcall import dependencies as router_mod
    from kongming.toolgateway.gateway import ToolGateway
    from kongming.toolgateway.models import load_manifest
    from kongming.toolgateway.websearch import WebSearchPolicy
    from tests.test_chat_query import _auth

    registry = restore_registry
    registry.activate(registry.build_registry(websearch_provider="tavily", websearch_api_key="k"))
    manifest, _w = load_manifest(
        [
            {
                "id": "websearch",
                "endpoint": "https://api.tavily.com/search",
                "whitelist_hosts": ["api.tavily.com"],
                "timeout_ms": 5000,
                "quota_per_minute": 60,
                "redaction": "allowlist:websearch",
            }
        ]
    )
    gw = ToolGateway(
        manifest=manifest,
        policies={
            "websearch": WebSearchPolicy(whitelist_hosts=manifest["websearch"].whitelist_hosts)
        },
    )
    monkeypatch.setattr(router_mod, "get_gateway", lambda: gw)

    from tests.test_system_admin import _admin

    await seed_all(session, get_settings())
    _, token_admin = await _admin(client, session, make_user)
    await _assert_governance_requires_permission(client, session, make_user, token_admin)

    await _assert_disable_rejects_and_enable_restores(
        client, session, make_user, monkeypatch, token_admin
    )
    await _assert_disable_persists_and_restores_on_boot(
        client, session, make_user, token_admin, gw, router_mod
    )
    # 清理:恢复启用态(写穿回库+本地生效),不污染同文件后续用例
    await client.post("/system/tools/domains/websearch/enable", headers=_auth(token_admin))


async def _assert_governance_requires_permission(
    client: Any, session: Any, make_user: Any, token_admin: str
) -> None:
    """无 system:tool:manage 的普通用户读治理面 403;管理员读 200 且携带 websearch 域。"""
    from sqlalchemy import text as sql_text

    from tests.test_chat_query import _auth, _bind, _login

    # 无 system:tool:manage → 403(治理面读写全拒)
    plain = await make_user("gw_plain")
    tid = (
        await session.execute(
            sql_text("SELECT id FROM public.sys_tenant WHERE tenant_key='default'")
        )
    ).scalar_one()
    await _bind(session, "sys_user_tenant", plain, str(tid))
    token_plain = await _login(client, "gw_plain")
    r403 = await client.get("/system/tools/domains", headers=_auth(token_plain))
    assert r403.status_code == 403

    r200 = await client.get("/system/tools/domains", headers=_auth(token_admin))
    assert r200.status_code == 200
    assert any(d["id"] == "websearch" and d.get("disabled") is False for d in r200.json())
    r404 = await client.post("/system/tools/domains/nope/disable", headers=_auth(token_admin))
    assert r404.status_code == 404


async def _assert_disable_rejects_and_enable_restores(
    client: Any, session: Any, make_user: Any, monkeypatch: Any, token_admin: str
) -> None:
    """停用 → internal execute 拒(与「工具从未注册」同形)+ 审计落 domain;
    启用恢复 executed。"""
    from sqlalchemy import text as sql_text

    from kongming.toolcall import dependencies as router_mod
    from tests.test_chat_query import _auth
    from tests.test_websearch_domain import _FakeWebSearchPort
    from tests.tool_helpers import _mk_tenant, _req, _user_with_tools

    # 停用 → internal execute 拒(与「工具从未注册」同形)+ 审计落 domain
    fake = _FakeWebSearchPort()
    monkeypatch.setattr(router_mod, "_build_websearch_client", lambda: fake)
    await _mk_tenant(session, "ek")
    uid = await _user_with_tools(session, make_user, "gwgov", ["tool:websearch:search"])
    headers = {"X-Internal-Token": "test-internal-token"}
    req = _req("web_search", {"query": "网关"}, user_id=uid, tenant_key="ek")
    rd = await client.post("/system/tools/domains/websearch/disable", headers=_auth(token_admin))
    assert rd.status_code == 204
    rrej = await client.post("/internal/tool/execute", headers=headers, json=req)
    assert rrej.status_code == 200 and rrej.json()["status"] == "rejected", rrej.text
    assert rrej.json()["reason"] == "TOOL_NOT_REGISTERED"
    audit = (
        (
            await session.execute(
                sql_text(
                    "SELECT status, domain FROM public.sys_tool_audit"
                    " ORDER BY created_at DESC LIMIT 1"
                )
            )
        )
        .mappings()
        .one()
    )
    assert audit["status"] == "REJECTED"
    assert audit["domain"] == "websearch"

    # 启用恢复:同请求 executed
    ren = await client.post("/system/tools/domains/websearch/enable", headers=_auth(token_admin))
    assert ren.status_code == 204
    rok = await client.post("/internal/tool/execute", headers=headers, json=req)
    assert rok.status_code == 200 and rok.json()["status"] == "executed", rok.text


async def _assert_disable_persists_and_restores_on_boot(
    client: Any, session: Any, make_user: Any, token_admin: str, gw: Any, router_mod: Any
) -> None:
    """停用写穿入库;模拟重启(清运行时态 + lifespan 同款装载)后意图与
    注册表同步恢复,execute 仍拒。"""
    from sqlalchemy import text as sql_text

    from kongming.toolcall import registry
    from tests.test_chat_query import _auth
    from tests.tool_helpers import _req, _user_with_tools

    headers = {"X-Internal-Token": "test-internal-token"}
    uid = await _user_with_tools(session, make_user, "gwboot", ["tool:websearch:search"])
    req = _req("web_search", {"query": "网关"}, user_id=uid, tenant_key="ek")
    # 停用持久化:写穿入库;「重启」=清网关单例缓存后经启动装载恢复意图
    await client.post("/system/tools/domains/websearch/disable", headers=_auth(token_admin))
    states = (
        await session.execute(
            sql_text("SELECT domain_id, disabled FROM public.sys_tool_domain_state")
        )
    ).all()
    assert ("websearch", True) in [(r[0], bool(r[1])) for r in states]
    gw.apply_disabled(frozenset())  # 模拟重启(进程内运行时态清零)
    await router_mod.load_persisted_domain_states()  # lifespan 同款装载
    assert gw._disabled == {"websearch"}  # 持久意图随装载恢复
    # 运行时注册表同步:停用域工具与「域从未存在」同形(注入集消失+分发解析即拒)
    assert registry.get_tool("web_search") is None
    rrej2 = await client.post("/internal/tool/execute", headers=headers, json=req)
    assert rrej2.status_code == 200 and rrej2.json()["status"] == "rejected", rrej2.text
    assert rrej2.json()["reason"] == "TOOL_NOT_REGISTERED", rrej2.text

    await client.post("/system/tools/domains/websearch/enable", headers=_auth(token_admin))


def test_manifest_invalid_host_entries_dropped() -> None:
    """白名单非法条目(非字符串/空/含路径或通配)剔除;全部非法整域剔除。"""
    domains, _w = load_manifest(
        [_raw_manifest(whitelist_hosts=["127.0.0.1", "", "bad/host", "*", 123])[0]]
    )
    assert set(domains) == {"knowledge"}
    assert domains["knowledge"].whitelist_hosts == ("127.0.0.1",)
    domains2, w2 = load_manifest([_raw_manifest(id="gone", whitelist_hosts=["bad/host", ""])[0]])
    assert domains2 == {}
    assert any("gone" in w for w in w2)


def test_manifest_bool_scalars_rejected() -> None:
    """bool 是 int 子类:true 穿透校验会得到 1ms 超时/1 次配额——显式拒绝。"""
    assert load_manifest([_raw_manifest(timeout_ms=True)[0]])[0] == {}
    assert load_manifest([_raw_manifest(quota_per_minute=False)[0]])[0] == {}
