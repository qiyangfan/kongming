"""knowledge 域策略器 + 执行器(089:081 knowledge_search 收编为网关第一域)。

策略器(纯函数,安全资产):出口校验(localhost 白名单)/参数校验/响应脱敏
(allowlist:只透出 source_name/note_path/anchor/text,分数与内部 id 永不入
观察)/重试分类(081 两档语义零回归:未完整取得响应族=未送达可重试;读超时/
非 2xx/报文畸形=已送达不重试)。失败文案与迁移前逐字一致(审计 error_reason
零漂移),分类按 KnowledgeSourceError 前缀判定。
执行器:单次出站的协议适配(httpx POST /api/search),不含治理逻辑——重试
与脱敏由网关切面统一承担。
"""

from typing import cast

import httpx

from kongming.toolgateway.gateway import KnowledgeSourceError
from kongming.toolgateway.models import (
    FAILURE_PROTOCOL_PREFIX,
    FAILURE_UNDELIVERED_PREFIX,
    RetryClass,
)

# 观察白名单(脱敏策略 allowlist:knowledge)——与迁移前观察整形逐字段一致
_HIT_FIELDS = ("source_name", "note_path", "anchor", "text")

# 失败文案前缀单源(094 评审循环收编,原 081 本地字面量)
_UNDELIVERED_PREFIX = FAILURE_UNDELIVERED_PREFIX
_PROTOCOL_PREFIX = FAILURE_PROTOCOL_PREFIX


class KnowledgePolicy:
    """knowledge 域策略器(纯函数,零外部状态)。"""

    def __init__(self, *, whitelist_hosts: tuple[str, ...]) -> None:
        self._hosts = frozenset(whitelist_hosts)

    def exit_host_allowed(self, host: str) -> bool:
        return host.lower() in self._hosts

    def validate_args(self, args: dict[str, object]) -> str | None:
        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            return "policy: query 必须为非空字符串"
        top_k = args.get("top_k")
        if top_k is not None and (not isinstance(top_k, int) or isinstance(top_k, bool)):
            return "policy: top_k 必须为整数"
        return None

    def redact(self, response: object) -> object:
        """allowlist 脱敏:命中条目只保留观察白名单字段(先于观察构建)。"""
        body = cast("dict[str, object] | None", response if isinstance(response, dict) else None)
        raw = body.get("results") if body is not None else None
        if not isinstance(raw, list):
            return {"results": []}
        hits: list[dict[str, object]] = []
        for h in cast("list[object]", raw):
            if isinstance(h, dict):
                hd = cast("dict[str, object]", h)
                hits.append({k: hd.get(k) for k in _HIT_FIELDS})
        return {"results": hits}

    def classify(self, exc: Exception | None, *, delivered: bool) -> RetryClass:
        """按执行器收敛出的前缀判定(081 两档语义零回归):
        unreachable=连接层未送达(重试至多一次);protocol=已送达不重试。"""
        if exc is None:
            return RetryClass.OK
        text = str(exc)
        if text.startswith(_UNDELIVERED_PREFIX):
            return RetryClass.UNDELIVERED
        return RetryClass.PROTOCOL

    def failure_reason(self, verdict: RetryClass, exc: Exception | None) -> str:
        """失败文案零漂移(审计 error_reason 与迁移前逐字一致)。"""
        if exc is None:
            return f"{_UNDELIVERED_PREFIX}知识库拒连(Unknown)"
        return str(exc)


def _as_dict(value: object) -> dict[str, object] | None:
    return cast("dict[str, object]", value) if isinstance(value, dict) else None


class KnowledgeExecutor:
    """knowledge 域执行器:单次协议适配(POST {base}/api/search);零治理。

    失败在执行器内收敛为 081 同款 KnowledgeSourceError(消息逐字保留),
    重试由网关切面按策略器分类承担——执行器自身不做第二次尝试。
    """

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        # transport 仅测试注入(MockTransport 定点触发特定异常族);生产 None
        self._transport = transport

    async def __call__(self, *, url: str, payload: dict[str, object], timeout_s: float) -> object:
        """url/payload/timeout 由网关切面给定(域清单口径);执行器只发一次。"""
        # 未完整取得响应族(081 同集;拒连在 macOS anyio 后端以 ReadError 浮现,
        # 按语义而非异常名归纳)
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
                trust_env=False,  # 出站受控:不采环境代理(SSRF 钉扎/内网回调语义)
                timeout=httpx.Timeout(timeout_s),
                transport=self._transport,
            ) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                body = _as_dict(resp.json())
                # 结构性不合格直抛(消息零漂移;不被 ValueError 兜底改写)
                if body is None:
                    raise KnowledgeSourceError("protocol: 检索响应缺 results 列表")
                raw = body.get("results")
                if not isinstance(raw, list):
                    raise KnowledgeSourceError("protocol: 检索响应缺 results 列表")
                items: list[dict[str, object]] = []
                for i in cast("list[object]", raw):
                    if isinstance(i, dict):
                        items.append(cast("dict[str, object]", i))
                return {"results": items}
        # 顺序铁律(审查 P1-1):未送达族(含 ConnectTimeout,是 TimeoutException
        # 子类)必须先于读超时判定,否则连接类被误判响应类、重试与文案双漂移
        except undelivered as e:
            raise KnowledgeSourceError(f"unreachable: 知识库拒连({type(e).__name__})") from e
        except httpx.TimeoutException as e:
            # 读超时=请求已送达、响应未按期 → 响应类不重试(081 语义零回归)
            raise KnowledgeSourceError(f"protocol: 检索读超时({type(e).__name__})") from e
        except httpx.HTTPStatusError as e:
            raise KnowledgeSourceError(f"protocol: 上游 HTTP {e.response.status_code}") from e
        except (ValueError, TypeError) as e:
            raise KnowledgeSourceError(f"protocol: 响应报文畸形({type(e).__name__})") from e
        except httpx.DecodingError as e:
            # 解码失败继承 RequestError(非 TransportError),不单列会以裸 httpx
            # 异常逃逸收敛链、审计 error_reason 丢结构化前缀(094 评审循环 R1 同款)
            raise KnowledgeSourceError(f"protocol: 检索响应解码失败({type(e).__name__})") from e
        except httpx.TransportError as e:
            # 传输层未枚举残族(防 httpx 分层演进逃逸;081 同款兜底)
            raise KnowledgeSourceError(f"unreachable: 知识库拒连({type(e).__name__})") from e
