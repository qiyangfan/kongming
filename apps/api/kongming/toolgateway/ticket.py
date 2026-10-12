"""ticket 域策略器 + 执行器(100 internal-connector-domain:内部工单,089 框架第三域)。

「最小工单 API 约定」单源在 apps/api/scripts/mock_ticket_server.py 文件头
(仓库单文件 mock 与 e2e/演示共用同一实现);本域只消费该约定:三端点
(GET /tickets、GET /tickets/{id}、POST /tickets)+ 统一 JSON 字段命名。

策略器(纯函数,安全资产):出口校验(部署端点主机白名单,组合根从端点单源
派生)/参数校验(三工具入参集不相交,按形态分派校验并拒绝混合形态)/响应
脱敏(allowlist:工单卡只透 id/title/status/priority/assignee/created_at/
updated_at——描述与工单系统内部元数据永不入观察)/重试分类(089 两档语义:
连接层未送达重试至多一次;响应类不重试)。
执行器:单次出站的约定协议适配(Bearer 服务凭证头装配 + 三操作形态分派 +
响应归一),不含治理逻辑——重试与脱敏由网关切面统一承担。
状态/优先级枚举原样透传不翻译(design D5:翻译语义在工具描述 prompt 层,
不落代码映射,避免语义漂移双源)。
"""

import json
from typing import cast
from urllib.parse import quote

import httpx

from kongming.toolgateway.models import (
    FAILURE_PROTOCOL_PREFIX,
    FAILURE_UNDELIVERED_PREFIX,
    TICKET_LIMIT_MAX,
    TICKET_LIMIT_MIN,
    TICKET_QUERY_MAX_LEN,
    TICKET_STATUS_MAX_LEN,
    RetryClass,
)

# 观察白名单(脱敏策略 allowlist:ticket)——工单卡七键:约定字段全集去掉
# description(描述属存储/入站面,进 LLM 上下文徒增噪声与泄露面,约定明文)
_CARD_FIELDS = ("id", "title", "status", "priority", "assignee", "created_at", "updated_at")

# 三工具入参键集(与注册表 schema additionalProperties=false 同源对齐;
# 策略器按形态校验并拒绝混合,是 schema 之外的纵深一层)
_GET_KEYS = frozenset({"id"})
_CREATE_KEYS = frozenset({"title", "description", "priority", "assignee"})
_SEARCH_KEYS = frozenset({"query", "status", "limit"})

# 域失败文案前缀单源在 models(与 gateway._was_delivered 共引,防三处漂移)
_UNDELIVERED_PREFIX = FAILURE_UNDELIVERED_PREFIX
_PROTOCOL_PREFIX = FAILURE_PROTOCOL_PREFIX


class TicketError(Exception):
    """ticket 域执行失败的结构化收敛(分发兜底 ERROR + 审计)。"""


class _ResponseTooLarge(Exception):
    """响应超字节闸(内部信号;执行器收敛为 protocol 前缀域错误)。"""


# 响应读取上限(字节闸,防巨头响应内存放大;099 connector 域同量级)
_RESPONSE_CAP = 1024 * 1024


async def _read_json_capped(resp: httpx.Response) -> object:
    """stream 上下文内读 JSON 响应(字节闸内)——resp.json() 会全量预读
    入内存,真流式须 aiter_bytes(099 connector 域同款修法)。"""
    buf = bytearray()
    async for chunk in resp.aiter_bytes(65536):
        buf.extend(chunk)
        if len(buf) > _RESPONSE_CAP:
            raise _ResponseTooLarge from None
    return json.loads(buf.decode("utf-8", errors="replace"))


def _as_dict(value: object) -> dict[str, object] | None:
    return cast("dict[str, object]", value) if isinstance(value, dict) else None


def _card(d: dict[str, object]) -> dict[str, object]:
    """工单卡 allowlist 投影(缺字段补 None,不透传白名单外键)。"""
    return {k: d.get(k) for k in _CARD_FIELDS}


