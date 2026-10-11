"""HTTP 连接器域策略器 + 执行器(099:租户级域,089 框架第四域)。

与既有部署级域(websearch/ticket)的两处结构差异(design D2):
- 清单不是启动装载的部署配置,而是租户库表行——装配纯函数(行 → 域清单
  声明)按请求动态重放,注册/启停即时生效;
- 凭证来自库行加密列(解密在组合根,执行器构造期注入,只进请求头)。

策略器(纯函数,安全资产):出口校验(注册声明的白名单精确命中——端口与
scheme 由路径模板约束,白名单仅主机)/参数校验(按注册参数声明:未知拒/
缺必填拒/类型不符拒)/响应整形(执行器已文本化截断;外部文本过既有注入
检测面)/重试分类(089 两档语义 + policy 前缀同样不重试)。
执行器:单次出站的通用 HTTP 适配——路径占位 URL 编码后直插(design D6)、
查询参数与 POST JSON body 按名映射、鉴权头四形态装配(none/bearer/api-key
头/basic)、响应文本化截断进观察、重定向不跟随、出站前经 029 SSRF 拒绝段
(白名单命中不构成豁免,design D3)。不含治理逻辑——出口校验/重试由网关
切面统一承担。
"""

import base64
import json
from typing import cast
from urllib.parse import quote, urlencode, urlsplit

import httpx

from kongming.connector.models import API_KEY_HEADER, ParamDecl, row_param_decls
from kongming.mcp.models import sanitize_external_text
from kongming.toolgateway.models import (
    CONNECTOR_DOMAIN,
    FAILURE_PROTOCOL_PREFIX,
    FAILURE_UNDELIVERED_PREFIX,
    DomainSpec,
    RetryClass,
)
from kongming.workflow.http_runner import PinnedTransport, assert_url_allowed

# 域失败文案前缀单源在 toolgateway.models(与 gateway._was_delivered 共引)
_UNDELIVERED_PREFIX = FAILURE_UNDELIVERED_PREFIX
_PROTOCOL_PREFIX = FAILURE_PROTOCOL_PREFIX
# 策略类拒绝前缀(网关既有出口/参数拒绝同前缀;分类语义=policy,不重试)
_POLICY_PREFIX = "policy:"

# 观察体积上限(响应文本化后进观察的截断点;标注截断,spec「大响应截断」)
_OBSERVATION_CAP = 16 * 1024
# 响应读取上限(出站读取的字节闸,防巨头响应内存放大;029 http 节点同量级)
_RESPONSE_CAP = 1024 * 1024


class HttpConnectorError(Exception):
    """连接器域执行失败的结构化收敛(分发兜底 ERROR + 审计;文案前缀承载
    分类:unreachable:/protocol:/policy:)。"""


def _as_text_response(resp: httpx.Response) -> tuple[str, bool]:
    """响应体 → 文本形态(JSON 序列化为文本,其余原样;读字节闸内)。
    返回 (文本, 是否截断)。"""
    buf = bytearray()
    for chunk in resp.iter_bytes(65536):
        buf.extend(chunk)
        if len(buf) > _RESPONSE_CAP:
            break
    text = buf.decode("utf-8", errors="replace")
    ctype = resp.headers.get("content-type", "")
    if "json" in ctype.lower():
        try:
            text = json.dumps(json.loads(text), ensure_ascii=False)
        except (ValueError, TypeError):
            pass  # 声明 JSON 但报文畸形:原样文本化(截断闸兜底)
    truncated = len(buf) > _RESPONSE_CAP or len(text) > _OBSERVATION_CAP
    if len(text) > _OBSERVATION_CAP:
        text = text[:_OBSERVATION_CAP]
    if truncated:
        text = f"{text}\n…[响应超 {_OBSERVATION_CAP // 1024}KB 已截断]"
    return text, truncated


def build_url(
    method: str, template: str, params: tuple[ParamDecl, ...], payload: dict[str, object]
) -> str:
    """路径模板 → 出站 URL(纯函数):占位参数 URL 编码后直插;query 只承载
    GET 的其余字符串型参数(非字符串标量转文本)。渠道按方法二选一(design
    D6「查询参数与 POST JSON body 由参数声明按名映射」;评审修复——原实现
    POST 声明参数同时进 query 与 body,业务参数值泄漏进 URL/网关日志)。
    缺省参数不出现在 URL。"""
    param_types = {p.name: p.type for p in params}
    path_args: dict[str, str] = {}
    query_pairs: list[tuple[str, str]] = []
    for name, value in payload.items():
        ptype = param_types.get(name)
        if ptype is None:
            continue  # 越声明参数由策略器拒绝;此处防御性忽略
        if ptype == "boolean":
            text = "true" if value is True else ("false" if value is False else "")
        else:
            text = str(value)
        if f"{{{name}}}" in template:
            path_args[name] = quote(text, safe="")
        elif method != "POST" and text != "":
            query_pairs.append((name, text))
    url = template
    for name, text in path_args.items():
        url = url.replace(f"{{{name}}}", text)
    if query_pairs:
        url = f"{url}?{urlencode(query_pairs)}"
    return url


