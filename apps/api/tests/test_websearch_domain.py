# pyright: reportPrivateUsage=false
"""094 web-search-domain 测试:供应商注册表/域策略器/执行器适配/注册条件/
配额分桶/组合配置校验。

安全规则类测试先行(宪法 Article III):白名单拒绝(零网络请求)、脱敏
allowlist(评分与追踪参数不入观察)、注册双条件、凭证只在控制面请求头。
执行器出站用真实 TCP 存根(与 081 同哲学:被测对象自建 AsyncClient,
记录收到的请求头/报文,凭证装配可断言);失败分类定点触发(拒连=关闭端口)。
"""

import json
import logging
import socket
import time
from typing import Any, cast

import httpx
import pytest

from kongming.config import Settings
from kongming.toolgateway.models import (
    DOMAIN_TOOLS,
    WEBSEARCH_COUNT_DEFAULT,
    WEBSEARCH_PROVIDERS,
    RetryClass,
    load_manifest,
)
from kongming.toolgateway.websearch import (
    _ADAPTERS,
    WebSearchError,
    WebSearchExecutor,
    WebSearchPolicy,
)
from tests.tool_helpers import _http_response, _Stub

# ── 纯函数段:供应商注册表与域映射 ──


def test_provider_registry_single_source() -> None:
    """供应商注册表单源:端点/白名单主机成对声明;工具名→域映射收录 websearch。"""
    assert set(WEBSEARCH_PROVIDERS) == {"tavily", "bocha", "zhipu", "alibaba_ai_search"}
    tavily = WEBSEARCH_PROVIDERS["tavily"]
    assert tavily.endpoint == "https://api.tavily.com/search"
    assert tavily.host == "api.tavily.com"
    bocha = WEBSEARCH_PROVIDERS["bocha"]
    assert bocha.endpoint == "https://api.bochaai.com/v1/web-search"
    assert bocha.host == "api.bochaai.com"
    zhipu = WEBSEARCH_PROVIDERS["zhipu"]
    assert zhipu.endpoint == "https://open.bigmodel.cn/api/paas/v4/web_search"
    assert zhipu.host == "open.bigmodel.cn"
    alibaba = WEBSEARCH_PROVIDERS["alibaba_ai_search"]
    assert alibaba.endpoint == (
        "https://default-c7qq.platform-cn-shanghai.opensearch.aliyuncs.com"
        "/v3/openapi/workspaces/default/web-search/ops-web-search-001"
    )
    assert alibaba.host == "default-c7qq.platform-cn-shanghai.opensearch.aliyuncs.com"
    assert DOMAIN_TOOLS["web_search"] == "websearch"
    # 数据表与适配表同键(评审 D2:只加数据不加适配会在启动期 ValueError 拒启,
    # fail-closed 够响,但同键断言把漂移挡在测试层)
    assert set(WEBSEARCH_PROVIDERS) == set(_ADAPTERS)


def test_provider_manifest_derivation_covers_registry() -> None:
    """105:域清单端点与出口白名单由供应商单源派生(组合根 _build_gateway 同构
    入参)——注册表任一供应商自动进清单,白名单=注册主机
    精确匹配,无需逐供应商接线。"""
    for spec in WEBSEARCH_PROVIDERS.values():
        manifest, warnings = load_manifest(
            [
                {
                    "id": "websearch",
                    "endpoint": spec.endpoint,
                    "whitelist_hosts": [spec.host],
                    "timeout_ms": 3000,
                    "quota_per_minute": 60,
                    "redaction": "allowlist:websearch",
                }
            ]
        )
        assert warnings == []
        assert manifest["websearch"].endpoint == spec.endpoint
        assert manifest["websearch"].whitelist_hosts == (spec.host,)
        policy = WebSearchPolicy(whitelist_hosts=manifest["websearch"].whitelist_hosts)
        assert policy.exit_host_allowed(spec.host)
        assert not policy.exit_host_allowed("evil.example.com")


# ── 域策略器(纯函数) ──


def _policy() -> WebSearchPolicy:
    return WebSearchPolicy(whitelist_hosts=("api.tavily.com",))


def test_policy_validate_args() -> None:
    p = _policy()
    assert p.validate_args({"query": "kongming 网关"}) is None
    assert p.validate_args({}) is not None  # 缺 query
    assert p.validate_args({"query": "   "}) is not None  # 空白
    assert p.validate_args({"query": "q" * 501}) is not None  # 超长
    assert p.validate_args({"query": "q", "count": 5}) is None
    assert p.validate_args({"query": "q", "count": 0}) is not None  # 下界
    assert p.validate_args({"query": "q", "count": 11}) is not None  # 上界
    assert p.validate_args({"query": "q", "count": True}) is not None  # bool 穿透
    assert p.validate_args({"query": "q", "count": "3"}) is not None


