# pyright: reportPrivateUsage=false
"""100 internal-connector-domain 测试:ticket 域(内部工单,部署级条件注册)。

照 094 websearch 域测试形态(安全规则类测试先行,宪法 Article III):
- 纯函数段:端点解析单源 / 域映射 / 策略器四职责(白名单拒绝零网络请求、
  三工具形态校验、工单卡 allowlist 脱敏、重试分类)/ 注册条件三形态 /
  组合配置校验(prod 拒启 / dev 告警降级)。
- 执行器段:出站以「最小工单 API 约定」的仓库 mock(scripts/mock_ticket_server.py,
  测试内起停线程)为真实目标——测试过的就是演示的;失败路径用真实 TCP 存根
  (连接计数可断言:响应类不重试、连接类重试恰好一次、凭证只进请求头)。
- 集成段(真实 PG+Redis,conftest 基建):经 /internal/tool/execute 打全链,
  断言观察形态 / 确认流(确认前零出站)/ 未授权排除 / 域配额分桶 /
  审计行 / 凭证不入日志与审计。
"""

import importlib.util
import json
import logging
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.config import Settings
from kongming.toolgateway.models import (
    DOMAIN_TOOLS,
    TICKET_LIMIT_MAX,
    TICKET_LIMIT_MIN,
    TICKET_QUERY_MAX_LEN,
    RetryClass,
    endpoint_host,
    load_manifest,
    parse_ticket_endpoint,
)
from kongming.toolgateway.ticket import (
    _CARD_FIELDS,
    TicketError,
    TicketExecutor,
    TicketPolicy,
)
from tests.test_websearch_domain import _http_response, _RecordingStub

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "mock_ticket_server.py"
_TICKET_KEY = "e2e-ticket-key"  # 测试凭证(道具,非密钥)


