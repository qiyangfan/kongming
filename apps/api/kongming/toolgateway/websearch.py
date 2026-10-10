"""websearch 域策略器 + 执行器(094:联网检索,089 框架第二域)。

策略器(纯函数,安全资产):出口校验(供应商 API 主机白名单)/参数校验/
响应脱敏(allowlist:检索卡只透 title/url/snippet,评分与追踪参数永不入
观察)/重试分类(089 两档语义:连接层未送达可重试一次;读超时/非 2xx/
报文畸形=已送达不重试)。
执行器:单次出站的供应商协议适配(Bearer 鉴权头装配 + 响应归一为统一
检索卡),不含治理逻辑——重试与脱敏由网关切面统一承担。
"""

from collections.abc import Callable
from typing import cast
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from kongming.toolgateway.models import (
    FAILURE_PROTOCOL_PREFIX,
    FAILURE_UNDELIVERED_PREFIX,
    WEBSEARCH_COUNT_MAX,
    WEBSEARCH_COUNT_MIN,
    WEBSEARCH_QUERY_MAX_LEN,
    RetryClass,
)

# 观察白名单(脱敏策略 allowlist:websearch)——检索卡三字段
_CARD_FIELDS = ("title", "url", "snippet")

# 常见链接追踪参数(脱敏一部分:剥离后链接才可进 LLM 上下文与答案引用;
# 非追踪参数原样保留)
_TRACKING_PARAMS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "fbclid",
        "gclid",
        "igshid",
        "msclkid",
        "spm",
        "scm",
        "ttclid",
        "yclid",
        "mc_cid",
        "mc_eid",
        "twclid",
        "ref_src",
        "li_fat_id",
        "_hsenc",
        "_hsmi",
    }
)

# 域失败文案前缀单源在 models(与 gateway._was_delivered 共引,防三处漂移)
_UNDELIVERED_PREFIX = FAILURE_UNDELIVERED_PREFIX
_PROTOCOL_PREFIX = FAILURE_PROTOCOL_PREFIX


def _strip_tracking(url: str) -> str:
    """剥离链接追踪参数(畸形 URL 原样返回;无追踪参数原样返回不重编码)。

    已知边界:剔除发生时保留参数经 urlencode 重编码(编码形态可能与原 URL
    有细微差异,解码语义等价);分号分隔的 legacy query 按 & 语义解析,同段
    非追踪参数可能连带丢弃——非规范输入,接受该近似。"""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if not parts.query:
        return url
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    kept = [(k, v) for k, v in pairs if k.lower() not in _TRACKING_PARAMS]
    if len(kept) == len(pairs):
        return url
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), parts.fragment))


class WebSearchError(Exception):
    """websearch 域执行失败的结构化收敛(分发兜底 ERROR + 审计)。"""