def test_policy_exit_host_allowlist() -> None:
    assert _policy().exit_host_allowed("api.tavily.com")
    assert _policy().exit_host_allowed("API.TAVILY.COM")  # 大小写不敏感
    assert not _policy().exit_host_allowed("evil.example.com")


def test_policy_redact_allowlist_cards() -> None:
    """脱敏 allowlist:检索卡只透三字段、链接剥追踪参数,评分/白名单外字段
    永不入观察;非法形态收敛空结果,不透传供应商原文。"""
    p = _policy()
    out = p.redact(
        {
            "results": [
                {
                    "title": "Kongming",
                    "url": "https://example.com/a?utm_source=x&id=7&fbclid=1",
                    "snippet": "正文",
                    "score": 0.98,
                    "raw_content": "全文...",
                    "internal_id": "xyz",
                },
                {"title": "无链接"},  # 缺 url 卡片丢弃(与归一层同判)
                "not-a-dict",  # 非法条目丢弃
            ]
        }
    )
    assert out == {
        "results": [{"title": "Kongming", "url": "https://example.com/a?id=7", "snippet": "正文"}]
    }
    # 无追踪参数的链接原样保留(不重编码)
    plain = p.redact({"results": [{"title": "t", "url": "https://e.com/p?a=1", "snippet": "s"}]})
    assert plain == {"results": [{"title": "t", "url": "https://e.com/p?a=1", "snippet": "s"}]}
    assert p.redact(None) == {"results": []}
    assert p.redact({"results": "oops"}) == {"results": []}
    assert p.redact("raw text") == {"results": []}


def test_policy_classify_and_failure_reason() -> None:
    p = _policy()
    assert p.classify(None, delivered=True) is RetryClass.OK
    assert (
        p.classify(WebSearchError("unreachable: 联网检索拒连(ConnectError)"), delivered=False)
        is RetryClass.UNDELIVERED
    )
    assert (
        p.classify(WebSearchError("protocol: 上游 HTTP 500"), delivered=True) is RetryClass.PROTOCOL
    )
    assert p.failure_reason(RetryClass.UNDELIVERED, None).startswith("unreachable:")
    err = WebSearchError("protocol: 检索读超时(ReadTimeout)")
    assert p.failure_reason(RetryClass.PROTOCOL, err) == "protocol: 检索读超时(ReadTimeout)"


# ── 执行器:真实 TCP 存根(请求头/报文可断言) ──


class _RecordingStub:
    """TCP 存根包装:记录收到的请求行/头/体,按剧本响应。"""

    def __init__(
        self,
        respond: bytes,
        *,
        responds: list[bytes | None] | None = None,
        delay: float = 0.0,
    ) -> None:
        """responds:按连接序剧本(第 N 连接取第 N 项,越界回落最后一项;
        None=accept 后立即关连——连接层未送达的定点触发,无死端口竞态)。
        delay:响应前滞留秒数(读超时定点触发)。"""
        self.seen: list[dict[str, str]] = []
        outer = self

        def handler(conn: socket.socket, _nth: int) -> None:
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                data += chunk
            head, _, rest = data.partition(b"\r\n\r\n")
            headers = head.decode("utf-8", "replace")
            length = 0
            for line in headers.split("\r\n"):
                if line.lower().startswith("content-length:"):
                    length = int(line.split(":", 1)[1].strip() or "0")
            while len(rest) < length:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                rest += chunk
            outer.seen.append(
                {
                    "request_line": headers.split("\r\n")[0],
                    "headers": headers,
                    "body": rest.decode("utf-8", "replace"),
                }
            )
            if delay > 0:
                time.sleep(delay)
            if responds is not None:
                idx = min(_nth - 1, len(responds) - 1)
                scripted = responds[idx]
                if scripted is None:
                    return  # accept 后关连:客户端按连接层未送达归类
                conn.sendall(scripted)
            else:
                conn.sendall(respond)

        self.stub = _Stub(handler)

    @property
    def port(self) -> int:
        return self.stub.port

    def close(self) -> None:
        self.stub.close()


def _gateway_with_websearch(
    stub_port: int,
    adapter: str,
    *,
    api_key: str = "test-key",
    timeout_ms: int = 3000,
    executor: Any | None = None,
) -> Any:
    """网关装配:websearch 域端点指向存根(执行器协议适配不校验 URL,
    出口校验由策略器按清单白名单承担——与生产同构)。"""
    from kongming.toolgateway.gateway import ToolGateway

    manifest, _ = load_manifest(
        [
            {
                "id": "websearch",
                "endpoint": f"http://127.0.0.1:{stub_port}/v1/search",
                "whitelist_hosts": ["127.0.0.1"],
                "timeout_ms": timeout_ms,
                "quota_per_minute": 60,
                "redaction": "allowlist:websearch",
            }
        ]
    )
    return ToolGateway(
        manifest=manifest,
        policies={
            "websearch": WebSearchPolicy(whitelist_hosts=manifest["websearch"].whitelist_hosts)
        },
        domain_executors={
            "websearch": executor or WebSearchExecutor(adapter=adapter, api_key=api_key)
        },
    )