def _load_mock() -> Any:
    """按文件路径加载仓库 mock 脚本(scripts/ 非包;单一实现供 e2e 与演示共用)。"""
    spec = importlib.util.spec_from_file_location("mock_ticket_server", _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── 纯函数段:端点解析单源与域映射 ──


def test_parse_ticket_endpoint_single_source() -> None:
    """KM_TICKET_BASE_URL 解析(注册条件与网关清单声明同源):
    合法 http(s) 归一去尾斜杠;空/非 http(s)/无主机/带 query → None。"""
    assert parse_ticket_endpoint("http://127.0.0.1:8123") == "http://127.0.0.1:8123"
    assert parse_ticket_endpoint("http://127.0.0.1:8123/") == "http://127.0.0.1:8123"
    assert parse_ticket_endpoint(" https://tickets.corp.example/api ") == (
        "https://tickets.corp.example/api"
    )
    assert endpoint_host("https://tickets.corp.example/api") == "tickets.corp.example"
    assert parse_ticket_endpoint("") is None
    assert parse_ticket_endpoint("   ") is None
    assert parse_ticket_endpoint("ftp://127.0.0.1:8123") is None
    assert parse_ticket_endpoint("http://") is None
    assert parse_ticket_endpoint("http://127.0.0.1:8123/?x=1") is None


def test_domain_tools_mapping_single_source() -> None:
    """工具名→域映射单源:ticket 三工具同域(审计域标签与配额闸共用)。"""
    assert DOMAIN_TOOLS["ticket_search"] == "ticket"
    assert DOMAIN_TOOLS["ticket_get"] == "ticket"
    assert DOMAIN_TOOLS["ticket_create"] == "ticket"


# ── 域策略器(纯函数) ──


def _policy() -> TicketPolicy:
    return TicketPolicy(whitelist_hosts=("127.0.0.1",))


def test_policy_validate_args_search_shape() -> None:
    p = _policy()
    assert p.validate_args({}) is None  # 检索全量入参可省(查最近工单形态)
    assert p.validate_args({"query": "报表"}) is None
    assert p.validate_args({"query": ""}) is None  # 空关键词=不过滤(mock 约定)
    assert p.validate_args({"query": "q" * (TICKET_QUERY_MAX_LEN + 1)}) is not None  # 超长
    assert p.validate_args({"query": "q", "status": "open"}) is None
    assert p.validate_args({"query": "q", "status": "   "}) is not None  # 空白状态非法
    assert p.validate_args({"query": "q", "limit": TICKET_LIMIT_MAX}) is None
    assert p.validate_args({"query": "q", "limit": TICKET_LIMIT_MIN - 1}) is not None
    assert p.validate_args({"query": "q", "limit": TICKET_LIMIT_MAX + 1}) is not None
    assert p.validate_args({"query": "q", "limit": True}) is not None  # bool 穿透
    assert p.validate_args({"query": "q", "limit": "3"}) is not None
    assert p.validate_args({"query": 1}) is not None  # 关键词须为字符串


def test_policy_validate_args_get_shape() -> None:
    p = _policy()
    assert p.validate_args({"id": "T-1001"}) is None
    assert p.validate_args({"id": "   "}) is not None  # 空白标识
    assert p.validate_args({"id": 7}) is not None  # 须为字符串
    # 空参落检索形态合法(检索全量);单查缺 id 不存在形态歧义
    assert p.validate_args({}) is None


def test_policy_validate_args_create_shape_and_mixing() -> None:
    p = _policy()
    ok: dict[str, object] = {"title": "标题", "description": "描述", "priority": "high"}
    assert p.validate_args(dict(ok)) is None
    assert p.validate_args({**ok, "assignee": "张三"}) is None
    assert p.validate_args({"title": "t", "description": " "}) is not None  # 空白描述
    assert p.validate_args({"title": "t", "description": "d"}) is not None  # 缺优先级
    assert p.validate_args({"title": "t", "priority": "high"}) is not None  # 缺描述
    assert p.validate_args({**ok, "priority": 3}) is not None  # 优先级须为字符串(透传)
    # 形态混合显式拒绝(三工具入参集不相交;schema additionalProperties 之外纵深)
    assert p.validate_args({"id": "T-1", "query": "q"}) is not None
    mixed_create: dict[str, object] = {
        "id": "T-1",
        "title": "t",
        "description": "d",
        "priority": "high",
    }
    assert p.validate_args(mixed_create) is not None
    assert p.validate_args({**ok, "limit": 3}) is not None
    assert p.validate_args({"query": "q", "title": "t"}) is not None


def test_policy_exit_host_allowlist() -> None:
    p = TicketPolicy(whitelist_hosts=("tickets.corp.example",))
    assert p.exit_host_allowed("tickets.corp.example")
    assert p.exit_host_allowed("TICKETS.CORP.EXAMPLE")  # 大小写不敏感
    assert not p.exit_host_allowed("evil.example.com")


def test_policy_redact_allowlist_cards() -> None:
    """脱敏 allowlist(工单卡六字段):描述与工单系统内部元数据永不入观察;
    列表形态与单卡形态分别收口;非法形态收敛不透传原文。"""
    p = _policy()
    out = p.redact(
        {
            "tickets": [
                {
                    "id": "T-1001",
                    "title": "报表导出超时",
                    "description": "内部描述不进观察",
                    "status": "open",
                    "priority": "high",
                    "assignee": "张三",
                    "created_at": "2026-10-09T02:15:00+00:00",
                    "updated_at": "2026-10-09T02:15:00+00:00",
                    "internal_id": 42,  # 工单系统元数据
                    "reporter": "某人",  # 白名单外字段
                },
                "not-a-dict",  # 非法条目丢弃
            ]
        }
    )
    assert out == {
        "tickets": [
            {
                "id": "T-1001",
                "title": "报表导出超时",
                "status": "open",
                "priority": "high",
                "assignee": "张三",
                "created_at": "2026-10-09T02:15:00+00:00",
                "updated_at": "2026-10-09T02:15:00+00:00",
            }
        ]
    }
    assert "description" not in _CARD_FIELDS  # allowlist 单源不含描述(约定明文)
    # 单卡形态(get/create)
    card = p.redact({"id": "T-9", "title": "t", "description": "d", "extra": "x"})
    assert card == {
        "id": "T-9",
        "title": "t",
        "status": None,
        "priority": None,
        "assignee": None,
        "created_at": None,
        "updated_at": None,
    }
    # 非法形态收敛
    assert p.redact(None) == {"tickets": []}
    assert p.redact({"tickets": "oops"}) == {"tickets": []}
    assert p.redact("raw text") == {"tickets": []}


def test_policy_classify_and_failure_reason() -> None:
    p = _policy()
    assert p.classify(None, delivered=True) is RetryClass.OK
    assert (
        p.classify(TicketError("unreachable: 工单系统拒连(ConnectError)"), delivered=False)
        is RetryClass.UNDELIVERED
    )
    assert p.classify(TicketError("protocol: 上游 HTTP 500"), delivered=True) is RetryClass.PROTOCOL
    assert p.failure_reason(RetryClass.UNDELIVERED, None).startswith("unreachable:")
    err = TicketError("protocol: 工单不存在")
    assert p.failure_reason(RetryClass.PROTOCOL, err) == "protocol: 工单不存在"


# ── 注册条件(部署级,纯函数)与组合配置校验 ──


def test_build_registry_ticket_conditions() -> None:
    from kongming.toolcall.registry import REGISTRY, build_registry

    base = build_registry(None)
    assert "ticket_search" not in base  # 缺省=静态基表,零回归
    assert set(base) == set(REGISTRY)

    gated = build_registry(None, ticket_base_url="http://127.0.0.1:8123", ticket_api_key="k")
    assert set(gated) == {*REGISTRY, "ticket_search", "ticket_get", "ticket_create"}
    assert gated["ticket_search"].perm_code == "tool:ticket:search"
    assert gated["ticket_get"].perm_code == "tool:ticket:search"  # 读同权限点(search/get)
    assert gated["ticket_search"].write is False
    assert gated["ticket_get"].require_confirm is False
    # create:写工具 + 缺省需确认(确认流独立于权限点,design D4)
    assert gated["ticket_create"].perm_code == "tool:ticket:manage"
    assert gated["ticket_create"].write is True
    assert gated["ticket_create"].require_confirm is True

    # 双条件缺一不注册(与「域从未存在」不可区分)
    key_only_space = build_registry(None, ticket_base_url="http://h", ticket_api_key=" ")
    assert "ticket_search" not in key_only_space
    assert "ticket_search" not in build_registry(None, ticket_base_url="", ticket_api_key="k")
    # 端点形态非法同属未注册(与网关清单声明同源判定,不出现「注入了却执行必拒」)
    assert "ticket_search" not in build_registry(
        None, ticket_base_url="ftp://h", ticket_api_key="k"
    )
    # 端点去尾斜杠归一(形态合法即注册,归一由单源函数承担)
    assert "ticket_search" in build_registry(
        None, ticket_base_url="http://127.0.0.1:8123/", ticket_api_key="k"
    )


_PROD_KW: dict[str, Any] = {
    "environment": "prod",
    "jwt_private_key_pem": "k",
    "jwt_public_key_pem": "k",
    "internal_token": "k",
    "datasource_encrypt_key": "k" * 32,
    "billing_mock_enabled": False,
    # 钉空隔离本地环境的 KM_TICKET_*(防本机配置把既有 prod 用例打红)
    "ticket_base_url": "",
    "ticket_api_key": "",
}


def test_ticket_misconfiguration_matrix() -> None:
    # 均未配置=合法缺省(零感知;显式空串隔离本地环境)
    bare = Settings(environment="dev", ticket_base_url="", ticket_api_key="")
    assert bare.ticket_misconfiguration() is None
    # 齐备=合法
    ok = Settings(ticket_base_url="http://127.0.0.1:8123", ticket_api_key="k")
    assert ok.ticket_misconfiguration() is None
    # 只配端点缺凭证=残缺
    missing = Settings(ticket_base_url="http://127.0.0.1:8123", ticket_api_key="")
    mis = missing.ticket_misconfiguration()
    assert mis is not None and "KM_TICKET_API_KEY" in mis
    # 只配凭证缺端点=残缺(与 websearch 同口径:排障「配了却没生效」不能无信号)
    keyonly = Settings(ticket_base_url="", ticket_api_key="k")
    mis2 = keyonly.ticket_misconfiguration()
    assert mis2 is not None and "KM_TICKET_BASE_URL" in mis2
    # 端点非法=残缺
    bogus = Settings(ticket_base_url="not a url", ticket_api_key="k")
    mis3 = bogus.ticket_misconfiguration()
    assert mis3 is not None and "非法" in mis3


def test_prod_failclosed_ticket_misconfig() -> None:
    s = Settings(
        **cast("dict[str, Any]", {**_PROD_KW, "ticket_base_url": "http://h", "ticket_api_key": ""})
    )
    with pytest.raises(RuntimeError, match="KM_TICKET_API_KEY"):
        s.fail_closed_checks()
    s2 = Settings(
        **cast("dict[str, Any]", {**_PROD_KW, "ticket_base_url": "", "ticket_api_key": "k"})
    )
    with pytest.raises(RuntimeError, match="KM_TICKET_BASE_URL"):
        s2.fail_closed_checks()
    s3 = Settings(
        **cast("dict[str, Any]", {**_PROD_KW, "ticket_base_url": "bad url", "ticket_api_key": "k"})
    )
    with pytest.raises(RuntimeError, match="非法"):
        s3.fail_closed_checks()
    # 齐备不拒启
    s4 = Settings(
        **cast(
            "dict[str, Any]",
            {**_PROD_KW, "ticket_base_url": "http://127.0.0.1:8123", "ticket_api_key": "k"},
        )
    )
    s4.fail_closed_checks()  # 不抛


@pytest.fixture(scope="session", autouse=True)
def _import_main_early() -> None:  # pyright: ignore[reportUnusedFunction]
    """先于用例阶段触发 kongming.main 模块导入(其 setup_logging 会重建根
    handler——若在用例 call 阶段首导,会把 pytest 本阶段已挂的日志捕获
    handler 一并清掉,caplog 全空;websearch 同款用例即踩此序,全量套件
    因更早用例已导入 main 而绿)。"""
    import kongming.main

    assert kongming.main.app.title  # 消费导入(setup_logging 副作用已完成)


def test_lifespan_ticket_misconfig_warns_and_degrades(
    monkeypatch: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """dev 告警降级(spec SHALL,websearch 同款):组合残缺 → kongming.main 显式
    告警,且装配条件与告警同源(域不注册);合法缺省 → 静默零感知。"""
    from kongming.config import get_settings
    from kongming.main import _warn_ticket_misconfiguration
    from kongming.toolcall import registry

    lg = logging.getLogger("kongming.main")
    orig_disabled = lg.disabled
    lg.disabled = False  # 会话级 alembic fileConfig 可能置 disabled(既有坑)
    try:
        s = get_settings()
        monkeypatch.setattr(s, "ticket_base_url", "http://127.0.0.1:8123")
        monkeypatch.setattr(s, "ticket_api_key", "")
        with caplog.at_level(logging.WARNING, logger="kongming.main"):
            _warn_ticket_misconfiguration()
        assert any(
            "ticket" in r.getMessage() and "KM_TICKET_API_KEY" in r.getMessage()
            for r in caplog.records
        ), [r.getMessage() for r in caplog.records]
        # 降级=域不注册(lifespan 装配走同一判定)
        built = registry.build_registry(
            None, ticket_base_url=s.ticket_base_url, ticket_api_key=s.ticket_api_key
        )
        assert "ticket_search" not in built
        # 合法缺省:静默
        monkeypatch.setattr(s, "ticket_base_url", "")
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="kongming.main"):
            _warn_ticket_misconfiguration()
        assert not caplog.records
    finally:
        lg.disabled = orig_disabled


# ── 执行器段:真实 mock(出站目标)与 TCP 存根(失败面) ──


@pytest.fixture(scope="session")
def mock_mod() -> Any:
    return _load_mock()


@pytest.fixture
async def mock_ticket(mock_mod: Any) -> AsyncIterator[Any]:
    """约定 mock 服务(测试内起停,不依赖手工启动;每次用例 fresh 种子)。"""
    server = mock_mod.MockTicketServer(token=_TICKET_KEY)
    server.start()
    try:
        yield server
    finally:
        server.stop()


def _gateway_with_ticket(
    base_url: str, *, api_key: str = _TICKET_KEY, timeout_ms: int = 3000
) -> Any:
    """网关装配:ticket 域端点指向 mock/存根(白名单由端点主机单源派生,
    与组合根 _build_gateway 同构)。"""
    from kongming.toolgateway.gateway import ToolGateway
    from kongming.toolgateway.ticket import TicketPolicy  # noqa: PLC0415

    endpoint = parse_ticket_endpoint(base_url)
    assert endpoint is not None
    manifest, _ = load_manifest(
        [
            {
                "id": "ticket",
                "endpoint": endpoint,
                "whitelist_hosts": [endpoint_host(endpoint)],
                "timeout_ms": timeout_ms,
                "quota_per_minute": 60,
                "redaction": "allowlist:ticket",
            }
        ]
    )
    return ToolGateway(
        manifest=manifest,
        policies={"ticket": TicketPolicy(whitelist_hosts=manifest["ticket"].whitelist_hosts)},
        domain_executors={"ticket": TicketExecutor(api_key=api_key)},
    )


async def _ticket_once(
    gw: Any, args: dict[str, object], tool: str = "ticket_search"
) -> dict[str, object]:
    return await gw.execute(
        tool_name=tool,
        args=args,
        domain="ticket",
        trace_id="tr-test",
        tenant_id="t",
        user_id="u",
    )


async def test_executor_search_via_mock(mock_ticket: Any) -> None:
    """检索走真实 mock:约定响应归一 + 策略器 allowlist(描述/内部字段不入观察)。"""
    gw = _gateway_with_ticket(mock_ticket.base_url)
    out = await _ticket_once(gw, {"status": "open", "limit": 2})
    assert out["status"] == "executed", out
    result = cast("dict[str, object]", out["result"])
    cards = cast("list[dict[str, object]]", result["tickets"])
    assert len(cards) == 2
    assert set(cards[0]) == set(_CARD_FIELDS)
    assert cards[0]["id"] == "T-1001"
    assert "description" not in cards[0]  # 白名单外字段不进观察
    # 空结果如实表达(计数由分发层按列表长度装配,不编造命中)
    out2 = await _ticket_once(gw, {"query": "不存在的关键词xyz"})
    result2 = cast("dict[str, object]", out2["result"])
    assert result2["tickets"] == []


async def test_executor_get_via_mock_and_missing_friendly(mock_ticket: Any) -> None:
    """单查命中返回标准化详情卡;不存在按响应类失败如实表达(友好「不存在」,
    不编造、不静默吐空)。"""
    gw = _gateway_with_ticket(mock_ticket.base_url)
    out = await _ticket_once(gw, {"id": "T-1002"}, tool="ticket_get")
    assert out["status"] == "executed", out
    card = cast("dict[str, object]", out["result"])
    assert card["id"] == "T-1002"
    assert set(card) == set(_CARD_FIELDS)
    miss = await _ticket_once(gw, {"id": "T-9999"}, tool="ticket_get")
    assert miss["status"] == "error"
    assert str(miss["reason"]).startswith("protocol: 工单不存在")


async def test_executor_create_via_mock(mock_ticket: Any) -> None:
    """创建走真实 mock:返回创建卡;随后检索可见(mock 内存态同实现)。"""
    gw = _gateway_with_ticket(mock_ticket.base_url)
    out = await _ticket_once(
        gw,
        {"title": "演示工单", "description": "e2e 创建", "priority": "high", "assignee": "张三"},
        tool="ticket_create",
    )
    assert out["status"] == "executed", out
    card = cast("dict[str, object]", out["result"])
    assert card["id"] == "T-1005"  # 种子最大号 +1(约定:服务端生成标识)
    assert card["status"] == "open"  # 初始状态(约定)
    found = await _ticket_once(gw, {"query": "演示工单"})
    result = cast("dict[str, object]", found["result"])
    cards = cast("list[dict[str, object]]", result["tickets"])
    assert [c["id"] for c in cards] == ["T-1005"]


async def test_executor_wrong_credential_protocol_fail(mock_ticket: Any) -> None:
    """凭证不匹配 → mock 401 → 响应类失败收口(不重试,凭证不出现在文案)。"""
    gw = _gateway_with_ticket(mock_ticket.base_url, api_key="wrong-key")
    out = await _ticket_once(gw, {})
    assert out["status"] == "error"
    reason = str(out["reason"])
    assert reason.startswith("protocol: 上游 HTTP 401")
    assert _TICKET_KEY not in reason and "wrong-key" not in reason  # 凭证不入失败文案


async def test_executor_headers_and_request_dialect() -> None:
    """TCP 存根断言出站形态:凭证只进 Bearer 请求头(不进 URL/报文);三操作
    按约定分派(GET /tickets?…、GET /tickets/{id}、POST /tickets)。"""
    ok_list = _http_response(json.dumps({"tickets": [], "total": 0}))
    stub = _RecordingStub(ok_list)
    try:
        gw = _gateway_with_ticket(f"http://127.0.0.1:{stub.port}")
        await _ticket_once(gw, {"query": "报表", "status": "open", "limit": 2})
    finally:
        stub.close()
    req = stub.seen[0]
    assert "Authorization: Bearer e2e-ticket-key" in req["headers"]
    assert req["request_line"].startswith("GET /tickets?")
    assert "status=open" in req["request_line"] and "limit=2" in req["request_line"]
    assert "e2e-ticket-key" not in req["request_line"]  # 凭证不进 URL

    ok_card = _http_response(json.dumps({"id": "T-1", "title": "t", "status": "open"}))
    stub2 = _RecordingStub(ok_card)
    try:
        gw2 = _gateway_with_ticket(f"http://127.0.0.1:{stub2.port}")
        await _ticket_once(gw2, {"id": "T-1"}, tool="ticket_get")
    finally:
        stub2.close()
    assert stub2.seen[0]["request_line"].startswith("GET /tickets/T-1 ")

    stub3 = _RecordingStub(ok_card)
    try:
        gw3 = _gateway_with_ticket(f"http://127.0.0.1:{stub3.port}")
        await _ticket_once(
            gw3,
            {"title": "t", "description": "d", "priority": "high", "assignee": "张三"},
            tool="ticket_create",
        )
    finally:
        stub3.close()
    req3 = stub3.seen[0]
    assert req3["request_line"].startswith("POST /tickets ")
    sent = json.loads(req3["body"])
    assert sent == {"title": "t", "description": "d", "priority": "high", "assignee": "张三"}


async def test_executor_protocol_failures_no_retry() -> None:
    """响应类失败(HTTP 500/报文畸形/结构缺字段)不重试:单连接即收口。"""
    stub = _RecordingStub(_http_response("boom", status=500))
    try:
        gw = _gateway_with_ticket(f"http://127.0.0.1:{stub.port}")
        out = await _ticket_once(gw, {})
    finally:
        stub.close()
    assert out["status"] == "error"
    assert str(out["reason"]).startswith("protocol: 上游 HTTP 500")
    assert stub.stub.connections == 1

    stub2 = _RecordingStub(_http_response("not-json{{"))
    try:
        gw2 = _gateway_with_ticket(f"http://127.0.0.1:{stub2.port}")
        out2 = await _ticket_once(gw2, {})
    finally:
        stub2.close()
    assert out2["status"] == "error"
    assert str(out2["reason"]).startswith("protocol: 响应报文畸形")
    assert stub2.stub.connections == 1

    # 结构缺字段:检索响应缺 tickets 列表 = 报文不合格,不静默吐空
    stub3 = _RecordingStub(_http_response(json.dumps({"oops": []})))
    try:
        gw3 = _gateway_with_ticket(f"http://127.0.0.1:{stub3.port}")
        out3 = await _ticket_once(gw3, {})
    finally:
        stub3.close()
    assert out3["status"] == "error"
    assert str(out3["reason"]).startswith("protocol: 工单响应缺 tickets 列表")
    assert stub3.stub.connections == 1

    # 单查 404:响应类不重试(友好「不存在」文案)
    stub4 = _RecordingStub(_http_response(json.dumps({"error": "ticket not found"}), status=404))
    try:
        gw4 = _gateway_with_ticket(f"http://127.0.0.1:{stub4.port}")
        out4 = await _ticket_once(gw4, {"id": "T-404"}, tool="ticket_get")
    finally:
        stub4.close()
    assert out4["status"] == "error"
    assert str(out4["reason"]).startswith("protocol: 工单不存在")
    assert stub4.stub.connections == 1


async def test_executor_undelivered_retries_once() -> None:
    """连接层未送达(accept 后关连)重试恰好一次:第二次尝试成功交付。"""
    ok = _http_response(json.dumps({"tickets": [], "total": 0}))
    stub = _RecordingStub(ok, responds=[None, ok])
    try:
        gw = _gateway_with_ticket(f"http://127.0.0.1:{stub.port}")
        out = await _ticket_once(gw, {})
    finally:
        stub.close()
    assert out["status"] == "executed", out
    assert stub.stub.connections == 2  # 未送达重试至多一次的实证计数


async def test_executor_undelivered_exhausted_unreachable() -> None:
    """两次尝试均未送达:按 unreachable 收口(不再第三次尝试)。"""
    stub = _RecordingStub(b"x", responds=[None])
    try:
        gw = _gateway_with_ticket(f"http://127.0.0.1:{stub.port}")
        out = await _ticket_once(gw, {})
    finally:
        stub.close()
    assert out["status"] == "error"
    assert str(out["reason"]).startswith("unreachable:")
    assert stub.stub.connections == 2


# ── 集成段:真实 PG+Redis,经 /internal/tool/execute 全链(mock 为出站目标) ──

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
        text(f"INSERT INTO public.{table} (user_id, {col}) VALUES (:u, :t)"),  # noqa: S608  # 测试常量
        {"u": user_id, "t": target_id},
    )
    await s.commit()