def build_json_body(
    method: str, template: str, payload: dict[str, object]
) -> dict[str, object] | None:
    """POST 语义的 JSON body(纯函数):占位参数之外的入参按名映射(POST 的
    唯一参数渠道,见 build_url);GET 无 body。缺省参数不进 body。"""
    if method != "POST":
        return None
    body = {k: v for k, v in payload.items() if f"{{{k}}}" not in template}
    return body or None


def build_auth_headers(auth_type: str, secret: str | None) -> dict[str, str]:
    """鉴权头四形态装配(纯函数):none 不带头;bearer/api_key 头/basic
    (材料单串 user:password → Basic b64)。凭证只进请求头,不进 URL、
    观察与审计(调用方保证不落日志)。"""
    if auth_type == "bearer" and secret:
        return {"Authorization": f"Bearer {secret}"}
    if auth_type == "api_key" and secret:
        return {API_KEY_HEADER: secret}
    if auth_type == "basic" and secret:
        encoded = base64.b64encode(secret.encode()).decode()
        return {"Authorization": f"Basic {encoded}"}
    return {}


class HttpConnectorPolicy:
    """连接器域策略器(纯函数,零外部状态;行派生,每连接器一份)。"""

    def __init__(self, *, whitelist_hosts: tuple[str, ...], params: tuple[ParamDecl, ...]) -> None:
        self._hosts = frozenset(h.lower() for h in whitelist_hosts)
        self._params = {p.name: p for p in params}

    def exit_host_allowed(self, host: str) -> bool:
        return host.lower() in self._hosts

    def validate_args(self, args: dict[str, object]) -> str | None:
        """按注册参数声明校验:未知拒/缺必填拒/类型不符拒(bool 不冒充
        number;number 接受 int/float)。"""
        for name, value in args.items():
            decl = self._params.get(name)
            if decl is None:
                return f"policy: 未知参数 {name}(不在该连接器参数声明内)"
            if decl.type == "string":
                if not isinstance(value, str) or not value.strip():
                    return f"policy: 参数 {name} 必须为非空字符串"
            elif decl.type == "number":
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    return f"policy: 参数 {name} 必须为数值"
            elif decl.type == "boolean" and not isinstance(value, bool):
                return f"policy: 参数 {name} 必须为布尔值"
        for name, decl in self._params.items():
            if decl.required and name not in args:
                return f"policy: 缺必填参数 {name}"
        return None

    def redact(self, response: object) -> object:
        """观察整形:执行器产物(status_code/body/truncated)的 body 过既有
        注入检测面(外部文本 fail-closed);形态异常收敛友好空观察。"""
        body_obj = cast(
            "dict[str, object] | None", response if isinstance(response, dict) else None
        )
        if body_obj is None:
            return {"status_code": None, "body": "(空响应)", "truncated": False}
        cleaned, hits = sanitize_external_text(str(body_obj.get("body") or ""))
        out: dict[str, object] = {
            "status_code": body_obj.get("status_code"),
            "body": cleaned,
            "truncated": bool(body_obj.get("truncated")),
        }
        if hits:
            out["sanitized"] = True
            out["injection_hits"] = hits
        return out

    def classify(self, exc: Exception | None, *, delivered: bool) -> RetryClass:
        """按执行器收敛出的前缀判定(089 两档 + policy 语义):unreachable=
        连接层未送达(重试至多一次);protocol/policy=不重试。"""
        if exc is None:
            return RetryClass.OK
        text = str(exc)
        if text.startswith(_UNDELIVERED_PREFIX):
            return RetryClass.UNDELIVERED
        return RetryClass.PROTOCOL

    def failure_reason(self, verdict: RetryClass, exc: Exception | None) -> str:
        """失败文案(审计 error_reason;与执行器收敛文案同口径)。"""
        if exc is None:
            return f"{_UNDELIVERED_PREFIX} 连接器端点拒连(Unknown)"
        return str(exc)