class TicketPolicy:
    """ticket 域策略器(纯函数,零外部状态)。"""

    def __init__(self, *, whitelist_hosts: tuple[str, ...]) -> None:
        self._hosts = frozenset(whitelist_hosts)

    def exit_host_allowed(self, host: str) -> bool:
        return host.lower() in self._hosts

    def validate_args(self, args: dict[str, object]) -> str | None:
        """按形态分派校验(三工具入参集不相交:id=单查 / title=创建 / 其余=
        检索;混合形态显式拒绝)。枚举值只校验字符串形态,不做值域翻译
        (design D5:透传语义)。"""
        if "id" in args:
            shape, keys = "单查", _GET_KEYS
        elif "title" in args:
            shape, keys = "创建", _CREATE_KEYS
        else:
            shape, keys = "检索", _SEARCH_KEYS
        unknown = set(args) - keys
        if unknown:
            return f"policy: 入参形态混合({shape}不接受 {'、'.join(sorted(unknown))})"
        if shape == "单查":
            ticket_id = args.get("id")
            if not isinstance(ticket_id, str) or not ticket_id.strip():
                return "policy: id 必须为非空字符串"
            return None
        if shape == "创建":
            for required in ("title", "description", "priority"):
                v = args.get(required)
                if not isinstance(v, str) or not v.strip():
                    return f"policy: {required} 必须为非空字符串"
            assignee = args.get("assignee")
            if assignee is not None and not isinstance(assignee, str):
                return "policy: assignee 必须为字符串"
            return None
        query = args.get("query")
        if query is not None and (not isinstance(query, str) or len(query) > TICKET_QUERY_MAX_LEN):
            return f"policy: query 须为 ≤{TICKET_QUERY_MAX_LEN} 字符的字符串"
        status = args.get("status")
        if status is not None and (
            not isinstance(status, str) or not status.strip() or len(status) > TICKET_STATUS_MAX_LEN
        ):
            return "policy: status 须为非空短字符串(原样透传工单系统)"
        limit = args.get("limit")
        if limit is not None and (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not TICKET_LIMIT_MIN <= limit <= TICKET_LIMIT_MAX
        ):
            return f"policy: limit 必须为 {TICKET_LIMIT_MIN}-{TICKET_LIMIT_MAX} 的整数"
        return None

    def redact(self, response: object) -> object:
        """allowlist 脱敏(先于观察构建):列表形态 {"tickets": [...]} 逐卡投影;
        单卡形态(单查/创建的工单对象)整体投影;非法形态收敛空结果,
        不透传工单系统原文。"""
        body = _as_dict(response)
        if body is None:
            return {"tickets": []}
        raw = body.get("tickets")
        if isinstance(raw, list):
            cards = [
                _card(hd) for h in cast("list[object]", raw) if (hd := _as_dict(h)) is not None
            ]
            return {"tickets": cards}
        if "id" in body:  # 单查/创建返回的工单对象
            return _card(body)
        return {"tickets": []}

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
            return f"{_UNDELIVERED_PREFIX} 工单系统拒连(Unknown)"
        return str(exc)