async def _user_with_tools(s: AsyncSession, make_user: Any, username: str, codes: list[str]) -> str:
    uid = await make_user(username)
    rid = await _mk_role_with_perms(s, f"role_{username}", codes)
    tid = (
        await s.execute(text("SELECT id FROM public.sys_tenant WHERE tenant_key=:k"), {"k": "ek"})
    ).scalar_one()
    await _bind(s, "sys_user_role", uid, rid)
    await _bind(s, "sys_user_tenant", uid, str(tid))
    return uid


async def _mk_chat_session(s: AsyncSession, user_id: str) -> str:
    tid = (
        await s.execute(text("SELECT id FROM public.sys_tenant WHERE tenant_key=:k"), {"k": "ek"})
    ).scalar_one()
    sid = (
        await s.execute(
            text(
                "INSERT INTO public.chat_session (id, user_id, tenant_id, title, datasource_key)"
                " VALUES (gen_random_uuid(), :u, :t, 't', 'default') RETURNING id"
            ),
            {"u": user_id, "t": str(tid)},
        )
    ).scalar_one()
    await s.commit()
    return str(sid)


@pytest.fixture
def restore_registry() -> Any:
    """激活态还原(与既有域套件同款,防模块全局污染)。"""
    from kongming.toolcall import registry

    saved = registry.effective()
    try:
        yield registry
    finally:
        registry.set_runtime_disabled(frozenset())
        registry.activate(saved)