async def _websearch_once(gw: Any) -> dict[str, object]:
    return await gw.execute(
        tool_name="web_search",
        args={"query": "kongming 工具网关", "count": 2},
        domain="websearch",
        trace_id="tr-test",
        tenant_id="t",
        user_id="u",
    )


@pytest.mark.asyncio
async def test_executor_tavily_adaptation() -> None:
    """tavily 适配:Bearer 头装配、方言参数(max_results)、响应归一为检索卡
    (content→snippet;缺 url 卡片丢弃;评分不入观察——redact allowlist)。"""
    body = json.dumps(
        {
            "results": [
                {"title": "T1", "url": "https://a.example/1", "content": "摘要一", "score": 0.9},
                {"title": "T2", "url": "https://a.example/2", "content": "摘要二"},
                {"title": "无链接", "content": "丢弃"},
            ]
        }
    )
    stub = _RecordingStub(_http_response(body))
    try:
        gw = _gateway_with_websearch(stub.port, "tavily")
        out = await _websearch_once(gw)
    finally:
        stub.close()
    assert out["status"] == "executed", out
    result = cast("dict[str, object]", out["result"])
    cards = cast("list[dict[str, object]]", result["results"])
    assert cards == [
        {"title": "T1", "url": "https://a.example/1", "snippet": "摘要一"},
        {"title": "T2", "url": "https://a.example/2", "snippet": "摘要二"},
    ]
    req = stub.seen[0]
    assert "Authorization: Bearer test-key" in req["headers"]  # 凭证只进请求头
    sent = json.loads(req["body"])
    assert sent == {"query": "kongming 工具网关", "max_results": 2}  # 方言参数


@pytest.mark.asyncio
async def test_executor_bocha_adaptation() -> None:
    """bocha 适配:count/summary 方言参数,data.webPages.value 归一
    (name→title,summary 优先、snippet 回退)。"""
    body = json.dumps(
        {
            "code": 200,
            "data": {
                "webPages": {
                    "value": [
                        {"name": "B1", "url": "https://b.example/1", "summary": "摘要一"},
                        {"name": "B2", "url": "https://b.example/2", "snippet": "回退摘要"},
                    ]
                }
            },
        }
    )
    stub = _RecordingStub(_http_response(body))
    try:
        gw = _gateway_with_websearch(stub.port, "bocha")
        out = await _websearch_once(gw)
    finally:
        stub.close()
    assert out["status"] == "executed", out
    result = cast("dict[str, object]", out["result"])
    cards = cast("list[dict[str, object]]", result["results"])
    assert cards == [
        {"title": "B1", "url": "https://b.example/1", "snippet": "摘要一"},
        {"title": "B2", "url": "https://b.example/2", "snippet": "回退摘要"},
    ]
    sent = json.loads(stub.seen[0]["body"])
    assert sent["count"] == 2 and sent["summary"] is True


@pytest.mark.asyncio
async def test_executor_zhipu_adaptation() -> None:
    """zhipu 适配:官方必填方言字段(search_engine/search_intent)、search_query
    70 字符硬限截断、search_result[].{title,link,content} 归一为检索卡。"""
    body = json.dumps(
        {
            "id": "req-1",
            "search_result": [
                {"title": "Z1", "link": "https://z.example/1", "content": "摘要一", "media": "M"},
                {"title": "Z2", "link": "https://z.example/2", "content": "摘要二", "icon": "i"},
                {"title": "无链接", "content": "丢弃"},
            ],
        }
    )
    stub = _RecordingStub(_http_response(body))
    try:
        gw = _gateway_with_websearch(stub.port, "zhipu")
        out = await gw.execute(
            tool_name="web_search",
            args={"query": "q" * 100, "count": 2},  # 超方言上限:装配层截断
            domain="websearch",
            trace_id="tr-test",
            tenant_id="t",
            user_id="u",
        )
    finally:
        stub.close()
    assert out["status"] == "executed", out
    result = cast("dict[str, object]", out["result"])
    cards = cast("list[dict[str, object]]", result["results"])
    assert cards == [
        {"title": "Z1", "url": "https://z.example/1", "snippet": "摘要一"},
        {"title": "Z2", "url": "https://z.example/2", "snippet": "摘要二"},
    ]
    sent = json.loads(stub.seen[0]["body"])
    assert sent["search_engine"] == "search_std" and sent["search_intent"] is False
    assert sent["search_query"] == "q" * 70  # 方言硬限截断(域契约 500 不动)
    assert sent["count"] == 2