class WebSearchPolicy:
    """websearch 域策略器(纯函数,零外部状态)。"""

    def __init__(self, *, whitelist_hosts: tuple[str, ...]) -> None:
        self._hosts = frozenset(whitelist_hosts)

    def exit_host_allowed(self, host: str) -> bool:
        return host.lower() in self._hosts

    def validate_args(self, args: dict[str, object]) -> str | None:
        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            return "policy: query 必须为非空字符串"
        if len(query) > WEBSEARCH_QUERY_MAX_LEN:
            return f"policy: query 超长(上限 {WEBSEARCH_QUERY_MAX_LEN} 字符)"
        count = args.get("count")
        if count is None:
            return None
        if (
            not isinstance(count, int)
            or isinstance(count, bool)
            or not WEBSEARCH_COUNT_MIN <= count <= WEBSEARCH_COUNT_MAX
        ):
            return f"policy: count 必须为 {WEBSEARCH_COUNT_MIN}-{WEBSEARCH_COUNT_MAX} 的整数"
        return None

    def redact(self, response: object) -> object:
        """allowlist 脱敏:命中卡片只保留观察白名单字段(先于观察构建),
        链接剥离追踪参数;非法形态一律收敛空结果,不透传供应商原文。"""
        body = cast("dict[str, object] | None", response if isinstance(response, dict) else None)
        raw = body.get("results") if body is not None else None
        if not isinstance(raw, list):
            return {"results": []}
        cards: list[dict[str, object]] = []
        for h in cast("list[object]", raw):
            if isinstance(h, dict):
                hd = cast("dict[str, object]", h)
                url = hd.get("url")
                if not isinstance(url, str) or not url:
                    continue  # 无链接卡片对 LLM 无用,丢弃不透传(与归一层同判)
                card = {k: hd.get(k) for k in _CARD_FIELDS}
                card["url"] = _strip_tracking(url)
                cards.append(card)
        return {"results": cards}

    def classify(self, exc: Exception | None, *, delivered: bool) -> RetryClass:
        """按执行器收敛出的前缀判定(089 两档语义):
        unreachable=连接层未送达(重试至多一次);protocol=已送达不重试。"""
        if exc is None:
            return RetryClass.OK
        text = str(exc)
        if text.startswith(_UNDELIVERED_PREFIX):
            return RetryClass.UNDELIVERED
        return RetryClass.PROTOCOL

    def failure_reason(self, verdict: RetryClass, exc: Exception | None) -> str:
        """失败文案(审计 error_reason;与执行器收敛文案同口径)。"""
        if exc is None:
            return f"{_UNDELIVERED_PREFIX} 联网检索拒连(Unknown)"
        return str(exc)


def _as_dict(value: object) -> dict[str, object] | None:
    return cast("dict[str, object]", value) if isinstance(value, dict) else None


def _normalize_tavily(body: dict[str, object]) -> list[dict[str, object]]:
    """tavily 响应归一:results[].{title,url,content} → 检索卡(content 即摘要)。"""
    raw = body.get("results")
    if not isinstance(raw, list):
        raise WebSearchError(f"{_PROTOCOL_PREFIX} 检索响应缺 results 列表")
    cards: list[dict[str, object]] = []
    for item in cast("list[object]", raw):
        d = _as_dict(item)
        if d is None:
            continue
        url = d.get("url")
        if not isinstance(url, str) or not url:
            continue  # 无链接卡片对 LLM 无用,丢弃不透传
        cards.append(
            {
                "title": d.get("title") if isinstance(d.get("title"), str) else "",
                "url": url,
                "snippet": d.get("content") if isinstance(d.get("content"), str) else "",
            }
        )
    return cards


def _normalize_bocha(body: dict[str, object]) -> list[dict[str, object]]:
    """bocha 响应归一:data.webPages.value[].{name,url,summary|snippet} → 检索卡。"""
    data = _as_dict(body.get("data"))
    pages = _as_dict(data.get("webPages")) if data is not None else None
    raw = pages.get("value") if pages is not None else None
    if not isinstance(raw, list):
        raise WebSearchError(f"{_PROTOCOL_PREFIX} 检索响应缺 webPages 列表")
    cards: list[dict[str, object]] = []
    for item in cast("list[object]", raw):
        d = _as_dict(item)
        if d is None:
            continue
        url = d.get("url")
        if not isinstance(url, str) or not url:
            continue
        snippet = d.get("summary") if isinstance(d.get("summary"), str) else ""
        if not snippet and isinstance(d.get("snippet"), str):
            snippet = d.get("snippet") or ""
        title = d.get("name") if isinstance(d.get("name"), str) else ""
        cards.append({"title": title, "url": url, "snippet": snippet})
    return cards


def _tavily_request(payload: dict[str, object]) -> dict[str, object]:
    query = payload.get("query")
    count = payload.get("count")
    body: dict[str, object] = {"query": query}
    if isinstance(count, int) and not isinstance(count, bool):
        body["max_results"] = count
    return body