def _wire_ticket(monkeypatch: Any, mock_ticket: Any) -> Any:
    """组合根接线:网关与工单端口适配都指向真实 mock(全链真出站,无假件)。"""
    from kongming.toolcall import router as router_mod

    gw = _gateway_with_ticket(mock_ticket.base_url)
    monkeypatch.setattr(router_mod, "_get_gateway", lambda: gw)
    monkeypatch.setattr(
        router_mod, "_build_ticket_client", lambda: router_mod._GatewayTicketClient(gw)
    )
    return gw


async def _mock_count(mock_ticket: Any, mock_mod: Any) -> int:
    """经 HTTP 读工单总数(空过滤=全量),不戳 mock 内部结构。"""
    del mock_mod  # 保持签名对称(部分调用方传入仅为语义标注)
    import httpx  # noqa: PLC0415

    async with httpx.AsyncClient() as hc:
        resp = await hc.get(
            f"{mock_ticket.base_url}/tickets",
            headers={"Authorization": f"Bearer {_TICKET_KEY}"},
        )
        body = cast("dict[str, object]", resp.json())
        return int(cast("int", body["total"]))


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    from kongming.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_ticket_e2e_search_observation_and_audit(
    client: AsyncClient,
    session: AsyncSession,
    make_user: Any,
    monkeypatch: Any,
    restore_registry: Any,
    mock_ticket: Any,
    mock_mod: Any,
) -> None:
    """全链:注入集 → 权限 → 网关 → mock 出站 → 标准化观察 + 审计行(domain=ticket)。"""
    registry = restore_registry
    registry.activate(
        registry.build_registry(
            None, ticket_base_url=mock_ticket.base_url, ticket_api_key=_TICKET_KEY
        )
    )
    _wire_ticket(monkeypatch, mock_ticket)
    await _mk_tenant(session, "ek")
    uid = await _user_with_tools(session, make_user, "tkc", ["tool:ticket:search"])
    req = _req("ticket_search", {"status": "open", "limit": 2}, user_id=uid)
    r = await client.post("/internal/tool/execute", headers=INTERNAL, json=req)
    assert r.status_code == 200 and r.json()["status"] == "executed", r.text
    body = r.json()
    assert body["result"] == {
        "count": 2,
        "tickets": [
            {
                "id": "T-1001",
                "title": "报表导出超时",
                "status": "open",
                "priority": "high",
                "assignee": "张三",
                "created_at": "2026-10-09T02:15:00+00:00",
                "updated_at": "2026-10-09T02:15:00+00:00",
            },
            {
                "id": "T-1003",
                "title": "工单列表希望支持按优先级排序",
                "status": "open",
                "priority": "low",
                "assignee": "",
                "created_at": "2026-10-10T06:00:00+00:00",
                "updated_at": "2026-10-10T06:00:00+00:00",
            },
        ],
    }
    audits = (
        (
            await session.execute(
                text(
                    "SELECT status, domain, tool_name FROM public.sys_tool_audit WHERE trace_id=:t"
                ),
                {"t": req["trace_id"]},
            )
        )
        .mappings()
        .all()
    )
    assert [(a["status"], a["domain"], a["tool_name"]) for a in audits] == [
        ("EXECUTED", "ticket", "ticket_search")
    ]