@pytest.mark.asyncio
async def test_executor_zhipu_malformed_no_retry() -> None:
    """zhipu 缺 search_result 列表:报文畸形按 protocol 收口不重试。"""
    stub = _RecordingStub(_http_response(json.dumps({"id": "req-1"})))
    try:
        gw = _gateway_with_websearch(stub.port, "zhipu")
        out = await _websearch_once(gw)
    finally:
        stub.close()
    assert out["status"] == "error"
    assert str(out["reason"]).startswith("protocol: 检索响应缺 search_result 列表")
    assert stub.stub.connections == 1


# 官方「联网搜索API参考」响应示例剪裁(105 契约桩夹具,2026-10-11 核对):
# result.search_result[].{title,link,snippet,content} + position/meta_info 附加字段
_ALIBABA_SAMPLE_BODY = {
    "request_id": "5B9F5A24-8F1D-****-B7E2-3C6A1D9E4F02",
    "latency": 2.3,
    "usage": {"ops-web-search-001/pro": 1},
    "result": {
        "search_result": [
            {
                "title": "杭州气象微博",
                "link": "https://www.hzqx.cn/pc/hztq/",
                "snippet": "较昨日同期 持平 相对湿度:-% 风力:- 实时数据、未经人工复核",
                "position": None,
                "meta_info": {"publishedTime": "2026-08-26T10:55:00+08:00"},
                "content": "杭州气象微博\n较昨日同期 持平\n相对湿度: -- % 风力: --",
            },
            {
                "title": "杭州7天天气",
                "link": "https://www.ip.cn/tianqi/zhejiang/hangzhou/7day.html",
                "snippet": "杭州天气:2026-08-20至2026-08-26 天气预报 中雨转多云 25℃～31℃",
            },
            {"title": "无链接", "snippet": "缺 link 卡片应丢弃"},
        ]
    },
}


@pytest.mark.asyncio
async def test_executor_alibaba_adaptation() -> None:
    """alibaba 适配:OS- 独立 key 的 Bearer 头、方言参数(top_k/content_type=snippet)、
    result.search_result[].{link→url,snippet} 归一为检索卡(缺 link 卡丢弃,
    position/meta_info/usage 附加字段不入观察——redact allowlist)。"""
    stub = _RecordingStub(_http_response(json.dumps(_ALIBABA_SAMPLE_BODY)))
    try:
        gw = _gateway_with_websearch(stub.port, "alibaba_ai_search", api_key="OS-test-key")
        out = await _websearch_once(gw)
    finally:
        stub.close()
    assert out["status"] == "executed", out
    result = cast("dict[str, object]", out["result"])
    cards = cast("list[dict[str, object]]", result["results"])
    assert cards == [
        {
            "title": "杭州气象微博",
            "url": "https://www.hzqx.cn/pc/hztq/",
            "snippet": "较昨日同期 持平 相对湿度:-% 风力:- 实时数据、未经人工复核",
        },
        {
            "title": "杭州7天天气",
            "url": "https://www.ip.cn/tianqi/zhejiang/hangzhou/7day.html",
            "snippet": "杭州天气:2026-08-20至2026-08-26 天气预报 中雨转多云 25℃～31℃",
        },
    ]
    req = stub.seen[0]
    assert "Authorization: Bearer OS-test-key" in req["headers"]  # OS- 独立 key 形态
    sent = json.loads(req["body"])
    assert sent == {"query": "kongming 工具网关", "top_k": 2, "content_type": "snippet"}


@pytest.mark.asyncio
async def test_executor_alibaba_malformed_no_retry() -> None:
    """alibaba 缺 result.search_result 列表:报文畸形显式按 protocol 收口不重试
    (不静默吐空)。"""
    stub = _RecordingStub(_http_response(json.dumps({"request_id": "req-1", "latency": 2.0})))
    try:
        gw = _gateway_with_websearch(stub.port, "alibaba_ai_search", api_key="OS-test-key")
        out = await _websearch_once(gw)
    finally:
        stub.close()
    assert out["status"] == "error"
    assert str(out["reason"]).startswith("protocol: 检索响应缺 search_result 列表")
    assert stub.stub.connections == 1


@pytest.mark.asyncio
async def test_executor_alibaba_empty_result_honest() -> None:
    """alibaba 空结果如实表达:search_result 空表 → executed + 空列表
    (分发层按列表长度装配 count,空表即计数 0,不编造命中)。"""
    body = json.dumps(
        {
            "request_id": "req-1",
            "usage": {"ops-web-search-001/pro": 1},
            "result": {"search_result": []},
        }
    )
    stub = _RecordingStub(_http_response(body))
    try:
        gw = _gateway_with_websearch(stub.port, "alibaba_ai_search", api_key="OS-test-key")
        out = await _websearch_once(gw)
    finally:
        stub.close()
    assert out["status"] == "executed", out
    result = cast("dict[str, object]", out["result"])
    assert result["results"] == []