class HttpConnectorExecutor:
    """连接器域执行器:单次出站的通用 HTTP 适配;零治理。

    失败在执行器内收敛为 HttpConnectorError(unreachable:/protocol:/policy:
    前缀),重试由网关切面按策略器分类承担——执行器自身不做第二次尝试。
    出站前经 029 SSRF 拒绝段(design D3:白名单命中不构成豁免;存量行/
    白名单拼错的执行期兜底),判定地址钉扎直连(防 DNS rebinding),重定向
    不跟随(白名单绕过的经典通道)。凭证只在构造期注入,只进请求头。
    """

    def __init__(
        self,
        *,
        method: str,
        path_template: str,
        params: tuple[ParamDecl, ...],
        auth_type: str,
        auth_secret: str | None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._method = method
        self._template = path_template
        self._params = params
        self._headers = build_auth_headers(auth_type, auth_secret)
        # transport 仅测试注入(与 knowledge/websearch 域同构的注入缝;
        # 生产 None——生产走 SSRF 钉扎 transport)
        self._test_transport = transport

    async def __call__(self, *, url: str, payload: dict[str, object], timeout_s: float) -> object:
        """url=路径模板(带占位;网关 spec.endpoint 口径),payload=已过策略器
        的入参;执行器只发一次。"""
        final_url = build_url(self._method, self._template, self._params, payload)
        body = build_json_body(self._method, self._template, payload)
        try:
            # 029 拒绝段(design D3 执行期兜底;先于出站,DNS 解析任一地址
            # 落拒绝段即拒,白名单命中不构成豁免)
            pinned = await assert_url_allowed(final_url)
        except Exception as e:  # SsrFBlocked 与解析族统一按策略类拒绝
            raise HttpConnectorError(f"{_POLICY_PREFIX} 目标地址被拒绝({e})") from e
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
            transport = self._test_transport or PinnedTransport(pinned)
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(timeout_s),
                transport=transport,
                follow_redirects=False,  # 重定向是白名单绕过的经典通道,一律不跟随
            ) as client:
                resp = await client.request(
                    self._method,
                    final_url,
                    json=body,
                    headers=self._headers,
                )
                if 300 <= resp.status_code < 400:
                    # 不静默跟随:按响应类失败收口(spec 场景「重定向不出白名单」)
                    raise HttpConnectorError(
                        f"{_PROTOCOL_PREFIX} 端点返回重定向 {resp.status_code}(不跟随)"
                    )
                resp.raise_for_status()
                body_text, truncated = _as_text_response(resp)
                return {
                    "status_code": resp.status_code,
                    "body": body_text,
                    "truncated": truncated,
                }
        # 顺序铁律(089 审查 P1-1 同款):未送达族(含 ConnectTimeout)先于读超时
        except HttpConnectorError:
            raise  # 执行器自收敛(重定向)原样上浮,保前缀分类
        except undelivered as e:
            raise HttpConnectorError(
                f"{_UNDELIVERED_PREFIX} 连接器端点拒连({type(e).__name__})"
            ) from e
        except httpx.TimeoutException as e:
            # 读超时=请求已送达、响应未按期 → 响应类不重试
            raise HttpConnectorError(f"{_PROTOCOL_PREFIX} 连接器读超时({type(e).__name__})") from e
        except httpx.HTTPStatusError as e:
            raise HttpConnectorError(
                f"{_PROTOCOL_PREFIX} 端点 HTTP {e.response.status_code}"
            ) from e
        except httpx.TransportError as e:
            # 传输层未枚举残族(防 httpx 分层演进逃逸;089 同款兜底)
            raise HttpConnectorError(
                f"{_UNDELIVERED_PREFIX} 连接器端点拒连({type(e).__name__})"
            ) from e


def connector_domain_spec(row: dict[str, object], *, quota_per_minute: int) -> DomainSpec:
    """连接器行 → 域清单声明(纯函数,design D2「行→声明」;每请求重放,
    可确定性测试)。endpoint=路径模板(占位形态;网关出口校验取其主机)。"""
    hosts_raw = row.get("allowed_hosts")
    hosts = (
        tuple(str(h) for h in cast("list[object]", hosts_raw) if isinstance(h, str))
        if isinstance(hosts_raw, list)
        else ()
    )
    return DomainSpec(
        id=CONNECTOR_DOMAIN,
        endpoint=str(row.get("path_template") or ""),
        whitelist_hosts=hosts,
        timeout_ms=int(str(row.get("timeout_ms") or 0)),
        quota_per_minute=quota_per_minute,
        redaction="text:http-connector",
    )


def build_connector_stack(
    row: dict[str, object],
    *,
    auth_secret: str | None,
    quota_per_minute: int,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[DomainSpec, HttpConnectorPolicy, HttpConnectorExecutor]:
    """行 → (域声明, 策略器, 执行器) 三件套(纯函数;凭证只进执行器构造期;
    transport 仅测试注入)。"""
    params = row_param_decls(row)
    spec = connector_domain_spec(row, quota_per_minute=quota_per_minute)
    policy = HttpConnectorPolicy(whitelist_hosts=spec.whitelist_hosts, params=params)
    executor = HttpConnectorExecutor(
        method=str(row.get("method") or "GET"),
        path_template=spec.endpoint,
        params=params,
        auth_type=str(row.get("auth_type") or "none"),
        auth_secret=auth_secret,
        transport=transport,
    )
    return spec, policy, executor


def template_host(path_template: str) -> str:
    """模板主机提取(出口白名单一致性与治理面摘要共用;小写)。"""
    return (urlsplit(path_template).hostname or "").lower()