async def test_ticket_e2e_get_missing_honest_error(
    client: AsyncClient,
    session: AsyncSession,
    make_user: Any,
    monkeypatch: Any,
    restore_registry: Any,
    mock_ticket: Any,
) -> None:
    """单查不存在:全链按响应类失败如实表达(审计 ERROR + 友好「不存在」)。"""
    registry = restore_registry
    registry.activate(
        registry.build_registry(
            None, ticket_base_url=mock_ticket.base_url, ticket_api_key=_TICKET_KEY
        )
    )
    _wire_ticket(monkeypatch, mock_ticket)
    await _mk_tenant(session, "ek")
    uid = await _user_with_tools(session, make_user, "tkm", ["tool:ticket:search"])
    req = _req("ticket_get", {"id": "T-404"}, user_id=uid)
    r = await client.post("/internal/tool/execute", headers=INTERNAL, json=req)
    assert r.status_code == 200 and r.json()["status"] == "error", r.text
    assert str(r.json()["reason"]).startswith("protocol: 工单不存在")
    row = (
        (
            await session.execute(
                text("SELECT status FROM public.sys_tool_audit WHERE trace_id=:t"),
                {"t": req["trace_id"]},
            )
        )
        .mappings()
        .one()
    )
    assert row["status"] == "ERROR"