@pytest.mark.asyncio
async def test_executor_protocol_failures_no_retry() -> None:
    """响应类失败(HTTP 500/报文畸形)不重试:单连接即收口。"""
    stub = _RecordingStub(_http_response("boom", status=500))
    try:
        gw = _gateway_with_websearch(stub.port, "tavily")
        out = await _websearch_once(gw)
    finally:
        stub.close()
    assert out["status"] == "error"
    assert str(out["reason"]).startswith("protocol: 上游 HTTP 500")
    assert stub.stub.connections == 1  # 响应类不重试

    stub2 = _RecordingStub(_http_response("not-json{{"))
    try:
        gw2 = _gateway_with_websearch(stub2.port, "tavily")
        out2 = await _websearch_once(gw2)
    finally:
        stub2.close()
    assert out2["status"] == "error"
    assert str(out2["reason"]).startswith("protocol:")
    assert stub2.stub.connections == 1


@pytest.mark.asyncio
async def test_executor_undelivered_retries_once() -> None:
    """连接层未送达(accept 后关连)重试恰好一次:第二次尝试成功交付。"""
    ok = _http_response(
        json.dumps({"results": [{"title": "T", "url": "https://ok.example/1", "content": "S"}]})
    )
    stub = _RecordingStub(ok, responds=[None, ok])  # 第 1 连接关连,第 2 连接起正常
    try:
        gw = _gateway_with_websearch(stub.port, "tavily")
        out = await _websearch_once(gw)
    finally:
        stub.close()
    assert out["status"] == "executed", out  # 重试恰好一次后交付
    assert stub.stub.connections == 2  # 未送达重试至多一次的实证计数


@pytest.mark.asyncio
async def test_executor_undelivered_exhausted_unreachable() -> None:
    """两次尝试均未送达:按 unreachable 收口(不再第三次尝试)。"""
    stub = _RecordingStub(b"x", responds=[None])  # 全部连接 accept 后关连
    try:
        gw = _gateway_with_websearch(stub.port, "tavily")
        out = await _websearch_once(gw)
    finally:
        stub.close()
    assert out["status"] == "error"
    assert str(out["reason"]).startswith("unreachable:")
    assert stub.stub.connections == 2


@pytest.mark.asyncio
async def test_executor_read_timeout_protocol_no_retry() -> None:
    """读超时=请求已送达:响应类不重试(慢存根定点触发)。"""
    body = _http_response(json.dumps({"results": []}))
    stub = _RecordingStub(body, delay=1.0)  # 响应滞留 1s > 超时 0.3s
    try:
        gw = _gateway_with_websearch(stub.port, "tavily", timeout_ms=300)
        out = await _websearch_once(gw)
    finally:
        stub.close()
    assert out["status"] == "error"
    assert str(out["reason"]).startswith("protocol: 检索读超时")
    assert stub.stub.connections == 1  # 响应类不重试


def _decoding_broken(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, headers={"Content-Encoding": "gzip"}, content=b"not-gzip-bytes")


@pytest.mark.asyncio
async def test_executor_decoding_error_protocol_no_retry() -> None:
    """评审 R1 回归:解码失败(DecodingError∈RequestError,非 TransportError)
    须按 protocol 前缀收口,不裸抛 httpx 异常(否则审计丢结构化分类)、不重试。"""

    executor = WebSearchExecutor(
        adapter="tavily", api_key="k", transport=httpx.MockTransport(_decoding_broken)
    )
    gw = _gateway_with_websearch(0, "tavily", executor=executor)
    out = await _websearch_once(gw)
    assert out["status"] == "error"
    assert str(out["reason"]).startswith("protocol: 响应解码失败(DecodingError)")


def test_executor_unknown_adapter_rejected() -> None:
    """未知供应商适配器:组合根装配即拒(装配缺陷显式化,不静默)。"""
    with pytest.raises(ValueError, match="未知供应商适配器"):
        WebSearchExecutor(adapter="bogus", api_key="k")


# ── 网关按域执行器路由与回落 ──


