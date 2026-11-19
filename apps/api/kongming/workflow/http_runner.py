"""HTTP 工作流节点执行器:解析并固定目标 IP 后再连接,降低 DNS 重绑定风险;
不跟随重定向,响应上限 1 MB,超时 15 秒。

拒绝私网、回环、link-local、未指定和保留地址(含云元数据地址);
IPv4 映射 IPv6 地址先归一化再校验。"""

import asyncio
import ipaddress
import socket
from typing import Any
from urllib.parse import urlparse

import httpx

from kongming.workflow.ports import SsrFBlocked

_RESPONSE_CAP = 1 * 1024 * 1024  # 1MB 截断
_BODY_CAP = 64 * 1024  # 进 outputs 的 body 截断
# 响应头无显式闸:依赖 h11 默认 16KB 头块上限(DEFAULT_MAX_INCOMPLETE_EVENT_SIZE,
# 超限抛协议异常 → 引擎通用 except 收敛 FAILED)——已兜底巨头攻击;该依赖
# 以「HTTP/1.1 传输(未启用 HTTP/2)」为前提,若未来切换传输须补显式头闸
_TIMEOUT = 15


async def _resolve(host: str, port: int) -> list[Any]:
    """模块级解析入口(测试可 monkeypatch;loop.getaddrinfo 直调不可 patch)。"""
    return await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)


_CGNAT = ipaddress.ip_network("100.64.0.0/10")  # 共享段(云内网/Tailscale)


def _is_denied_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped  # ::ffff:x.x.x.x 映射形态归一为 IPv4 再判(防 v6 包 v4 逃逸)
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_unspecified
        or ip.is_reserved
        or ip.is_multicast
        or ip in _CGNAT  # is_private 不含 CGNAT 共享段,显式补
    )


def _parse_literal_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """字面 IP 判定;域名形态返回 None(须走 DNS)。"""
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


async def _pin_resolved_address(host: str, port: int) -> str:
    """域名解析后全量判定(任一落入拒绝段即拒),返回首个通过地址作钉扎目标。"""
    try:
        infos = await _resolve(host, port)
    except socket.gaierror as e:
        raise SsrFBlocked(f"域名解析失败: {host}") from e
    if not infos:
        raise SsrFBlocked(f"域名无解析结果: {host}")
    pinned: str | None = None
    for info in infos:
        addr = info[4][0]
        resolved = _parse_literal_ip(addr)
        if resolved is None:
            raise SsrFBlocked(f"解析结果非法: {addr}")
        if _is_denied_ip(resolved):
            raise SsrFBlocked(f"{host} 解析到拒绝段地址: {addr}")
        if pinned is None:
            pinned = str(resolved)
    if pinned is None:  # infos 非空且全部通过判定,防御分支
        raise SsrFBlocked(f"{host} 无可用解析结果")
    return pinned


async def assert_url_allowed(url: str) -> str:
    """SSRF 判定(纯函数+DNS 解析):scheme/host/解析地址三层。

    返回钉扎 IP(连接期直连该地址,消除二次解析的 rebinding 窗口);
    字面 IP 输入原样返回。
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise SsrFBlocked(f"仅允许 http/https: {parsed.scheme or '(空)'}")
    host = parsed.hostname
    if not host:
        raise SsrFBlocked("URL 缺 host")
    # 字面 IP 直判(不经 DNS)
    ip = _parse_literal_ip(host)
    if ip is not None:
        if _is_denied_ip(ip):
            raise SsrFBlocked(f"目标地址在拒绝段: {host}")
        return str(ip)
    return await _pin_resolved_address(
        host, parsed.port or (443 if parsed.scheme == "https" else 80)
    )


def assert_host_allowed_sync(host: str, port: int) -> None:
    """同步检查主机级 SSRF 拒绝规则:解析出的任一地址命中拒绝段即拒绝;
    同步解析必须在线程中运行。IMAP 连接无法固定已解析 IP,连接阶段
    仍有 DNS 重绑定风险。"""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        if _is_denied_ip(ip):
            raise SsrFBlocked(f"目标地址在拒绝段: {host}")
        return
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise SsrFBlocked(f"域名解析失败: {host}") from e
    for info in infos:
        addr = info[4][0]
        try:
            resolved = ipaddress.ip_address(addr)
        except ValueError:
            raise SsrFBlocked(f"解析结果非法: {addr}") from None
        if _is_denied_ip(resolved):
            raise SsrFBlocked(f"{host} 解析到拒绝段地址: {addr}")


class _PinnedTransport(httpx.AsyncHTTPTransport):
    """连接期 DNS 钉扎:URL host 换成判定时的 IP,Host 头/SNI 保原域名。"""

    def __init__(self, pinned_ip: str) -> None:
        super().__init__(trust_env=False)  # 钉扎=直连该 IP,环境代理会架空 SSRF 判定
        self._pinned = pinned_ip

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        original = request.url
        if original.host != self._pinned:
            # Host 头=原域名(显式端口保留);TLS SNI/证书校验经扩展指向原域名
            port = original.port
            default = 443 if original.scheme == "https" else 80
            netloc = original.host if port in (None, default) else f"{original.host}:{port}"
            request.headers["Host"] = netloc
            request.extensions["sni_hostname"] = original.host
            request.url = original.copy_with(host=self._pinned)
        return await super().handle_async_request(request)


# 组合根跨域复用别名(连接器执行器出站钉扎;registry.LOCAL_HOSTS 同哲学)
PinnedTransport = _PinnedTransport


class HttpNodeRunner:
    """HttpRunner 实现(httpx;不跟随重定向——302 跳内网是 SSRF 经典绕过)。"""

    async def run_http(
        self,
        *,
        url: str,
        method: str,
        headers: dict[str, str],
        body: str | None,
    ) -> dict[str, Any]:
        pinned = await assert_url_allowed(url)
        async with httpx.AsyncClient(
            trust_env=False,  # 出站受控:不采环境代理(SSRF 钉扎/内网回调语义)
            timeout=httpx.Timeout(_TIMEOUT),
            follow_redirects=False,
            transport=_PinnedTransport(pinned),
        ) as client:
            async with client.stream(method, url, headers=headers, content=body) as resp:
                buf = bytearray()
                async for chunk in resp.aiter_bytes(65536):
                    buf.extend(chunk)
                    if len(buf) > _RESPONSE_CAP:
                        break
                text = buf.decode("utf-8", errors="replace")
                truncated = len(buf) > _RESPONSE_CAP
                if truncated:
                    text = text[:_BODY_CAP] + "\n…[响应超 1MB 已截断]"
                else:
                    text = text[:_BODY_CAP]
                return {"status": resp.status_code, "body": text}