async def test_ticket_create_confirm_flow_zero_outbound_before_confirm(
    client: AsyncClient,
    session: AsyncSession,
    make_user: Any,
    monkeypatch: Any,
    restore_registry: Any,
    mock_ticket: Any,
    mock_mod: Any,
) -> None:
    """创建确认流(写工具):确认前零出站(mock 无新单);确认后执行并如实
    返回;审计终态行参数摘要回填目标工单标识(spec)。"""
    from tests.test_chat_query import _auth, _login

    registry = restore_registry
    registry.activate(
        registry.build_registry(
            None, ticket_base_url=mock_ticket.base_url, ticket_api_key=_TICKET_KEY
        )
    )
    _wire_ticket(monkeypatch, mock_ticket)
    await _mk_tenant(session, "ek")
    uid = await _user_with_tools(session, make_user, "tkw", ["tool:ticket:manage", "chat:execute"])
    before = await _mock_count(mock_ticket, mock_mod)
    assert before == 4  # 种子四条

    req = _req(
        "ticket_create",
        {
            "title": "确认流演示",
            "description": "确认后创建",
            "priority": "high",
            "assignee": "张三",
        },
        user_id=uid,
    )
    r = await client.post("/internal/tool/execute", headers=INTERNAL, json=req)
    body = r.json()
    assert body["status"] == "confirm_required", r.text
    assert body["confirm_id"]
    assert await _mock_count(mock_ticket, mock_mod) == before  # 确认前零出站
    # 挂起行:原文留存(args_json)+ 域标签
    pend = (
        (
            await session.execute(
                text(
                    "SELECT status, domain, args_json FROM public.sys_tool_audit WHERE trace_id=:t"
                ),
                {"t": req["trace_id"]},
            )
        )
        .mappings()
        .one()
    )
    assert pend["status"] == "PENDING_CONFIRM" and pend["domain"] == "ticket"
    assert cast("dict[str, object]", pend["args_json"])["title"] == "确认流演示"

    # 确认执行(chat 面)
    sid = await _mk_chat_session(session, uid)
    token = await _login(client, "tkw")
    rc = await client.post(
        "/chat/tool-confirm",
        headers=_auth(token),
        json={"confirm_id": body["confirm_id"], "decision": "confirm", "session_id": sid},
    )
    assert rc.status_code == 200 and rc.json()["status"] == "executed", rc.text
    result = cast("dict[str, object]", rc.json()["result"])
    card = cast("dict[str, object]", result["ticket"])
    created_id = str(card["id"])
    assert created_id == "T-1005"
    assert await _mock_count(mock_ticket, mock_mod) == before + 1  # 确认后真实出站

    # 审计终态:EXECUTED + 参数摘要回填目标工单标识(spec「创建结果回填」)
    fin = (
        (
            await session.execute(
                text(
                    "SELECT status, args_digest, idempotency_key FROM public.sys_tool_audit"
                    " WHERE id=:i"
                ),
                {"i": body["confirm_id"]},
            )
        )
        .mappings()
        .one()
    )
    assert fin["status"] == "EXECUTED"
    assert f'"ticket_id": "{created_id}"' in str(fin["args_digest"])