@pytest.mark.asyncio
async def test_gateway_domain_executor_routing_and_fallback() -> None:
    """按域执行器路由:声明专属执行器的域走专属;未声明的域回落通道级
    执行器(既有域零回归);执行器缺失的域拒绝(与「域从未存在」同形)。"""
    from kongming.toolgateway.gateway import ToolGateway
    from kongming.toolgateway.models import RetryClass

    class _DemoPolicy:
        """demo 域测试策略器(框架回落路径的对照域;纯桩)。"""

        def exit_host_allowed(self, host: str) -> bool:
            return host in ("127.0.0.1", "localhost")

        def validate_args(self, args: dict[str, object]) -> str | None:
            return None

        def redact(self, response: object) -> object:
            return response

        def classify(self, exc: Exception | None, *, delivered: bool) -> RetryClass:
            return RetryClass.OK if exc is None else RetryClass.PROTOCOL

    manifest, _ = load_manifest(
        [
            {
                "id": "demo",
                "endpoint": "http://127.0.0.1:9901/api/search",
                "whitelist_hosts": ["127.0.0.1"],
                "timeout_ms": 1000,
                "quota_per_minute": 60,
                "redaction": "allowlist:demo",
            },
            {
                "id": "websearch",
                "endpoint": "http://127.0.0.1:9902/v1/search",
                "whitelist_hosts": ["127.0.0.1"],
                "timeout_ms": 1000,
                "quota_per_minute": 60,
                "redaction": "allowlist:websearch",
            },
        ]
    )

    class _Spy:
        def __init__(self) -> None:
            self.urls: list[str] = []

        async def __call__(
            self, *, url: str, payload: dict[str, object], timeout_s: float
        ) -> object:
            self.urls.append(url)
            return {"results": []}

    default_spy, ws_spy = _Spy(), _Spy()
    gw = ToolGateway(
        manifest=manifest,
        policies={
            "demo": _DemoPolicy(),
            "websearch": WebSearchPolicy(whitelist_hosts=("127.0.0.1",)),
        },
        executor=default_spy,
        domain_executors={"websearch": ws_spy},
    )
    out_ws = await gw.execute(
        tool_name="web_search",
        args={"query": "q"},
        domain="websearch",
        trace_id="t",
        tenant_id="t",
        user_id="u",
    )
    out_k = await gw.execute(
        tool_name="demo_tool",
        args={"query": "q"},
        domain="demo",
        trace_id="t",
        tenant_id="t",
        user_id="u",
    )
    assert out_ws["status"] == "executed" and out_k["status"] == "executed"
    assert ws_spy.urls == ["http://127.0.0.1:9902/v1/search"]
    assert default_spy.urls == ["http://127.0.0.1:9901/api/search"]  # 回落生效
    # 执行器缺失的域拒绝(域在清单/有策略器但无执行器)
    gw_bare = ToolGateway(
        manifest=manifest,
        policies={
            "demo": _DemoPolicy(),
            "websearch": WebSearchPolicy(whitelist_hosts=("127.0.0.1",)),
        },
    )
    out_none = await gw_bare.execute(
        tool_name="web_search",
        args={"query": "q"},
        domain="websearch",
        trace_id="t",
        tenant_id="t",
        user_id="u",
    )
    assert out_none == {"status": "rejected", "reason": "policy: 域 websearch 不可用"}


# ── 注册条件(部署级,纯函数) ──


def test_build_registry_websearch_conditions() -> None:
    from kongming.toolcall.registry import REGISTRY, build_registry

    base = build_registry()
    assert "web_search" not in base  # 缺省=静态基表,零回归
    assert set(base) == set(REGISTRY)

    gated = build_registry(websearch_provider="tavily", websearch_api_key="tvly-k")
    assert "web_search" in gated
    assert gated["web_search"].perm_code == "tool:websearch:search"
    assert gated["web_search"].write is False
    assert gated["web_search"].require_confirm is False

    # 双条件缺一不注册;供应商值大小写归一
    assert "web_search" not in build_registry(websearch_provider="tavily", websearch_api_key=" ")
    assert "web_search" not in build_registry(websearch_provider="bogus", websearch_api_key="k")
    assert "web_search" in build_registry(websearch_provider="TAVILY", websearch_api_key="k")


# ── 组合配置校验(prod 拒启 / dev 告警降级) ──

_PROD_KW: dict[str, Any] = {
    "environment": "prod",
    "jwt_private_key_pem": "k",
    "jwt_public_key_pem": "k",
    "internal_token": "k",
    "datasource_encrypt_key": "k" * 32,
    "billing_mock_enabled": False,
}


def test_websearch_misconfiguration_matrix() -> None:
    # 均未配置=合法缺省(零感知;显式空串隔离本地 .env 的 KM_WEBSEARCH_*)
    bare = Settings(environment="dev", websearch_provider="", websearch_api_key="")
    assert bare.websearch_misconfiguration() is None
    # 齐备=合法
    ok = Settings(websearch_provider="tavily", websearch_api_key="k")
    assert ok.websearch_misconfiguration() is None
    # 只配供应商缺 key=残缺
    missing = Settings(websearch_provider="tavily", websearch_api_key="")
    mis = missing.websearch_misconfiguration()
    assert mis is not None and "KM_WEBSEARCH_API_KEY" in mis
    # 非法供应商值=残缺
    bogus = Settings(websearch_provider="google", websearch_api_key="k")
    mis2 = bogus.websearch_misconfiguration()
    assert mis2 is not None and "非法值" in mis2
    # 只配 key 缺供应商=残缺(评审 D3:排障「配了 key 却没生效」不能无信号)
    keyonly = Settings(websearch_provider="", websearch_api_key="k")
    mis3 = keyonly.websearch_misconfiguration()
    assert mis3 is not None and "KM_WEBSEARCH_PROVIDER" in mis3


