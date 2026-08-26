"""http 节点执行器(029):SSRF 防护(design D3)——URL host 经
getaddrinfo 解析,任一地址落入拒绝段即 SsrFBlocked(不发出请求);
不跟随重定向(302 跳内网防);响应 1MB 截断;15s 超时。

审查循环 F4(DNS rebinding TOCTOU):判定解析与 httpx 连接是两次独立
DNS 解析,权威 DNS 可对两次返回不同答案(TTL=0)绕过判定——修=判定时
钉扎解析到的 IP,经自定义 transport 直接连该 IP(Host 头保原域名、
TLS SNI/证书校验经 sni_hostname 扩展指向原域名),连接期不再二次解析。

拒绝段:私网 10/8、172.16/12、192.168/16;回环 127/8 与 ::1;
链路本地 169.254/16(含云元数据 169.254.169.254)与 fe80::/10;
未指定 0.0.0.0/:: 与保留段;IPv4 映射 IPv6 逃逸面经 ipaddress
归一化(ipv4_mapped 取出再判)。
"""

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
# 超限抛协议异常 → 引擎通用 except 收敛 FAILED)——已实证兜底巨头攻击;该依赖
# 以「HTTP/1.1 传输(未启用 HTTP/2)」为前提,若未来切换传输须补显式头闸(B2-B9 结案注记)
_TIMEOUT = 15


async def _resolve(host: str, port: int) -> list[Any]:
    """模块级解析入口(测试可 monkeypatch;loop.getaddrinfo 直调不可 patch)。"""
    return await asyncio.get_running_loop().getaddrinfo(
        host, port, type=socket.SOCK_STREAM
    )


_CGNAT = ipaddress.ip_network("100.64.0.0/10")  # R1-J7:共享段(云内网/Tailscale)


def _is_denied_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped  # ::ffff:127.0.0.1 逃逸面归一
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_unspecified
        or ip.is_reserved
        or ip.is_multicast
        or ip in _CGNAT  # is_private 不含 CGNAT 共享段(实证),显式补
    )


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
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        if _is_denied_ip(ip):
            raise SsrFBlocked(f"目标地址在拒绝段: {host}")
        return str(ip)
    # 域名:解析后全量判定(任一落入拒绝段即拒),首个通过地址作钉扎目标
    try:
        infos = await _resolve(host, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as e:
        raise SsrFBlocked(f"域名解析失败: {host}") from e
    if not infos:
        raise SsrFBlocked(f"域名无解析结果: {host}")
    pinned: str | None = None
    for info in infos:
        addr = info[4][0]
        try:
            resolved = ipaddress.ip_address(addr)
        except ValueError:
            raise SsrFBlocked(f"解析结果非法: {addr}") from None
        if _is_denied_ip(resolved):
            raise SsrFBlocked(f"{host} 解析到拒绝段地址: {addr}")
        if pinned is None:
            pinned = str(resolved)
    if pinned is None:  # infos 非空且全部通过判定,防御分支
        raise SsrFBlocked(f"{host} 无可用解析结果")
    return pinned


class _PinnedTransport(httpx.AsyncHTTPTransport):
    """连接期 DNS 钉扎:URL host 换成判定时的 IP,Host 头/SNI 保原域名。"""

    def __init__(self, pinned_ip: str) -> None:
        super().__init__()
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
            timeout=httpx.Timeout(_TIMEOUT),
            follow_redirects=False,
            transport=_PinnedTransport(pinned),
        ) as client:
            async with client.stream(
                method, url, headers=headers, content=body
            ) as resp:
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