async def test_ticket_unconfigured_zero_aware(
    client: AsyncClient,
    session: AsyncSession,
    make_user: Any,
    restore_registry: Any,
) -> None:
    """未配置端点与凭证:工具对全链不存在(与「域从未存在」不可区分),
    直调被拒并落审计(spec 场景)。"""
    registry = restore_registry
    registry.activate(registry.build_registry(None))  # 未配置 ticket(缺省基表)
    assert registry.get_tool("ticket_search") is None
    await _mk_tenant(session, "ek")
    uid = await _user_with_tools(session, make_user, "tkz", ["tool:ticket:search"])
    req = _req("ticket_search", {}, user_id=uid)
    r = await client.post("/internal/tool/execute", headers=INTERNAL, json=req)
    assert r.status_code == 200 and r.json()["status"] == "rejected", r.text
    assert "TOOL_NOT_REGISTERED" in str(r.json()["reason"])
    audit = (
        (
            await session.execute(
                text("SELECT status, domain FROM public.sys_tool_audit WHERE trace_id=:t"),
                {"t": req["trace_id"]},
            )
        )
        .mappings()
        .one()
    )
    assert audit["status"] == "REJECTED" and audit["domain"] == "ticket"


async def test_ticket_create_rejected_keeps_zero_outbound(
    client: AsyncClient,
    session: AsyncSession,
    make_user: Any,
    monkeypatch: Any,
    restore_registry: Any,
    mock_ticket: Any,
) -> None:
    """拒绝确认:不出站、工单系统无新单(spec 场景)。"""
    from tests.test_chat_query import _auth, _login

    registry = restore_registry
    registry.activate(
        registry.build_registry(
            None, ticket_base_url=mock_ticket.base_url, ticket_api_key=_TICKET_KEY
        )
    )
    _wire_ticket(monkeypatch, mock_ticket)
    await _mk_tenant(session, "ek")
    uid = await _user_with_tools(session, make_user, "tkr", ["tool:ticket:manage", "chat:execute"])
    req = _req(
        "ticket_create",
        {"title": "不建", "description": "用户会拒绝", "priority": "low"},
        user_id=uid,
    )
    r = await client.post("/internal/tool/execute", headers=INTERNAL, json=req)
    confirm_id = r.json()["confirm_id"]
    sid = await _mk_chat_session(session, uid)
    token = await _login(client, "tkr")
    rc = await client.post(
        "/chat/tool-confirm",
        headers=_auth(token),
        json={"confirm_id": confirm_id, "decision": "reject", "session_id": sid},
    )
    assert rc.status_code == 200 and rc.json()["status"] == "confirm_rejected"
    assert await _mock_count(mock_ticket, mock_mod) == 4  # 无新单


async def test_ticket_unauthorized_excluded(
    client: AsyncClient,
    session: AsyncSession,
    make_user: Any,
    monkeypatch: Any,
    restore_registry: Any,
    mock_ticket: Any,
) -> None:
    """未授权:工具不入注入集,直调被拒并落审计(spec 场景)。"""
    registry = restore_registry
    registry.activate(
        registry.build_registry(
            None, ticket_base_url=mock_ticket.base_url, ticket_api_key=_TICKET_KEY
        )
    )
    # 注入集:无 tool:ticket:search 权限点 → 三个工单工具都不可见
    codes = {"tool:sql:validate"}
    assert all(not s["name"].startswith("ticket_") for s in registry.filter_specs(codes))
    search_codes = {"tool:ticket:search"}
    names = {s["name"] for s in registry.filter_specs(search_codes)}
    assert names == {"ticket_search", "ticket_get"}  # manage 点不含在读权限点内

    _wire_ticket(monkeypatch, mock_ticket)
    await _mk_tenant(session, "ek")
    uid = await _user_with_tools(session, make_user, "tku", ["tool:sql:validate"])
    req = _req("ticket_search", {}, user_id=uid)
    r = await client.post("/internal/tool/execute", headers=INTERNAL, json=req)
    assert r.status_code == 200 and r.json()["status"] == "rejected", r.text
    assert "TOOL_PERMISSION_DENIED" in str(r.json()["reason"])
    audit = (
        (
            await session.execute(
                text("SELECT status, domain FROM public.sys_tool_audit WHERE trace_id=:t"),
                {"t": req["trace_id"]},
            )
        )
        .mappings()
        .one()
    )
    assert audit["status"] == "REJECTED" and audit["domain"] == "ticket"
    # manage 点缺失:create 被拒(读写分权)
    uid2 = await _user_with_tools(session, make_user, "tku2", ["tool:ticket:search"])
    req2 = _req(
        "ticket_create",
        {"title": "t", "description": "d", "priority": "high"},
        user_id=uid2,
    )
    r2 = await client.post("/internal/tool/execute", headers=INTERNAL, json=req2)
    assert r2.json()["status"] == "rejected"
    assert "tool:ticket:manage" in str(r2.json()["reason"])