def test_prod_failclosed_websearch_misconfig() -> None:
    s = Settings(
        **cast(
            "dict[str, Any]",
            {**_PROD_KW, "websearch_provider": "tavily", "websearch_api_key": ""},
        )
    )
    with pytest.raises(RuntimeError, match="KM_WEBSEARCH_API_KEY"):
        s.fail_closed_checks()
    s2 = Settings(**cast("dict[str, Any]", {**_PROD_KW, "websearch_provider": "bogus"}))
    with pytest.raises(RuntimeError, match="非法值"):
        s2.fail_closed_checks()
    s3 = Settings(
        **cast(
            "dict[str, Any]",
            {**_PROD_KW, "websearch_provider": "", "websearch_api_key": "k"},
        )
    )
    with pytest.raises(RuntimeError, match="KM_WEBSEARCH_PROVIDER"):
        s3.fail_closed_checks()


# ── 路由级:websearch 域配额独立分桶(集成段,真实 PG+Redis) ──


class _FakeWebSearchPort:
    """WebSearchPort 假件:检索卡剧本化(缺省 1 卡,可注入空结果)。"""

    def __init__(self, cards: list[dict[str, object]] | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self._cards: list[dict[str, object]] = (
            [{"title": "T", "url": "https://example.com/x", "snippet": "S"}]
            if cards is None
            else cards
        )

    async def search(self, *, query: str, count: int) -> list[dict[str, object]]:
        self.calls.append({"query": query, "count": count})
        return self._cards


@pytest.fixture
def restore_registry() -> Any:
    """激活态还原(与 test_toolgateway 同款,防模块全局污染)。"""
    from kongming.toolcall import registry

    saved = registry.effective()
    try:
        yield registry
    finally:
        registry.set_runtime_disabled(frozenset())
        registry.activate(saved)


@pytest.mark.asyncio
async def test_websearch_domain_quota_limited(
    client: Any,
    session: Any,
    make_user: Any,
    monkeypatch: Any,
    restore_registry: Any,
) -> None:
    """websearch 域配额第二维度:窗口内第二次调用即 RATE_LIMITED(域配额
    文案),不触达执行器;观察形态 = count+检索卡。"""
    from kongming.config import get_settings
    from kongming.toolcall import router as router_mod
    from tests.tool_helpers import _mk_tenant, _req, _user_with_tools

    registry = restore_registry
    registry.activate(registry.build_registry(websearch_provider="tavily", websearch_api_key="k"))
    monkeypatch.setattr(get_settings(), "toolgateway_websearch_quota", 1)
    ws_fake = _FakeWebSearchPort()
    monkeypatch.setattr(router_mod, "_build_websearch_client", lambda: ws_fake)
    await _mk_tenant(session, "ek")
    uid = await _user_with_tools(session, make_user, "wsq", ["tool:websearch:search"])
    headers = {"X-Internal-Token": "test-internal-token"}
    req = _req("web_search", {"query": "kongming 网关"}, user_id=uid, tenant_key="ek")
    r1 = await client.post("/internal/tool/execute", headers=headers, json=req)
    assert r1.status_code == 200 and r1.json()["status"] == "executed", r1.text
    assert ws_fake.calls[0]["count"] == WEBSEARCH_COUNT_DEFAULT  # 缺省 5(单源常量)
    assert r1.json()["result"] == {
        "count": 1,
        "results": [{"title": "T", "url": "https://example.com/x", "snippet": "S"}],
    }
    r2 = await client.post("/internal/tool/execute", headers=headers, json=req)
    assert r2.json()["status"] == "rate_limited"
    assert "域配额" in str(r2.json().get("reason"))


@pytest.mark.asyncio
async def test_websearch_unregistered_zero_aware(
    client: Any,
    session: Any,
    make_user: Any,
    restore_registry: Any,
) -> None:
    """未配置供应商:工具不存在(与「域从未存在」不可区分),直调被拒落审计。"""
    from tests.tool_helpers import _mk_tenant, _req, _user_with_tools

    registry = restore_registry
    registry.activate(registry.build_registry())
    assert registry.get_tool("web_search") is None
    await _mk_tenant(session, "ek")
    uid = await _user_with_tools(session, make_user, "wsz", ["tool:websearch:search"])
    headers = {"X-Internal-Token": "test-internal-token"}
    req = _req("web_search", {"query": "q"}, user_id=uid, tenant_key="ek")
    r = await client.post("/internal/tool/execute", headers=headers, json=req)
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"
    assert "TOOL_NOT_REGISTERED" in str(r.json().get("reason"))


# ── 审查补强:端口失败映射 + 组合根形态 ──


@pytest.mark.asyncio
async def test_websearch_client_failure_mapping() -> None:
    """端口适配层:网关非 executed(拒绝/错误/限流)一律收敛 WebSearchError
    (分发兜底 ERROR+审计,不裸崩)。"""
    from kongming.toolcall.router import _GatewayWebSearchClient

    class _RejectingGateway:
        async def execute(self, **kw: object) -> dict[str, object]:
            return {"status": "rejected", "reason": "policy: 域 websearch 不可用"}

    with pytest.raises(WebSearchError, match="policy"):
        await _GatewayWebSearchClient(cast("Any", _RejectingGateway())).search(query="q", count=5)


def test_build_gateway_composition_shapes(monkeypatch: Any) -> None:
    """组合根形态:全缺=无网关;websearch-only 独立装配(declared 只认装配,
    不随停用翻转——端口绑定与治理态解耦)。"""
    from kongming.config import get_settings
    from kongming.toolcall import router as router_mod

    s = get_settings()
    monkeypatch.setattr(s, "websearch_provider", "")
    monkeypatch.setattr(s, "websearch_api_key", "")
    router_mod._get_gateway.cache_clear()
    try:
        assert router_mod._build_gateway() is None  # 全缺=无网关(既有路径零回归)
        # websearch-only
        monkeypatch.setattr(s, "websearch_provider", "tavily")
        monkeypatch.setattr(s, "websearch_api_key", "k")
        gw = router_mod._build_gateway()
        assert gw is not None
        assert gw.declared("websearch")
    finally:
        router_mod._get_gateway.cache_clear()


# ── 评审回灌:dev 告警降级接线 + 空结果如实表达 ──


@pytest.fixture
def _restore_main_logger() -> Any:  # pyright: ignore[reportUnusedFunction] —— fixture 经参数名引用
    """会话级 alembic upgrade 的 fileConfig(disable_existing_loggers)会把收集期
    已创建的 kongming.main logger 置 disabled;本用例断言 main 告警,先复启再还原。"""
    lg = logging.getLogger("kongming.main")
    orig = lg.disabled
    lg.disabled = False
    yield
    lg.disabled = orig


def test_lifespan_websearch_misconfig_warns_and_degrades(
    monkeypatch: Any,
    caplog: pytest.LogCaptureFixture,
    _restore_main_logger: Any,
) -> None:
    """dev 告警降级(spec SHALL):组合残缺 → kongming.main 显式告警,且装配
    条件与告警同源(域不注册);合法缺省 → 静默零感知。"""
    from kongming.config import get_settings
    from kongming.main import _warn_websearch_misconfiguration
    from kongming.toolcall import registry

    s = get_settings()
    monkeypatch.setattr(s, "websearch_provider", "tavily")
    monkeypatch.setattr(s, "websearch_api_key", "")
    with caplog.at_level(logging.WARNING, logger="kongming.main"):
        _warn_websearch_misconfiguration()
    assert any(
        "websearch" in r.getMessage() and "KM_WEBSEARCH_API_KEY" in r.getMessage()
        for r in caplog.records
    ), [r.getMessage() for r in caplog.records]
    # 降级=域不注册(lifespan 装配走同一判定)
    built = registry.build_registry(
        websearch_provider=s.websearch_provider,
        websearch_api_key=s.websearch_api_key,
    )
    assert "web_search" not in built
    # 合法缺省:静默
    monkeypatch.setattr(s, "websearch_provider", "")
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="kongming.main"):
        _warn_websearch_misconfiguration()
    assert not caplog.records


@pytest.mark.asyncio
async def test_websearch_empty_result_honest_observation(
    client: Any,
    session: Any,
    make_user: Any,
    monkeypatch: Any,
    restore_registry: Any,
) -> None:
    """空结果如实表达(spec SHALL):观察 = count=0 + 空表,不编造命中。"""
    from kongming.toolcall import router as router_mod
    from tests.tool_helpers import _mk_tenant, _req, _user_with_tools

    registry = restore_registry
    registry.activate(registry.build_registry(websearch_provider="tavily", websearch_api_key="k"))
    ws_fake = _FakeWebSearchPort(cards=[])
    monkeypatch.setattr(router_mod, "_build_websearch_client", lambda: ws_fake)
    await _mk_tenant(session, "ek")
    uid = await _user_with_tools(session, make_user, "wse", ["tool:websearch:search"])
    headers = {"X-Internal-Token": "test-internal-token"}
    req = _req("web_search", {"query": "无结果问句"}, user_id=uid, tenant_key="ek")
    r = await client.post("/internal/tool/execute", headers=headers, json=req)
    assert r.status_code == 200 and r.json()["status"] == "executed", r.text
    assert r.json()["result"] == {"count": 0, "results": []}