def _bocha_request(payload: dict[str, object]) -> dict[str, object]:
    query = payload.get("query")
    count = payload.get("count")
    body: dict[str, object] = {"query": query, "summary": True}
    if isinstance(count, int) and not isinstance(count, bool):
        body["count"] = count
    return body


# 供应商适配注册表(新供应商=增一条映射,design D1):
# 请求装配(统一参数→供应商方言)+ 响应归一(供应商报文→统一检索卡)
_Adapter = tuple[
    Callable[[dict[str, object]], dict[str, object]],
    Callable[[dict[str, object]], list[dict[str, object]]],
]
_ADAPTERS: dict[str, _Adapter] = {
    "tavily": (_tavily_request, _normalize_tavily),
    "bocha": (_bocha_request, _normalize_bocha),
}


class WebSearchExecutor:
    """websearch 域执行器:单次供应商协议适配(POST + Bearer 头);零治理。

    失败在执行器内收敛为 WebSearchError(unreachable:/protocol: 前缀),
    重试由网关切面按策略器分类承担——执行器自身不做第二次尝试。
    凭证只在构造期注入(组合根装配),只进请求头,不进观察与审计参数。
    """

    def __init__(
        self,
        *,
        adapter: str,
        api_key: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        # transport 仅测试注入(与 knowledge 域同构的注入缝;生产 None,
        # 本域套件经真实 TCP 存根覆盖)
        self._transport = transport
        self._api_key = api_key
        if adapter not in _ADAPTERS:  # 未知适配器组合根即拒(装配缺陷显式化)
            raise ValueError(f"websearch 未知供应商适配器:{adapter}")
        self._request, self._normalize = _ADAPTERS[adapter]

    async def __call__(self, *, url: str, payload: dict[str, object], timeout_s: float) -> object:
        """url/payload/timeout 由网关切面给定(域清单口径);执行器只发一次。"""
        # 未完整取得响应族(拒连在 macOS anyio 后端以 ReadError 浮现,按语义归纳)
        undelivered = (
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.RemoteProtocolError,
            httpx.ReadError,
            httpx.WriteError,
            httpx.CloseError,
        )
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(timeout_s), transport=self._transport
            ) as client:
                resp = await client.post(
                    url,
                    json=self._request(payload),
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
                resp.raise_for_status()
                body = _as_dict(resp.json())
                if body is None:
                    raise WebSearchError(f"{_PROTOCOL_PREFIX} 检索响应报文畸形(JSON 对象缺失)")
                return {"results": self._normalize(body)}
        # 顺序铁律(089 审查 P1-1 同款):未送达族(含 ConnectTimeout)先于读超时
        except undelivered as e:
            raise WebSearchError(f"{_UNDELIVERED_PREFIX} 联网检索拒连({type(e).__name__})") from e
        except httpx.TimeoutException as e:
            # 读超时=请求已送达、响应未按期 → 响应类不重试
            raise WebSearchError(f"{_PROTOCOL_PREFIX} 检索读超时({type(e).__name__})") from e
        except httpx.HTTPStatusError as e:
            raise WebSearchError(f"{_PROTOCOL_PREFIX} 上游 HTTP {e.response.status_code}") from e
        except (ValueError, TypeError) as e:
            raise WebSearchError(f"{_PROTOCOL_PREFIX} 响应报文畸形({type(e).__name__})") from e
        except httpx.DecodingError as e:
            # 解码失败继承 RequestError(非 TransportError),不单列会以裸 httpx
            # 异常逃逸收敛链、审计 error_reason 丢结构化前缀(评审循环 R1)
            raise WebSearchError(f"{_PROTOCOL_PREFIX} 响应解码失败({type(e).__name__})") from e
        except httpx.TransportError as e:
            # 传输层未枚举残族(防 httpx 分层演进逃逸;089 同款兜底)
            raise WebSearchError(f"{_UNDELIVERED_PREFIX} 联网检索拒连({type(e).__name__})") from e