async def test_ticket_quota_bucket_independent(
    client: AsyncClient,
    session: AsyncSession,
    make_user: Any,
    monkeypatch: Any,
    restore_registry: Any,
    mock_ticket: Any,
) -> None:
    """域配额独立分桶(spec 场景):ticket 超限只限 ticket 桶,knowledge 不受挤占。"""
    from kongming.config import get_settings
    from kongming.toolcall import router as router_mod
    from tests.test_external_knowledge import LOCAL_URL

    registry = restore_registry
    registry.activate(
        registry.build_registry(
            LOCAL_URL,  # knowledge 工具同时入表(对照桶)
            ticket_base_url=mock_ticket.base_url,
            ticket_api_key=_TICKET_KEY,
        )
    )
    monkeypatch.setattr(get_settings(), "toolgateway_ticket_quota", 1)

    class _FakeKnowledgePort:
        async def search(self, *, query: str, top_k: int) -> list[dict[str, object]]:
            return [{"source_name": "n", "text": "t", "note_path": "p", "anchor": "a"}]

    monkeypatch.setattr(router_mod, "_build_knowledge_client", lambda: _FakeKnowledgePort())
    _wire_ticket(monkeypatch, mock_ticket)
    await _mk_tenant(session, "ek")
    uid = await _user_with_tools(
        session, make_user, "tkq", ["tool:ticket:search", "tool:knowledge:search"]
    )
    req = _req("ticket_search", {}, user_id=uid)
    r1 = await client.post("/internal/tool/execute", headers=INTERNAL, json=req)
    assert r1.status_code == 200 and r1.json()["status"] == "executed", r1.text
    r2 = await client.post("/internal/tool/execute", headers=INTERNAL, json=req)
    assert r2.json()["status"] == "rate_limited"
    assert "域配额" in str(r2.json().get("reason"))
    # 其它域(knowledge)不受 ticket 桶挤占
    req_k = _req("knowledge_search", {"query": "口径"}, user_id=uid)
    r3 = await client.post("/internal/tool/execute", headers=INTERNAL, json=req_k)
    assert r3.status_code == 200 and r3.json()["status"] == "executed", r3.text


async def test_ticket_credential_never_in_log_or_audit(
    client: AsyncClient,
    session: AsyncSession,
    make_user: Any,
    monkeypatch: Any,
    restore_registry: Any,
    mock_ticket: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """凭证脱敏(websearch 同款断言形态):服务凭证不进日志、不进审计、
    不进观察与失败文案——grep 全量断言。"""
    registry = restore_registry
    registry.activate(
        registry.build_registry(
            None, ticket_base_url=mock_ticket.base_url, ticket_api_key=_TICKET_KEY
        )
    )
    _wire_ticket(monkeypatch, mock_ticket)
    await _mk_tenant(session, "ek")
    uid = await _user_with_tools(session, make_user, "tks", ["tool:ticket:search"])
    req = _req("ticket_search", {"query": "不存在的xyz"}, user_id=uid)
    with caplog.at_level(logging.DEBUG):
        r = await client.post("/internal/tool/execute", headers=INTERNAL, json=req)
    assert r.status_code == 200 and r.json()["status"] == "executed", r.text
    # 日志 grep(全级别):凭证值零出现
    log_blob = "\n".join(r.getMessage() for r in caplog.records)
    assert _TICKET_KEY not in log_blob
    # 审计 grep:参数摘要/失败文案零出现
    rows = (
        (
            await session.execute(
                text(
                    "SELECT args_digest, error_reason FROM public.sys_tool_audit WHERE trace_id=:t"
                ),
                {"t": req["trace_id"]},
            )
        )
        .mappings()
        .all()
    )
    assert rows
    for row in rows:
        assert _TICKET_KEY not in str(row["args_digest"])
        assert _TICKET_KEY not in str(row["error_reason"])
    # 响应观察零出现
    assert _TICKET_KEY not in r.text


def test_build_gateway_composition_ticket_shapes(monkeypatch: Any) -> None:
    """组合根形态:全缺=无网关;ticket-only 独立装配(declared 只认装配);
    与 websearch 共存时各自独立。"""
    from kongming.config import get_settings
    from kongming.toolcall import router as router_mod

    s = get_settings()
    monkeypatch.setattr(s, "siku_base_url", "")
    monkeypatch.setattr(s, "websearch_provider", "")
    monkeypatch.setattr(s, "websearch_api_key", "")
    monkeypatch.setattr(s, "ticket_base_url", "")
    monkeypatch.setattr(s, "ticket_api_key", "")
    router_mod._get_gateway.cache_clear()
    try:
        assert router_mod._build_gateway() is None  # 全缺=无网关(既有路径零回归)
        monkeypatch.setattr(s, "ticket_base_url", "http://127.0.0.1:8123")
        monkeypatch.setattr(s, "ticket_api_key", "k")
        gw = router_mod._build_gateway()
        assert gw is not None
        assert gw.declared("ticket")
        assert not gw.declared("websearch") and not gw.declared("knowledge")
        assert gw.quota("ticket") == s.toolgateway_ticket_quota
        # 治理面摘要含 ticket(域治理自动出现在既有域清单)
        summary = {str(d["id"]): d for d in gw.summary()}
        assert summary["ticket"]["redaction"] == "allowlist:ticket"
    finally:
        router_mod._get_gateway.cache_clear()
