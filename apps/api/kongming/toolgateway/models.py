"""toolgateway 领域模型(089):域清单装载 + 重试分类(零框架,宪法 Article II)。

域清单是数据:装载为可确定性重放的纯函数,畸形单域剔除并告警(fail-closed
不裸崩,不影响其余域);重试分类=已送达与否的二值判定,由各域策略器声明。
"""

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, cast
from urllib.parse import urlsplit


class RetryClass(Enum):
    """失败分类(策略器职责 4):OK=成功;UNDELIVERED=连接层未送达(重试至多
    一次);PROTOCOL=已送达但响应不合格(4xx/5xx/报文畸形/读超时,不重试)。"""

    OK = "ok"
    UNDELIVERED = "undelivered"
    PROTOCOL = "protocol"


# 工具名 → 域标识单源(审计域标签与域配额闸共用;089 仅 knowledge 收编)。
# 放 models(零依赖数据),避免 toolcall.service 传递性引入 httpx(审查 P2)。
DOMAIN_TOOLS: dict[str, str] = {"knowledge_search": "knowledge"}


class DomainPolicy(Protocol):
    """域策略器协议(四职责,纯函数);各域实现结构性满足即可注册。"""

    def exit_host_allowed(self, host: str) -> bool: ...

    def validate_args(self, args: dict[str, object]) -> str | None: ...

    def redact(self, response: object) -> object: ...

    def classify(self, exc: Exception | None, *, delivered: bool) -> "RetryClass": ...


@dataclass(frozen=True)
class DomainSpec:
    """域清单条目(声明式数据,装载产物不可变)。"""

    id: str
    endpoint: str
    whitelist_hosts: tuple[str, ...]
    timeout_ms: int
    quota_per_minute: int
    redaction: str


def _endpoint_host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def load_manifest(raw: list[dict[str, object]]) -> tuple[dict[str, DomainSpec], list[str]]:
    """域清单装载(纯函数,同配置同结果):缺必填字段/白名单主机非法/标识
    冲突的域剔除并告警,其余正常注册;不抛异常(fail-closed 不裸崩)。"""
    domains: dict[str, DomainSpec] = {}
    warnings: list[str] = []
    for entry in cast("list[object]", raw):
        entryd = cast("dict[str, object] | None", entry if isinstance(entry, dict) else None)
        if entryd is None:
            warnings.append("toolgateway: 清单条目非对象,已剔除")
            continue
        did = str(entryd.get("id") or "")
        endpoint = str(entryd.get("endpoint") or "")
        hosts_raw = entryd.get("whitelist_hosts")
        hosts: tuple[str, ...] = ()
        if isinstance(hosts_raw, list):
            host_list = cast("list[object]", hosts_raw)
            # 白名单主机非法条目剔除(spec:非字符串/空/含路径或通配不构成精确主机)
            hosts = tuple(
                h
                for h in (x.strip().lower() for x in host_list if isinstance(x, str))
                if h and "/" not in h and "*" not in h
            )
        timeout = entryd.get("timeout_ms")
        quota = entryd.get("quota_per_minute")
        redaction = entryd.get("redaction")
        problems: list[str] = []
        if not did:
            problems.append("缺域标识")
        if not endpoint:
            problems.append("缺端点")
        if not hosts:
            problems.append("缺出口白名单")
        if not _endpoint_host(endpoint):
            problems.append("端点主机非法")
        # bool 是 int 子类:true 穿透校验会得到 1ms 超时/1 次配额——显式排除
        if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
            problems.append("超时非法")
        if not isinstance(quota, int) or isinstance(quota, bool) or quota <= 0:
            problems.append("配额非法")
        if did in domains:
            problems.append("标识冲突(后者剔除)")
        if problems:
            warnings.append(f"toolgateway: 域 {did or '(无名)'} 剔除——{'、'.join(problems)}")
            continue
        domains[did] = DomainSpec(
            id=did,
            endpoint=endpoint,
            whitelist_hosts=hosts,
            timeout_ms=timeout if isinstance(timeout, int) else 0,
            quota_per_minute=quota if isinstance(quota, int) else 0,
            redaction=str(redaction) if isinstance(redaction, str) else "allowlist",
        )
    return domains, warnings