class TicketExecutor:
    """ticket 域执行器:单次约定协议适配(三操作形态分派 + Bearer 头);零治理。

    操作分派按入参形态(id=单查 / title=创建 / 其余=检索;与策略器同构——
    网关切面先经策略器校验,此处为最后一道防线)。失败在执行器内收敛为
    TicketError(unreachable:/protocol: 前缀),重试由网关切面按策略器分类
    承担——执行器自身不做第二次尝试。
    凭证只在构造期注入(组合根装配),只进请求头,不进观察、URL 与审计参数。
    """

    def __init__(
        self,
        *,
        api_key: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        # transport 仅测试注入(与 knowledge/websearch 域同构的注入缝;生产 None)
        self._transport = transport
        self._api_key = api_key

    async def __call__(self, *, url: str, payload: dict[str, object], timeout_s: float) -> object:
        """url/payload/timeout 由网关切面给定(域清单口径);执行器只发一次。
        url 为约定根(组合根 parse_ticket_endpoint 归一,无尾斜杠)。"""
        base = url.rstrip("/")
        # 操作分派(与策略器同构;网关切面先经策略器校验,此处为最后一道防线)
        if "id" in payload:
            op = "get"
            path = f"{base}/tickets/{quote(str(payload.get('id') or ''), safe='')}"
            method, body, params = "GET", None, None
        elif "title" in payload:
            op = "create"
            path = f"{base}/tickets"
            req: dict[str, object] = {
                "title": payload.get("title"),
                "description": payload.get("description"),
                "priority": payload.get("priority"),
            }
            assignee = payload.get("assignee")
            if isinstance(assignee, str) and assignee.strip():
                req["assignee"] = assignee
            method, body, params = "POST", req, None
        else:
            op = "search"
            path = f"{base}/tickets"
            qs: dict[str, str | int] = {}
            query = payload.get("query")
            if isinstance(query, str) and query.strip():
                qs["query"] = query
            status = payload.get("status")
            if isinstance(status, str) and status.strip():
                qs["status"] = status
            limit = payload.get("limit")
            if isinstance(limit, int) and not isinstance(limit, bool):
                qs["limit"] = limit
            method, body, params = "GET", None, qs or None

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
                trust_env=False,  # 出站受控:不采环境代理(SSRF 钉扎/内网回调语义)
                timeout=httpx.Timeout(timeout_s),
                transport=self._transport,
            ) as client:
                # stream 上下文+字节闸读取(评审修复:resp.json() 全量预读,
                # 巨头响应先入内存;真流式见 _read_json_capped)
                async with client.stream(
                    method,
                    path,
                    json=body,
                    params=params,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                ) as resp:
                    if op == "get" and resp.status_code == 404:
                        # 单查不存在:响应类失败,友好「不存在」文案(spec 场景——
                        # 不编造、不静默吐空;凭证与端点细节不入文案)
                        raise TicketError(f"{_PROTOCOL_PREFIX} 工单不存在")
                    resp.raise_for_status()
                    data = _as_dict(await _read_json_capped(resp))
                    if data is None:
                        raise TicketError(f"{_PROTOCOL_PREFIX} 工单响应报文畸形(JSON 对象缺失)")
                    if op == "search":
                        # 检索形态:约定信封 {"tickets": [...], "total": n}
                        raw = data.get("tickets")
                        if not isinstance(raw, list):
                            raise TicketError(f"{_PROTOCOL_PREFIX} 工单响应缺 tickets 列表")
                        return {
                            "tickets": [
                                cast("dict[str, object]", item)
                                for item in cast("list[object]", raw)
                                if isinstance(item, dict)
                            ]
                        }
                    # 单查/创建:工单对象(allowlist 投影由策略器承担)
                    if "id" not in data:
                        raise TicketError(f"{_PROTOCOL_PREFIX} 工单响应缺工单标识")
                    return data
        # 顺序铁律(089 审查 P1-1 同款):未送达族(含 ConnectTimeout)先于读超时
        except undelivered as e:
            raise TicketError(f"{_UNDELIVERED_PREFIX} 工单系统拒连({type(e).__name__})") from e
        except httpx.TimeoutException as e:
            # 读超时=请求已送达、响应未按期 → 响应类不重试
            raise TicketError(f"{_PROTOCOL_PREFIX} 工单读超时({type(e).__name__})") from e
        except httpx.HTTPStatusError as e:
            raise TicketError(f"{_PROTOCOL_PREFIX} 上游 HTTP {e.response.status_code}") from e
        except _ResponseTooLarge as e:
            raise TicketError(f"{_PROTOCOL_PREFIX} 响应超 {_RESPONSE_CAP // 1024}KB 上限") from e
        except (ValueError, TypeError) as e:
            raise TicketError(f"{_PROTOCOL_PREFIX} 响应报文畸形({type(e).__name__})") from e
        except httpx.DecodingError as e:
            # 解码失败继承 RequestError(非 TransportError),不单列会以裸 httpx
            # 异常逃逸收敛链、审计 error_reason 丢结构化前缀(094 评审 R1 同款)
            raise TicketError(f"{_PROTOCOL_PREFIX} 响应解码失败({type(e).__name__})") from e
        except httpx.TransportError as e:
            # 传输层未枚举残族(防 httpx 分层演进逃逸;089 同款兜底)
            raise TicketError(f"{_UNDELIVERED_PREFIX} 工单系统拒连({type(e).__name__})") from e
